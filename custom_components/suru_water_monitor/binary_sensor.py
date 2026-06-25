"""Binary sensor platform for SURU WaterMonitor (pipe-monitor alarm)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SuruCoordinator
from .entity import SuruEntity

_ACTIVE_STATES = {"OPEN", "CONFIRMED"}
_INCIDENT_KEYS = ("highFlow", "continuousWaterFlow", "temperature", "lowFlow")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm binary sensor for every unit."""
    coordinator: SuruCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SuruAlarmBinarySensor(coordinator, unit_id) for unit_id in coordinator.data
    )


class SuruAlarmBinarySensor(SuruEntity, BinarySensorEntity):
    """ON when the pipe monitor reports an active alarm/incident."""

    _attr_translation_key = "alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SuruCoordinator, unit_id: str) -> None:
        super().__init__(coordinator, unit_id)
        self._attr_unique_id = f"{unit_id}_alarm"

    def _active_incidents(self) -> list[str]:
        incidents = (self.unit_data.get("incidents") or {}).get("incidents") or {}
        return [
            key
            for key in _INCIDENT_KEYS
            if isinstance(incidents.get(key), dict)
            and incidents[key].get("state") in _ACTIVE_STATES
        ]

    @property
    def is_on(self) -> bool | None:
        dashboard = self.unit_data.get("incidents")
        if not dashboard:
            return None
        if (dashboard.get("basicAlarm") or {}).get("active"):
            return True
        return bool(self._active_incidents())

    @property
    def extra_state_attributes(self) -> dict[str, list[str]] | None:
        if not self.unit_data.get("incidents"):
            return None
        return {"active_incidents": self._active_incidents()}
