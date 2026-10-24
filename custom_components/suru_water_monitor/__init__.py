"""The SURU WaterMonitor integration."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .api import SuruError, SuruWaterApi
from .const import (
    ATTR_START_DATE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_HISTORY_DAYS,
    SERVICE_IMPORT_HISTORY,
)
from .coordinator import SuruCoordinator, unit_name
from .statistics import async_clear_hourly_statistics, async_rebuild_hourly_consumption

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

IMPORT_HISTORY_SCHEMA = vol.Schema({vol.Required(ATTR_START_DATE): cv.date})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""

    async def _import_history(call: ServiceCall) -> None:
        start: date = call.data[ATTR_START_DATE]
        today = dt_util.now().date()
        if start >= today:
            raise ServiceValidationError("The start date must be in the past")
        if (today - start).days > MAX_HISTORY_DAYS:
            raise ServiceValidationError(
                f"The start date must be at most {MAX_HISTORY_DAYS} days ago"
            )
        coordinators: list[SuruCoordinator] = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            raise ServiceValidationError("SURU WaterMonitor is not set up")
        for coordinator in coordinators:
            for unit_id, unit in (coordinator.data or {}).items():
                hass.async_create_background_task(
                    _async_import_history(
                        hass, coordinator, unit_id,
                        unit_name(unit.get("detail") or {}), start,
                    ),
                    f"{DOMAIN} import history {unit_id}",
                )

    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_HISTORY, _import_history, schema=IMPORT_HISTORY_SCHEMA
    )
    return True


async def _async_import_history(
    hass: HomeAssistant,
    coordinator: SuruCoordinator,
    unit_id: str,
    name: str,
    start: date,
) -> None:
    """Rebuild one unit's hourly statistic and report the outcome."""
    notification_id = f"{DOMAIN}_import_history_{unit_id}"
    days = (dt_util.now().date() - start).days + 1
    persistent_notification.async_create(
        hass,
        f"Loading hourly consumption of {name} since {start.isoformat()} "
        f"({days} days). This takes a few minutes.",
        title="SURU WaterMonitor",
        notification_id=notification_id,
    )
    try:
        series = await async_rebuild_hourly_consumption(
            hass, coordinator.api, unit_id, name, start
        )
    except SuruError as err:
        _LOGGER.warning("Loading history for %s failed: %s", name, err)
        message = f"Loading the history of {name} failed: {err}"
    else:
        if series:
            first = dt_util.as_local(series[0][0])
            message = (
                f"Imported {len(series)} hourly values of {name}. "
                f"The SURU cloud has data since {first:%Y-%m-%d %H:%M}."
            )
        else:
            message = (
                f"The SURU cloud returned no hourly values for {name} since "
                f"{start.isoformat()}. The statistic was left unchanged."
            )
    persistent_notification.async_create(
        hass, message, title="SURU WaterMonitor", notification_id=notification_id
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SURU WaterMonitor from a config entry."""
    api = SuruWaterApi(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    minutes = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
    coordinator = SuruCoordinator(hass, api, timedelta(minutes=minutes))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version == 1 and entry.minor_version < 2:
        # Pre-release builds imported the hourly statistics from 00:00 UTC
        # instead of local midnight (shifted by the UTC offset). Drop them;
        # they are re-imported with the right hours on the next update.
        await async_clear_hourly_statistics(hass)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options (e.g. scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
