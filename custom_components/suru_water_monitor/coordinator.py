"""Data update coordinator for SURU WaterMonitor."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SuruApiError, SuruAuthError, SuruWaterApi
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SuruCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Fetch unit detail, meter reading and today's consumption per unit."""

    def __init__(self, hass: HomeAssistant, api: SuruWaterApi) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            units = await self.api.async_get_units()
            data: dict[str, dict] = {}
            for unit in units:
                unit_id = unit.get("id")
                if not unit_id:
                    continue
                detail = await self.api.async_get_unit(unit_id)
                reading = await self.api.async_get_reading(unit_id)
                consumption = await self.api.async_get_consumption(unit_id, "day")
                values = [v for v in (consumption.get("values") or []) if v is not None]

                devices = detail.get("devices") or []
                device_id = devices[0].get("id") if devices else None
                try:
                    incidents = await self.api.async_get_incidents(unit_id, device_id)
                except SuruApiError:
                    incidents = None  # alarm sensor degrades gracefully

                data[unit_id] = {
                    "detail": detail,
                    "reading": reading,
                    "consumption_today_l": float(sum(values)) if values else None,
                    "incidents": incidents,
                }
            return data
        except SuruAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SuruApiError as err:
            raise UpdateFailed(str(err)) from err
