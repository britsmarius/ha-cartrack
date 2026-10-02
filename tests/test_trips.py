"""Tests for the trips client and websocket command."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.cartrack.api import CartrackTrip

from .conftest import STATUS_URL

SAST = ZoneInfo("Africa/Johannesburg")
TRIPS_URL = "https://fleetapi-za.cartrack.com/rest/trips/ABC123GP"

TRIP = {
    "trip_id": "t1",
    "registration": "ABC123GP",
    "start_timestamp": "2026-10-02 07:30:00",
    "end_timestamp": "2026-10-02 07:44:00",
    "start_location": "Bosduif Cres",
    "end_location": "N1",
    "start_coordinates": {"latitude": -25.85, "longitude": 28.15},
    "end_coordinates": {"latitude": -25.8, "longitude": 28.2},
    "start_odometer": 150000000,
    "end_odometer": 150012000,
    "trip_distance": 12.0,
    "max_speed": 98,
    "driver_name": "Marius",
    "driver_surname": "",
}


def test_trip_parsing_uses_local_time() -> None:
    trip = CartrackTrip.from_api(TRIP, SAST)
    assert trip.start == datetime(2026, 10, 2, 5, 30, tzinfo=UTC)
    assert trip.end == datetime(2026, 10, 2, 5, 44, tzinfo=UTC)
    assert trip.distance_km == 12.0
    assert trip.duration_seconds == 14 * 60
    assert trip.start_coordinates == (-25.85, 28.15)
    assert trip.driver == "Marius"
    assert trip.as_dict()["start"] == "2026-10-02T07:30:00+02:00"


def test_trip_distance_fallbacks() -> None:
    no_distance = {**TRIP, "trip_distance": None}
    assert CartrackTrip.from_api(no_distance, SAST).distance_km == 12.0
    metres = {**TRIP, "trip_distance": 12000}
    assert CartrackTrip.from_api(metres, SAST).distance_km == 12.0
    # Real business-account trip: 800 m in 4 min 16 s.
    short = {
        **TRIP,
        "start_timestamp": "2026-10-01 10:25:16+02",
        "end_timestamp": "2026-10-01 10:29:32+02",
        "trip_distance": 800,
    }
    assert CartrackTrip.from_api(short, SAST).distance_km == 0.8
    # Real personal-account trip: 23.4 km in 36 min stays in km.
    long = {
        **TRIP,
        "start_timestamp": "2026-10-02 06:09:08+02",
        "end_timestamp": "2026-10-02 06:45:06+02",
        "trip_distance": 23.4,
    }
    assert CartrackTrip.from_api(long, SAST).distance_km == 23.4
    offset = {**TRIP, "start_timestamp": "2026-10-02T05:30:00Z"}
    assert CartrackTrip.from_api(offset, SAST).start == datetime(
        2026, 10, 2, 5, 30, tzinfo=UTC
    )


async def _setup(hass: HomeAssistant, config_entry) -> None:
    await hass.config.async_set_time_zone("Africa/Johannesburg")
    assert await async_setup_component(hass, "websocket_api", {})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_ws_trips_paginates_in_local_time(
    hass: HomeAssistant,
    hass_ws_client,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    late = {**TRIP, "trip_id": "t2", "start_timestamp": "2026-10-02 17:00:00"}
    aioclient_mock.get(
        TRIPS_URL,
        params={"page": "1"},
        json={"data": [late], "meta": {"current_page": 1, "last_page": 2}},
    )
    aioclient_mock.get(
        TRIPS_URL,
        params={"page": "2"},
        json={"data": [TRIP], "meta": {"current_page": 2, "last_page": 2}},
    )
    await _setup(hass, config_entry)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "cartrack/trips",
            "entity_id": "device_tracker.abc123gp",
            "date": "2026-10-02",
        }
    )
    msg = await client.receive_json()
    assert msg["success"], msg
    trips = msg["result"]["trips"]
    assert [trip["trip_id"] for trip in trips] == ["t1", "t2"]  # sorted by start
    assert trips[0]["start"] == "2026-10-02T07:30:00+02:00"

    trip_calls = [
        call for call in aioclient_mock.mock_calls if "/trips/" in str(call[1])
    ]
    assert len(trip_calls) == 2
    query = trip_calls[0][1].query
    assert query["start_timestamp"] == "2026-10-02 00:00:00"
    assert query["end_timestamp"] == "2026-10-02 23:59:59"


async def test_ws_trips_forbidden_and_unknown_entity(
    hass: HomeAssistant,
    hass_ws_client,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    aioclient_mock.get(TRIPS_URL, status=403)
    await _setup(hass, config_entry)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "cartrack/trips",
            "entity_id": "device_tracker.abc123gp",
            "date": "2026-10-02",
        }
    )
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "forbidden"

    await client.send_json_auto_id(
        {
            "type": "cartrack/trips",
            "entity_id": "device_tracker.somebody_else",
            "date": "2026-10-02",
        }
    )
    msg = await client.receive_json()
    assert msg["error"]["code"] == "not_found"

    await client.send_json_auto_id(
        {
            "type": "cartrack/trips",
            "entity_id": "device_tracker.abc123gp",
            "date": "2 Oct",
        }
    )
    msg = await client.receive_json()
    assert msg["error"]["code"] == "invalid_format"


async def test_tracker_exposes_odometer_for_history(
    hass: HomeAssistant,
    config_entry,
    aioclient_mock: AiohttpClientMocker,
    parked_payload,
) -> None:
    aioclient_mock.get(STATUS_URL, json=parked_payload)
    await _setup(hass, config_entry)
    state = hass.states.get("device_tracker.abc123gp")
    assert state.attributes["odometer"] == 96181760
    assert datetime.fromisoformat(state.attributes["last_update"]) == datetime(
        2026, 10, 1, 19, 0, 21, tzinfo=UTC
    )
