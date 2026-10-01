"""Device tracker for Cartrack vehicles."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CartrackConfigEntry, CartrackCoordinator
from .entity import CartrackEntity, async_add_vehicle_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CartrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a tracker for every vehicle."""
    async_add_vehicle_entities(
        entry,
        async_add_entities,
        lambda coordinator, key: [CartrackTracker(coordinator, key)],
    )


class CartrackTracker(CartrackEntity, TrackerEntity):
    """Where a vehicle is."""

    _attr_name = None  # the device name (registration) is the entity name
    _attr_translation_key = "vehicle"

    def __init__(self, coordinator: CartrackCoordinator, vehicle_key: str) -> None:
        super().__init__(coordinator, vehicle_key, "tracker")

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self.vehicle.latitude if self.vehicle else None

    @property
    def longitude(self) -> float | None:
        return self.vehicle.longitude if self.vehicle else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        vehicle = self.vehicle
        if vehicle is None:
            return {}
        return {
            "registration": vehicle.registration,
            "address": vehicle.address,
            "speed": vehicle.speed,
            "heading": vehicle.heading,
            "ignition": vehicle.ignition,
            "last_update": vehicle.updated.isoformat() if vehicle.updated else None,
        }
