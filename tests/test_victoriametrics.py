"""Tests for reading routes from VictoriaMetrics."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.cartrack.const import (
    CONF_SCAN_INTERVAL_MOVING,
    CONF_SCAN_INTERVAL_PARKED,
    CONF_STALE_TIMEOUT,
    CONF_VM_URL,
)
from custom_components.cartrack.victoriametrics import merge_export

from .conftest import STATUS_URL

VM = "http://a0d7b954-victoriametrics:8428"


def _series(name: str, values: list[float], timestamps: list[int], **tags) -> str:
    return json.dumps(
        {
            "metric": {
                "__name__": name,
                "domain": "device_tracker",
                "entity_id": "abc123gp",
                **tags,
            },
            "values": values,
            "timestamps": timestamps,
        }
    )


EXPORT = "\n".join(
    [
        _series(
            "state_latitude", [-25.85, -25.85, -25.84, -25.83], [1000, 2000, 3000, 4000]
        ),
        _series(
            "state_longitude", [28.15, 28.15, 28.16, 28.17], [1000, 2000, 3000, 4000]
        ),
        _series("state_odometer", [100, 100, 900, 1800], [1000, 2000, 3000, 4000]),
        _series("state_speed", [0, 0, 40, 50], [1000, 2000, 3000, 4000]),
        # A friendly_name change starts a new series; it must merge in.
        _series("state_latitude", [-25.82], [5000], friendly_name="Ranger"),
        _series("state_longitude", [28.18], [5000], friendly_name="Ranger"),
        _series("state_gps_accuracy", [0], [5000]),
        "not json",
    ]
)


def test_merge_export_joins_and_dedupes() -> None:
    rows = merge_export(EXPORT)
    assert [row[0] for row in rows] == [1000, 3000, 4000, 5000]
    # The parked duplicate keeps the newest odometer/speed.
    assert rows[0] == [1000, -25.85, 28.15, 0.0, 100.0]
    assert rows[1] == [3000, -25.84, 28.16, 40.0, 900.0]
    assert rows[3] == [5000, -25.82, 28.18, None, None]


async def _setup(hass: HomeAssistant, config_entry, options=None) -> None:
    await hass.config.async_set_time_zone("Africa/Johannesburg")
    assert await async_setup_component(hass, "websocket_api", {})
    if options is not None:
        hass.config_entries.async_update_entry(config_entry, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_ws_route_reads_victoriametrics(
    hass: HomeAssistant,
    hass_ws_client,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    aioclient_mock.get(f"{VM}/api/v1/export", text=EXPORT)
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_VM_URL: VM})
    await hass.config.async_set_time_zone("Africa/Johannesburg")
    assert await async_setup_component(hass, "websocket_api", {})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "cartrack/route",
            "entity_id": "device_tracker.abc123gp",
            "date": "2026-09-30",
        }
    )
    msg = await client.receive_json()
    assert msg["success"], msg
    assert msg["result"]["source"] == "victoriametrics"
    assert len(msg["result"]["points"]) == 4

    call = next(c for c in aioclient_mock.mock_calls if "/api/v1/export" in str(c[1]))
    query = call[1].query
    assert 'entity_id="abc123gp"' in query["match[]"]
    assert 'domain="device_tracker"' in query["match[]"]
    # 2026-09-30 00:00 SAST .. 2026-10-01 00:00 SAST
    assert query["start"] == "1790719200"
    assert query["end"] == "1790805600"


async def test_ws_route_not_configured_and_errors(
    hass: HomeAssistant,
    hass_ws_client,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    config_entry.add_to_hass(hass)
    await hass.config.async_set_time_zone("Africa/Johannesburg")
    assert await async_setup_component(hass, "websocket_api", {})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    request = {
        "type": "cartrack/route",
        "entity_id": "device_tracker.abc123gp",
        "date": "2026-09-30",
    }
    await client.send_json_auto_id(request)
    msg = await client.receive_json()
    assert msg["error"]["code"] == "not_configured"

    hass.config_entries.async_update_entry(config_entry, options={CONF_VM_URL: VM})
    await hass.async_block_till_done()
    aioclient_mock.get(f"{VM}/api/v1/export", status=500, text="boom")
    await client.send_json_auto_id(request)
    msg = await client.receive_json()
    assert msg["error"]["code"] == "victoriametrics_error"


async def test_options_flow_checks_victoriametrics(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    base = {
        CONF_SCAN_INTERVAL_PARKED: 60,
        CONF_SCAN_INTERVAL_MOVING: 10,
        CONF_STALE_TIMEOUT: 180,
    }
    aioclient_mock.get("http://wrong:8428/health", status=404)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, CONF_VM_URL: "http://wrong:8428"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_VM_URL: "vm_cannot_connect"}

    aioclient_mock.get(f"{VM}/health", text="OK")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, CONF_VM_URL: f"{VM}/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_VM_URL] == VM
    assert config_entry.options[CONF_SCAN_INTERVAL_PARKED] == 60
