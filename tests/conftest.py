"""Shared fixtures for Cartrack tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cartrack.const import CONF_REGION, DOMAIN

STATUS_URL = "https://fleetapi-za.cartrack.com/rest/vehicles/status"

PARKED_CAR: dict[str, Any] = {
    "vehicle_id": 1001,
    "registration": "ABC123GP",
    "speed": 0,
    "bearing": 253,
    "altitude": 1453,
    "ignition": False,
    "odometer": 96181760,
    "tcu_percentage": 100,
    "vext": 12.44,
    "location": {
        "latitude": -25.84796,
        "longitude": 28.148655,
        "position_description": "Example St, Centurion, Gauteng, South Africa",
        "updated": "2026-10-01 21:00:21+02",
    },
}

MOVING_CAR: dict[str, Any] = {
    "vehicle_id": 1002,
    "registration": "XYZ789GP",
    "speed": 62.5,
    "bearing": 90,
    "ignition": "true",
    "odometer": 1160400,
    "vext": 14.1,
    "location": {
        "latitude": -25.80,
        "longitude": 28.20,
        "position_description": "N1, Pretoria",
        "updated": 0,  # replaced per test with a fresh epoch
    },
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components/."""


@pytest.fixture
def parked_payload() -> dict[str, Any]:
    return {"data": [deepcopy(PARKED_CAR)]}


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Personal",
        unique_id="za_demo",
        data={CONF_USERNAME: "demo", CONF_PASSWORD: "secret", CONF_REGION: "za"},
    )
