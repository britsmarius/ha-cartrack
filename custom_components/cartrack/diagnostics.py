"""Diagnostics for Cartrack.

The raw API payload is included (with location and identifiers redacted)
so field-name differences between Cartrack accounts can be diagnosed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import CartrackConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lng",
    "position_description",
    "registration",
    "vin",
    "chassis_number",
    "engine_number",
    "driver",
    "driver_name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CartrackConfigEntry
) -> dict[str, Any]:
    """Return redacted config and the latest raw payload per vehicle."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        "vehicles": [
            async_redact_data(vehicle.raw, TO_REDACT)
            for vehicle in (coordinator.data or {}).values()
        ],
    }
