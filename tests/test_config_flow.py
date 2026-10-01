"""Tests for the config and options flows."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.cartrack.const import (
    CONF_REGION,
    CONF_SCAN_INTERVAL_MOVING,
    CONF_SCAN_INTERVAL_PARKED,
    CONF_STALE_TIMEOUT,
    DOMAIN,
)

from .conftest import STATUS_URL

USER_INPUT = {
    CONF_USERNAME: "demo",
    CONF_PASSWORD: "secret",
    CONF_REGION: "za",
    "name": "Personal",
}


async def test_user_flow_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, parked_payload
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Personal"
    assert result["data"] == {
        CONF_USERNAME: "demo",
        CONF_PASSWORD: "secret",
        CONF_REGION: "za",
    }
    assert result["result"].unique_id == "za_demo"


async def test_user_flow_invalid_auth_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, parked_payload
) -> None:
    aioclient_mock.get(STATUS_URL, status=401)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    for status, error, payload in (
        (429, "rate_limited", None),
        (500, "cannot_connect", None),
        (200, "no_vehicles", {"data": []}),
    ):
        aioclient_mock.clear_requests()
        if payload is None:
            aioclient_mock.get(STATUS_URL, status=status)
        else:
            aioclient_mock.get(STATUS_URL, json=payload)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["errors"] == {"base": error}, status
        hass.config_entries.flow.async_abort(result["flow_id"])


async def test_duplicate_account_aborts(
    hass: HomeAssistant, config_entry, aioclient_mock: AiohttpClientMocker
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_USERNAME: "DEMO"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL_PARKED: 120,
            CONF_SCAN_INTERVAL_MOVING: 15,
            CONF_STALE_TIMEOUT: 300,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    assert coordinator.parked_interval.total_seconds() == 120
    assert coordinator.moving_interval.total_seconds() == 15
    assert coordinator.stale_timeout == 300


async def test_reauth_flow(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-secret"
