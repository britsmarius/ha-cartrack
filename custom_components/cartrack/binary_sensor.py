"""Binary sensors for Cartrack vehicles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import CartrackConfigEntry, CartrackCoordinator
from .entity import CartrackEntity, async_add_vehicle_entities


@dataclass(frozen=True, kw_only=True)
class CartrackBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value getter."""

    value_fn: Callable[[CartrackCoordinator, str], bool | None]


def _ignition(coordinator: CartrackCoordinator, key: str) -> bool | None:
    vehicle = coordinator.data.get(key)
    return vehicle.ignition if vehicle else None


def _moving(coordinator: CartrackCoordinator, key: str) -> bool | None:
    vehicle = coordinator.data.get(key)
    if vehicle is None:
        return None
    return vehicle.is_moving(dt_util.utcnow(), coordinator.stale_timeout)


BINARY_SENSORS: tuple[CartrackBinarySensorDescription, ...] = (
    CartrackBinarySensorDescription(
        key="ignition",
        translation_key="ignition",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=_ignition,
    ),
    CartrackBinarySensorDescription(
        key="moving",
        translation_key="moving",
        device_class=BinarySensorDeviceClass.MOVING,
        value_fn=_moving,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CartrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors for every vehicle."""
    async_add_vehicle_entities(
        entry,
        async_add_entities,
        lambda coordinator, key: [
            CartrackBinarySensor(coordinator, key, description)
            for description in BINARY_SENSORS
        ],
    )


class CartrackBinarySensor(CartrackEntity, BinarySensorEntity):
    """An on/off vehicle state."""

    entity_description: CartrackBinarySensorDescription

    def __init__(
        self,
        coordinator: CartrackCoordinator,
        vehicle_key: str,
        description: CartrackBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, vehicle_key, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator, self._vehicle_key)
