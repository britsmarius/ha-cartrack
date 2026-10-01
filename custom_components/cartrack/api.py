"""Minimal async client for the Cartrack Fleet API.

Only the read-only vehicle status endpoint is used. The response shape is
parsed defensively, because Cartrack nests some fields (location) and the
field names differ slightly between accounts and API versions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .const import DEFAULT_RATE_LIMIT_BACKOFF, KAROOOOO_REGIONS, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

TRUTHY = frozenset({"true", "1", "on", "yes"})


class CartrackError(Exception):
    """Base error for the Cartrack client."""


class CartrackAuthError(CartrackError):
    """Credentials were rejected (HTTP 401/403)."""


class CartrackConnectionError(CartrackError):
    """Network problem or unexpected response."""


class CartrackRateLimitError(CartrackError):
    """Cartrack returned HTTP 429."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def base_url(region: str) -> str:
    """Return the Fleet API base URL for a region code."""
    region = region.strip().lower()
    domain = "karooooo.com" if region in KAROOOOO_REGIONS else "cartrack.com"
    return f"https://fleetapi-{region}.{domain}/rest"


def _first(*values: Any) -> Any:
    """Return the first value that is not None or an empty string."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUTHY


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an epoch or ISO-like timestamp into an aware datetime."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        if number > 1e12:  # milliseconds
            number /= 1000
        return datetime.fromtimestamp(number, tz=UTC)

    clean = text.replace("Z", "+00:00").replace(" ", "T", 1)
    # Cartrack sometimes sends a bare hour offset such as "+02".
    clean = re.sub(r"([+-]\d{2})$", r"\1:00", clean)
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(slots=True)
class CartrackVehicle:
    """One vehicle's latest status, normalised from the API payload."""

    key: str
    registration: str
    latitude: float | None
    longitude: float | None
    address: str | None
    speed: float | None  # km/h
    heading: float | None  # degrees
    altitude: float | None  # metres
    ignition: bool | None
    odometer: float | None  # metres
    supply_voltage: float | None  # volts (vehicle battery / external power)
    tracker_battery: float | None  # percent
    updated: datetime | None
    raw: dict[str, Any]

    @property
    def has_position(self) -> bool:
        """True when the payload carried coordinates."""
        return self.latitude is not None and self.longitude is not None

    @property
    def reports_movement(self) -> bool:
        """Ignition on or a non-zero speed, regardless of data age."""
        return bool(self.ignition) or (self.speed or 0) > 0

    def is_moving(self, now: datetime, stale_after: float) -> bool:
        """Moving, with telemetry fresh enough to believe it.

        A car that drove into an underground parking keeps reporting its last
        moving state; once the data is older than ``stale_after`` seconds it
        is treated as parked.
        """
        if not self.reports_movement:
            return False
        if self.updated is None:
            return True
        return (now - self.updated).total_seconds() <= stale_after

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> CartrackVehicle | None:
        """Build from one element of the /vehicles/status response."""
        location = data.get("location")
        if not isinstance(location, dict):
            location = {}

        registration = _first(data.get("registration"), data.get("vehicle_name"))
        key = _first(
            data.get("vehicle_id"),
            data.get("vehicleId"),
            data.get("id"),
            registration,
        )
        if key is None:
            _LOGGER.debug("Skipping vehicle without an identifier: %s", data)
            return None

        odometer = _to_float(_first(data.get("odometer"), data.get("mileage")))

        return cls(
            key=str(key).strip(),
            registration=str(_first(registration, key)).strip(),
            latitude=_to_float(
                _first(
                    location.get("latitude"),
                    location.get("lat"),
                    data.get("latitude"),
                    data.get("lat"),
                )
            ),
            longitude=_to_float(
                _first(
                    location.get("longitude"),
                    location.get("lon"),
                    location.get("lng"),
                    data.get("longitude"),
                    data.get("lon"),
                    data.get("lng"),
                )
            ),
            address=_first(
                location.get("position_description"),
                data.get("position_description"),
            ),
            speed=_to_float(data.get("speed")),
            heading=_to_float(
                _first(data.get("bearing"), data.get("heading"), data.get("direction"))
            ),
            altitude=_to_float(data.get("altitude")),
            ignition=_to_bool(data.get("ignition")),
            odometer=odometer,
            supply_voltage=_to_float(data.get("vext")),
            tracker_battery=_to_float(
                _first(data.get("tcu_percentage"), data.get("tcu_battery_percentage"))
            ),
            updated=parse_timestamp(
                _first(
                    location.get("updated"),
                    data.get("event_ts"),
                    data.get("timestamp"),
                    data.get("lastUpdated"),
                    data.get("gpsTimestamp"),
                    data.get("last_updated"),
                )
            ),
            raw=data,
        )


class CartrackClient:
    """Read-only client for one Cartrack account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        region: str,
    ) -> None:
        self._session = session
        self._auth = aiohttp.BasicAuth(username, password)
        self._base_url = base_url(region)

    async def async_get_vehicle_status(self) -> list[CartrackVehicle]:
        """Return the latest status of every vehicle on the account."""
        payload = await self._get("/vehicles/status")
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("data") or payload.get("vehicles") or []
        else:
            items = []

        vehicles: list[CartrackVehicle] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            vehicle = CartrackVehicle.from_api(item)
            if vehicle is not None:
                vehicles.append(vehicle)
        return vehicles

    async def _get(self, path: str) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(
                url,
                auth=self._auth,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT.total_seconds()),
            ) as resp:
                if resp.status in (401, 403):
                    raise CartrackAuthError(f"HTTP {resp.status} from Cartrack")
                if resp.status == 429:
                    raise CartrackRateLimitError(_retry_after(resp.headers))
                if resp.status != 200:
                    text = await resp.text()
                    raise CartrackConnectionError(
                        f"HTTP {resp.status} from Cartrack: {text[:200]}"
                    )
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CartrackConnectionError(str(err) or type(err).__name__) from err
        except ValueError as err:  # invalid JSON
            raise CartrackConnectionError("Invalid JSON from Cartrack") from err


def _retry_after(headers: Any) -> int:
    for name in ("X-RateLimit-Retry-After-Seconds", "Retry-After"):
        value = headers.get(name)
        if value is None:
            continue
        try:
            return max(1, int(float(value)))
        except (TypeError, ValueError):
            continue
    return DEFAULT_RATE_LIMIT_BACKOFF
