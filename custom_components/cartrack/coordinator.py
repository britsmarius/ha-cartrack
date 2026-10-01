"""Polling coordinator for one Cartrack account."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CartrackAuthError,
    CartrackClient,
    CartrackConnectionError,
    CartrackRateLimitError,
    CartrackVehicle,
)
from .const import (
    CONF_SCAN_INTERVAL_MOVING,
    CONF_SCAN_INTERVAL_PARKED,
    CONF_STALE_TIMEOUT,
    DEFAULT_SCAN_INTERVAL_MOVING,
    DEFAULT_SCAN_INTERVAL_PARKED,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

type CartrackConfigEntry = ConfigEntry[CartrackCoordinator]


class CartrackCoordinator(DataUpdateCoordinator[dict[str, CartrackVehicle]]):
    """Fetch all vehicles for an account, polling faster while any is moving."""

    config_entry: CartrackConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CartrackConfigEntry,
        client: CartrackClient,
    ) -> None:
        self.client = client
        self.parked_interval = timedelta(
            seconds=entry.options.get(
                CONF_SCAN_INTERVAL_PARKED, DEFAULT_SCAN_INTERVAL_PARKED
            )
        )
        self.moving_interval = timedelta(
            seconds=entry.options.get(
                CONF_SCAN_INTERVAL_MOVING, DEFAULT_SCAN_INTERVAL_MOVING
            )
        )
        self.stale_timeout = float(
            entry.options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=self.parked_interval,
        )

    async def _async_update_data(self) -> dict[str, CartrackVehicle]:
        try:
            vehicles = await self.client.async_get_vehicle_status()
        except CartrackAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CartrackRateLimitError as err:
            # Wait out the limit, then go back to the normal cadence.
            self.update_interval = max(
                timedelta(seconds=err.retry_after), self.parked_interval
            )
            raise UpdateFailed(
                f"Cartrack rate limit hit, retrying in {err.retry_after}s"
            ) from err
        except CartrackConnectionError as err:
            raise UpdateFailed(str(err)) from err

        now = dt_util.utcnow()
        moving = [
            v.registration for v in vehicles if v.is_moving(now, self.stale_timeout)
        ]
        new_interval = self.moving_interval if moving else self.parked_interval
        if new_interval != self.update_interval:
            _LOGGER.debug(
                "%s: %s, polling every %ss",
                self.config_entry.title,
                f"moving: {', '.join(moving)}" if moving else "all vehicles parked",
                int(new_interval.total_seconds()),
            )
            self.update_interval = new_interval

        return {vehicle.key: vehicle for vehicle in vehicles}
