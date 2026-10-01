"""The Cartrack integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CartrackClient
from .const import CONF_REGION
from .coordinator import CartrackConfigEntry, CartrackCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: CartrackConfigEntry) -> bool:
    """Set up a Cartrack account from a config entry."""
    client = CartrackClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_REGION],
    )
    coordinator = CartrackCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CartrackConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_options(
    hass: HomeAssistant, entry: CartrackConfigEntry
) -> None:
    """Apply changed polling options."""
    await hass.config_entries.async_reload(entry.entry_id)
