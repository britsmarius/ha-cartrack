"""Sensors for Cartrack vehicles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CartrackVehicle
from .coordinator import CartrackConfigEntry, CartrackCoordinator
from .entity import CartrackEntity, async_add_vehicle_entities


@dataclass(frozen=True, kw_only=True)
class CartrackSensorDescription(SensorEntityDescription):
    """Sensor description with a value getter."""

    value_fn: Callable[[CartrackVehicle], float | str | datetime | None]


SENSORS: tuple[CartrackSensorDescription, ...] = (
    CartrackSensorDescription(
        key="speed",
        translation_key="speed",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_display_precision=0,
        value_fn=lambda v: v.speed,
    ),
    CartrackSensorDescription(
        key="address",
        translation_key="address",
        value_fn=lambda v: v.address,
    ),
    CartrackSensorDescription(
        key="supply_voltage",
        translation_key="supply_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        value_fn=lambda v: v.supply_voltage,
    ),
    CartrackSensorDescription(
        key="odometer",
        translation_key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=0,
        value_fn=lambda v: v.odometer,
    ),
    CartrackSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda v: v.updated,
    ),
    CartrackSensorDescription(
        key="tracker_battery",
        translation_key="tracker_battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda v: v.tracker_battery,
    ),
    CartrackSensorDescription(
        key="heading",
        translation_key="heading",
        native_unit_of_measurement=DEGREE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda v: v.heading,
    ),
    CartrackSensorDescription(
        key="altitude",
        translation_key="altitude",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.METERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda v: v.altitude,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CartrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for every vehicle."""
    async_add_vehicle_entities(
        entry,
        async_add_entities,
        lambda coordinator, key: [
            CartrackSensor(coordinator, key, description) for description in SENSORS
        ],
    )


class CartrackSensor(CartrackEntity, SensorEntity):
    """A single vehicle reading."""

    entity_description: CartrackSensorDescription

    def __init__(
        self,
        coordinator: CartrackCoordinator,
        vehicle_key: str,
        description: CartrackSensorDescription,
    ) -> None:
        super().__init__(coordinator, vehicle_key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | datetime | None:
        vehicle = self.vehicle
        return self.entity_description.value_fn(vehicle) if vehicle else None
