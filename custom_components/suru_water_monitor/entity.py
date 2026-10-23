"""Shared base entity for SURU WaterMonitor."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SuruCoordinator, unit_name


class SuruEntity(CoordinatorEntity[SuruCoordinator]):
    """Base entity tying each entity to one water unit (= one device)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SuruCoordinator, unit_id: str) -> None:
        super().__init__(coordinator)
        self._unit_id = unit_id

        detail = (coordinator.data.get(unit_id) or {}).get("detail") or {}
        devices = detail.get("devices") or [{}]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unit_id)},
            manufacturer="SURU / SenseGuard",
            name=unit_name(detail),
            model=(devices[0] or {}).get("meterType"),
            serial_number=detail.get("serialNumber"),
        )

    @property
    def unit_data(self) -> dict[str, Any]:
        """Coordinator data for this unit."""
        return self.coordinator.data.get(self._unit_id) or {}

    @property
    def available(self) -> bool:
        return super().available and self._unit_id in (self.coordinator.data or {})
