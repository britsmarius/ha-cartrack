"""Tests for payload parsing."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.cartrack.api import CartrackVehicle, base_url, parse_timestamp

from .conftest import PARKED_CAR


def test_base_url() -> None:
    assert base_url("ZA") == "https://fleetapi-za.cartrack.com/rest"
    assert base_url("ke") == "https://fleetapi-ke.karooooo.com/rest"


def test_parse_timestamp_formats() -> None:
    expected = datetime(2026, 10, 1, 19, 0, 21, tzinfo=UTC)
    assert parse_timestamp("2026-10-01 21:00:21+02") == expected
    assert parse_timestamp("2026-10-01T19:00:21Z") == expected
    assert parse_timestamp(expected.timestamp()) == expected
    assert parse_timestamp(expected.timestamp() * 1000) == expected
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(None) is None


def test_vehicle_from_api() -> None:
    vehicle = CartrackVehicle.from_api(PARKED_CAR)
    assert vehicle is not None
    assert vehicle.key == "1001"
    assert vehicle.registration == "ABC123GP"
    assert vehicle.latitude == -25.84796
    assert vehicle.longitude == 28.148655
    assert vehicle.address.startswith("Example St")
    assert vehicle.speed == 0
    assert vehicle.ignition is False
    assert vehicle.odometer == 96181760
    assert vehicle.supply_voltage == 12.44
    assert vehicle.tracker_battery == 100
    assert vehicle.updated == datetime(2026, 10, 1, 19, 0, 21, tzinfo=UTC)
    assert not vehicle.reports_movement


def test_vehicle_flat_payload_and_fallback_key() -> None:
    vehicle = CartrackVehicle.from_api(
        {
            "registration": "FLAT1GP",
            "latitude": "1.5",
            "longitude": "2.5",
            "speed": "10",
        }
    )
    assert vehicle is not None
    assert vehicle.key == "FLAT1GP"
    assert vehicle.has_position
    assert vehicle.speed == 10.0
    assert vehicle.reports_movement


def test_vehicle_without_identifier_is_skipped() -> None:
    assert CartrackVehicle.from_api({"speed": 5}) is None


def test_stale_moving_vehicle_counts_as_parked() -> None:
    vehicle = CartrackVehicle.from_api(
        {"registration": "A", "ignition": True, "location": {"updated": 1000}}
    )
    assert vehicle is not None
    now = datetime.fromtimestamp(1000 + 100, tz=UTC)
    assert vehicle.is_moving(now, stale_after=180)
    later = datetime.fromtimestamp(1000 + 600, tz=UTC)
    assert not vehicle.is_moving(later, stale_after=180)
