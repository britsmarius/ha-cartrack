"""Minimal async client for the Cartrack Fleet API.

Only read-only endpoints are used: vehicle status and trips. The response shape is
parsed defensively, because Cartrack nests some fields (location) and the
field names differ slightly between accounts and API versions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import DEFAULT_RATE_LIMIT_BACKOFF, KAROOOOO_REGIONS, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

TRUTHY = frozenset({"true", "1", "on", "yes"})


class CartrackError(Exception):
    """Base error for the Cartrack client."""


class CartrackAuthError(CartrackError):
    """Credentials were rejected (HTTP 401/403)."""


class CartrackForbiddenError(CartrackAuthError):
    """HTTP 403: the credentials are valid but lack access to this endpoint."""


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


def parse_timestamp(value: Any, naive_tz: tzinfo = UTC) -> datetime | None:
    """Parse an epoch or ISO-like timestamp into an aware datetime.

    Timestamps without an offset are interpreted in ``naive_tz``.
    """
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
        parsed = parsed.replace(tzinfo=naive_tz)
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


TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _coordinates(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lat = _to_float(_first(value.get("latitude"), value.get("lat")))
    lon = _to_float(_first(value.get("longitude"), value.get("lon"), value.get("lng")))
    if lat is None or lon is None:
        return None
    return lat, lon


@dataclass(slots=True)
class CartrackTrip:
    """One trip as reported by Cartrack's /trips endpoint."""

    trip_id: str | None
    start: datetime | None
    end: datetime | None
    start_location: str | None
    end_location: str | None
    start_coordinates: tuple[float, float] | None
    end_coordinates: tuple[float, float] | None
    distance_km: float | None
    duration_seconds: float | None
    max_speed: float | None
    idle_seconds: float | None
    driver: str | None
    raw_start: str | None
    raw_end: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any], local_tz: tzinfo) -> CartrackTrip:
        start = parse_timestamp(data.get("start_timestamp"), local_tz)
        end = parse_timestamp(data.get("end_timestamp"), local_tz)

        distance = _to_float(data.get("trip_distance"))
        start_odo = _to_float(data.get("start_odometer"))
        end_odo = _to_float(data.get("end_odometer"))
        if distance is None and start_odo is not None and end_odo is not None:
            # Odometer readings are in metres.
            distance = (end_odo - start_odo) / 1000
        elif distance is not None and distance > 2000:
            # Some accounts report trip_distance in metres.
            distance /= 1000

        duration = _to_float(data.get("trip_duration_seconds"))
        if duration is None and start and end:
            duration = (end - start).total_seconds()

        driver = " ".join(
            part
            for part in (data.get("driver_name"), data.get("driver_surname"))
            if isinstance(part, str) and part.strip()
        )
        trip_id = data.get("trip_id")
        return cls(
            trip_id=None if trip_id is None else str(trip_id),
            start=start,
            end=end,
            start_location=_first(data.get("start_location")),
            end_location=_first(data.get("end_location")),
            start_coordinates=_coordinates(data.get("start_coordinates")),
            end_coordinates=_coordinates(data.get("end_coordinates")),
            distance_km=None if distance is None else round(distance, 2),
            duration_seconds=duration,
            max_speed=_to_float(data.get("max_speed")),
            idle_seconds=_to_float(data.get("idle_time_seconds")),
            driver=driver or None,
            raw_start=_first(data.get("start_timestamp")),
            raw_end=_first(data.get("end_timestamp")),
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form for the frontend."""
        return {
            "trip_id": self.trip_id,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "start_location": self.start_location,
            "end_location": self.end_location,
            "start_coordinates": self.start_coordinates,
            "end_coordinates": self.end_coordinates,
            "distance_km": self.distance_km,
            "duration_seconds": self.duration_seconds,
            "max_speed": self.max_speed,
            "idle_seconds": self.idle_seconds,
            "driver": self.driver,
            "raw_start": self.raw_start,
            "raw_end": self.raw_end,
        }


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

    async def async_get_trips(
        self,
        registration: str,
        start: datetime,
        end: datetime,
        local_tz: tzinfo,
        max_pages: int = 10,
    ) -> list[CartrackTrip]:
        """Trips for one vehicle between two moments (max 31 days apart).

        Cartrack's timestamps carry no offset; they are sent and read in the
        account's local time zone (``local_tz``).
        """
        params = {
            "start_timestamp": start.astimezone(local_tz).strftime(TS_FORMAT),
            "end_timestamp": end.astimezone(local_tz).strftime(TS_FORMAT),
            "limit": 100,
        }
        path = f"/trips/{quote(registration, safe='')}"
        trips: list[CartrackTrip] = []
        for page in range(1, max_pages + 1):
            payload = await self._get(path, {**params, "page": page})
            items = payload.get("data", []) if isinstance(payload, dict) else payload
            for item in items or []:
                if isinstance(item, dict):
                    trips.append(CartrackTrip.from_api(item, local_tz))
            meta = payload.get("meta") if isinstance(payload, dict) else None
            if not isinstance(meta, dict):
                break
            try:
                if int(meta.get("current_page", page)) >= int(meta.get("last_page", 1)):
                    break
            except (TypeError, ValueError):
                break
        trips.sort(key=lambda trip: trip.start or datetime.max.replace(tzinfo=UTC))
        return trips

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(
                url,
                auth=self._auth,
                params=params,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT.total_seconds()),
            ) as resp:
                if resp.status == 403:
                    raise CartrackForbiddenError(f"HTTP 403 from Cartrack for {path}")
                if resp.status == 401:
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
