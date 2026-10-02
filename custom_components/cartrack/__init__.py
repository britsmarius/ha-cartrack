"""The Cartrack integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import websocket
from .api import CartrackClient
from .const import CARD_FILENAME, CONF_REGION, DOMAIN, FRONTEND_URL_BASE
from .coordinator import CartrackConfigEntry, CartrackCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the trips command and the route card."""
    websocket.async_register(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the route card and load it on every dashboard."""
    if "frontend" not in hass.config.components or hass.http is None:
        return
    # Imported here so tests without the frontend can still load the integration.
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    integration = await async_get_integration(hass, DOMAIN)
    folder = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_BASE, str(folder), False)]
    )
    # The version query makes browsers fetch the new card after an update.
    add_extra_js_url(
        hass, f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v={integration.version}"
    )


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
