"""Base entity for Cartrack vehicles."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CartrackVehicle
from .const import DOMAIN, MANUFACTURER
from .coordinator import CartrackConfigEntry, CartrackCoordinator


@callback
def async_add_vehicle_entities(
    entry: CartrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[CartrackCoordinator, str], Iterable[Entity]],
) -> None:
    """Create entities for every vehicle now, and for vehicles that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [key for key in coordinator.data if key not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(entity for key in new for entity in build(coordinator, key))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class CartrackEntity(CoordinatorEntity[CartrackCoordinator]):
    """One entity belonging to one vehicle."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CartrackCoordinator, vehicle_key: str, key: str
    ) -> None:
        super().__init__(coordinator)
        self._vehicle_key = vehicle_key
        vehicle = coordinator.data[vehicle_key]
        self._attr_unique_id = f"{vehicle_key}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vehicle_key)},
            name=vehicle.registration,
            manufacturer=MANUFACTURER,
            model="Vehicle tracker",
            serial_number=vehicle.registration,
        )

    @property
    def vehicle(self) -> CartrackVehicle | None:
        """Latest data for this vehicle, or None if it left the account."""
        return self.coordinator.data.get(self._vehicle_key)

    @property
    def available(self) -> bool:
        """Unavailable when the vehicle disappears from the response."""
        return super().available and self.vehicle is not None
