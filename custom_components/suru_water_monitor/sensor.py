"""Sensor platform for SURU WaterMonitor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SuruCoordinator
from .entity import SuruEntity


@dataclass(frozen=True, kw_only=True)
class SuruSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[dict[str, Any]], Any]


def _reading(d: dict) -> float | None:
    return (d.get("reading") or {}).get("reading")


def _measured_at(d: dict):
    ts = (d.get("reading") or {}).get("measuredAt")
    return dt_util.parse_datetime(ts) if ts else None


def _water_props(d: dict) -> dict:
    return (d.get("detail") or {}).get("waterProperties") or {}


SENSORS: tuple[SuruSensorDescription, ...] = (
    SuruSensorDescription(
        key="meter_reading",
        translation_key="meter_reading",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=_reading,
    ),
    SuruSensorDescription(
        key="consumption_today",
        translation_key="consumption_today",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water",
        value_fn=lambda d: d.get("consumption_today_l"),
    ),
    SuruSensorDescription(
        key="water_hardness",
        translation_key="water_hardness",
        native_unit_of_measurement="°dH",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:water-percent",
        value_fn=lambda d: _water_props(d).get("waterHardness"),
    ),
    SuruSensorDescription(
        key="ph_value",
        translation_key="ph_value",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        icon="mdi:ph",
        value_fn=lambda d: _water_props(d).get("phValue"),
    ),
    SuruSensorDescription(
        key="last_measured",
        translation_key="last_measured",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_measured_at,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SURU sensors for every unit on the account."""
    coordinator: SuruCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SuruSensor(coordinator, unit_id, description)
        for unit_id in coordinator.data
        for description in SENSORS
    )


class SuruSensor(SuruEntity, SensorEntity):
    """A single SURU WaterMonitor sensor."""

    entity_description: SuruSensorDescription

    def __init__(
        self,
        coordinator: SuruCoordinator,
        unit_id: str,
        description: SuruSensorDescription,
    ) -> None:
        super().__init__(coordinator, unit_id)
        self.entity_description = description
        self._attr_unique_id = f"{unit_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.unit_data)
