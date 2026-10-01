"""Tests for setup, entities and adaptive polling."""

from __future__ import annotations

from copy import deepcopy

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.cartrack.diagnostics import async_get_config_entry_diagnostics

from .conftest import MOVING_CAR, PARKED_CAR, STATUS_URL


async def _setup(hass: HomeAssistant, config_entry) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_entities_created_with_values(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    tracker = hass.states.get("device_tracker.abc123gp")
    assert tracker is not None
    assert tracker.attributes["latitude"] == -25.84796
    assert tracker.attributes["longitude"] == 28.148655
    assert tracker.attributes["address"].startswith("Example St")

    assert float(hass.states.get("sensor.abc123gp_speed").state) == 0
    assert hass.states.get("sensor.abc123gp_battery_voltage").state == "12.44"
    odometer = hass.states.get("sensor.abc123gp_odometer")
    assert odometer.attributes["unit_of_measurement"] == "km"
    assert round(float(odometer.state)) == 96182
    assert hass.states.get("sensor.abc123gp_address").state.startswith("Example St")
    assert hass.states.get("binary_sensor.abc123gp_ignition").state == "off"
    assert hass.states.get("binary_sensor.abc123gp_moving").state == "off"

    registry = er.async_get(hass)
    heading = registry.async_get("sensor.abc123gp_heading")
    assert heading is not None and heading.disabled_by is not None

    coordinator = config_entry.runtime_data
    assert coordinator.update_interval == coordinator.parked_interval


async def test_moving_vehicle_speeds_up_polling(
    hass: HomeAssistant, config_entry, aioclient_mock: AiohttpClientMocker
) -> None:
    moving = deepcopy(MOVING_CAR)
    moving["location"]["updated"] = int(dt_util.utcnow().timestamp())
    aioclient_mock.get(STATUS_URL, json={"data": [deepcopy(PARKED_CAR), moving]})
    await _setup(hass, config_entry)

    coordinator = config_entry.runtime_data
    assert coordinator.update_interval == coordinator.moving_interval
    assert hass.states.get("binary_sensor.xyz789gp_moving").state == "on"
    assert hass.states.get("binary_sensor.xyz789gp_ignition").state == "on"
    assert float(hass.states.get("sensor.xyz789gp_speed").state) == 62.5


async def test_new_vehicle_added_on_refresh(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    await _setup(hass, config_entry)
    assert hass.states.get("device_tracker.xyz789gp") is None

    moving = deepcopy(MOVING_CAR)
    moving["location"]["updated"] = int(dt_util.utcnow().timestamp())
    aioclient_mock.clear_requests()
    aioclient_mock.get(STATUS_URL, json={"data": [deepcopy(PARKED_CAR), moving]})
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.xyz789gp") is not None


async def test_rate_limit_backs_off(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    await _setup(hass, config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        STATUS_URL, status=429, headers={"X-RateLimit-Retry-After-Seconds": "300"}
    )
    coordinator = config_entry.runtime_data
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.update_interval.total_seconds() == 300
    assert hass.states.get("device_tracker.abc123gp").state == "unavailable"


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(STATUS_URL, status=401)
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_diagnostics_redacts(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diag["entry"]["password"] == "**REDACTED**"
    vehicle = diag["vehicles"][0]
    assert vehicle["registration"] == "**REDACTED**"
    assert vehicle["location"]["latitude"] == "**REDACTED**"
    assert vehicle["vext"] == 12.44


async def test_unload(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
