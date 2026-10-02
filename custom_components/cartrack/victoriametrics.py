"""Read vehicle positions back from VictoriaMetrics.

Home Assistant's InfluxDB integration can write every state change to
VictoriaMetrics, which keeps it far longer than the recorder. A device
tracker's numeric attributes arrive there as one series per attribute, named
``<measurement>_<attribute>`` (for example ``state_latitude``) and tagged with
``domain`` and ``entity_id`` (the object id, without ``device_tracker.``).

The measurement prefix depends on the InfluxDB integration's settings, so it is
not assumed: series are matched on the attribute suffix only.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

FIELDS = ("latitude", "longitude", "speed", "odometer")
_SUFFIX = re.compile(r"_(latitude|longitude|speed|odometer)$")
_SAFE_TAG = re.compile(r"^[a-z0-9_]+$")
TIMEOUT = aiohttp.ClientTimeout(total=30)


class VictoriaMetricsError(Exception):
    """VictoriaMetrics could not be reached or answered unexpectedly."""


def _auth(username: str | None, password: str | None) -> aiohttp.BasicAuth | None:
    if username:
        return aiohttp.BasicAuth(username, password or "")
    return None


async def async_check(
    session: aiohttp.ClientSession,
    url: str,
    username: str | None = None,
    password: str | None = None,
) -> None:
    """Raise VictoriaMetricsError unless the server answers its health check."""
    try:
        async with session.get(
            f"{url.rstrip('/')}/health",
            auth=_auth(username, password),
            timeout=TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise VictoriaMetricsError(f"HTTP {resp.status}")
    except (aiohttp.ClientError, TimeoutError) as err:
        raise VictoriaMetricsError(str(err) or type(err).__name__) from err


async def async_get_positions(
    session: aiohttp.ClientSession,
    url: str,
    object_id: str,
    start: datetime,
    end: datetime,
    username: str | None = None,
    password: str | None = None,
) -> list[list[float | None]]:
    """Raw positions for one tracker between two moments.

    Returns ``[timestamp_ms, latitude, longitude, speed, odometer]`` rows,
    oldest first, with consecutive duplicate positions removed.
    """
    if not _SAFE_TAG.match(object_id):
        raise VictoriaMetricsError(f"Unexpected entity id {object_id!r}")
    selector = (
        '{__name__=~".+_(latitude|longitude|speed|odometer)",'
        f'domain="device_tracker",entity_id="{object_id}"}}'
    )
    params = {
        "match[]": selector,
        "start": str(int(start.timestamp())),
        "end": str(int(end.timestamp())),
    }
    try:
        async with session.get(
            f"{url.rstrip('/')}/api/v1/export",
            params=params,
            auth=_auth(username, password),
            timeout=TIMEOUT,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise VictoriaMetricsError(f"HTTP {resp.status}: {text[:200]}")
            body = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise VictoriaMetricsError(str(err) or type(err).__name__) from err
    return merge_export(body)


def merge_export(body: str) -> list[list[float | None]]:
    """Join the per-attribute series of an /api/v1/export reply by timestamp."""
    by_time: dict[int, dict[str, float]] = {}
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            series: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        name = series.get("metric", {}).get("__name__", "")
        match = _SUFFIX.search(name)
        if not match:
            continue
        field = match.group(1)
        for ts, value in zip(
            series.get("timestamps", []), series.get("values", []), strict=False
        ):
            if value is None:
                continue
            by_time.setdefault(int(ts), {})[field] = float(value)

    rows: list[list[float | None]] = []
    for ts in sorted(by_time):
        sample = by_time[ts]
        if "latitude" not in sample or "longitude" not in sample:
            continue
        row = [
            ts,
            sample["latitude"],
            sample["longitude"],
            sample.get("speed"),
            sample.get("odometer"),
        ]
        if rows and rows[-1][1:3] == row[1:3]:
            # Parked: keep the first and the last reading of the stay, so the
            # time the car left is known, but nothing in between.
            if len(rows) >= 2 and rows[-2][1:3] == row[1:3]:
                rows[-1] = row
            else:
                rows.append(row)
            continue
        rows.append(row)
    return rows
