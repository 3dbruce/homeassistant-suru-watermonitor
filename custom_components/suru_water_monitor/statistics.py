"""Import SURU hourly consumption into Home Assistant long-term statistics.

The SURU cloud only learns about consumption when the utility's smart meter
uploads its data, which is often hours or a day late. A sensor state would
therefore attribute a whole day's water to the moment it arrives. Instead, the
hourly buckets are written as external statistics with their real timestamps,
so the Energy dashboard and statistics graphs show water use per hour, like the
app does.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone, tzinfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_list_statistic_ids,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.unit_conversion import VolumeConverter

from .api import SuruApiError, SuruWaterApi
from .const import DOMAIN, STATS_BACKFILL_DAYS, STATS_REFRESH_DAYS

try:  # Home Assistant 2025.4+
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:
    StatisticMeanType = None  # type: ignore[assignment,misc]

_LOGGER = logging.getLogger(__name__)

# hass.data key: statistic ids queued for deletion whose old rows must be ignored.
_CLEARED = f"{DOMAIN}_cleared_statistics"
# hass.data key: per-statistic locks, so a history rebuild and the regular
# import never compute running sums from each other's half-written rows.
_LOCKS = f"{DOMAIN}_statistics_locks"


def _lock(hass: HomeAssistant, stat_id: str) -> asyncio.Lock:
    return hass.data.setdefault(_LOCKS, {}).setdefault(stat_id, asyncio.Lock())


def statistic_id(unit_id: str) -> str:
    """External statistic id for a unit's hourly consumption."""
    return f"{DOMAIN}:{slugify(str(unit_id))}_consumption"


def _local_midnight(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, datetime.min.time(), tz).astimezone(timezone.utc)


def hourly_series(
    days: list[tuple[date, list]], now: datetime, tz: tzinfo
) -> list[tuple[datetime, float]]:
    """Flatten per-day hourly buckets into (UTC hour start, litres), oldest first.

    Bucket 0 is local midnight of that day in `tz`; counting in UTC hours keeps
    23/25-hour days around DST changes right. Hours that have not ended yet,
    trailing hours without data (not yet delivered by the utility) and leading
    ones (before the meter was activated) are dropped. Gaps in between count as
    0 L so the running sum stays contiguous.
    """
    series: list[tuple[datetime, float | None]] = []
    for day, values in days:
        day_start = _local_midnight(day, tz)
        day_end = _local_midnight(day + timedelta(days=1), tz)
        for i, value in enumerate(values):
            start = day_start + timedelta(hours=i)
            if start >= day_end or start + timedelta(hours=1) > now:
                break
            series.append((start, value))
    while series and series[-1][1] is None:
        series.pop()
    while series and series[0][1] is None:
        series.pop(0)
    return [(start, float(value or 0)) for start, value in series]


def _metadata(stat_id: str, name: str) -> StatisticMetaData:
    meta: dict = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": UnitOfVolume.LITERS,
    }
    if StatisticMeanType is not None:
        meta["mean_type"] = StatisticMeanType.NONE
    else:
        meta["has_mean"] = False
    if "unit_class" in StatisticMetaData.__annotations__:  # newer HA versions
        meta["unit_class"] = VolumeConverter.UNIT_CLASS
    return meta  # type: ignore[return-value]


async def _async_sum_before(hass: HomeAssistant, stat_id: str, start: datetime) -> float:
    """Running sum of the last imported hour before `start` (0 if none)."""
    recorder = get_instance(hass)
    # Normally the previous hour exists; only after a long outage look further back.
    for lookback in (timedelta(hours=1), timedelta(days=366)):
        stats = await recorder.async_add_executor_job(
            statistics_during_period,
            hass, start - lookback, start, {stat_id}, "hour", None, {"sum"},
        )
        if rows := stats.get(stat_id):
            return float(rows[-1].get("sum") or 0)
    return 0.0


async def async_import_hourly_consumption(
    hass: HomeAssistant,
    api: SuruWaterApi,
    unit_id: str,
    name: str,
    today: date,
    today_values: list,
) -> None:
    """Fetch recent hourly buckets for one unit and (re-)import them.

    `today` is the local date whose buckets the caller already fetched.
    """
    stat_id = statistic_id(unit_id)
    lock = _lock(hass, stat_id)
    if lock.locked():
        _LOGGER.debug("History import running for %s, skipping update", stat_id)
        return
    async with lock:
        await _async_import_recent(hass, api, unit_id, name, today, today_values)


async def _async_import_recent(
    hass: HomeAssistant,
    api: SuruWaterApi,
    unit_id: str,
    name: str,
    today: date,
    today_values: list,
) -> None:
    stat_id = statistic_id(unit_id)
    tz = dt_util.get_default_time_zone()
    oldest = today - timedelta(days=STATS_BACKFILL_DAYS - 1)

    cleared: set[str] = hass.data.get(_CLEARED, set())
    if stat_id in cleared:
        last = {}  # deletion still queued in the recorder: start from scratch
    else:
        last = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, stat_id, True, {"sum"}
        )
    if last.get(stat_id):
        last_start = dt_util.utc_from_timestamp(last[stat_id][0]["start"])
        first_day = min(
            today - timedelta(days=STATS_REFRESH_DAYS - 1),
            last_start.astimezone(tz).date(),
        )
        first_day = max(first_day, oldest)
    else:
        first_day = oldest

    days = await _async_fetch_days(api, unit_id, first_day, today)
    days.append((today, today_values))

    series = hourly_series(days, dt_util.utcnow(), tz)
    if not series:
        return
    total = (
        await _async_sum_before(hass, stat_id, series[0][0]) if last.get(stat_id) else 0.0
    )
    _async_write(hass, stat_id, name, series, total)
    cleared.discard(stat_id)


async def _async_fetch_days(
    api: SuruWaterApi, unit_id: str, first_day: date, end: date
) -> list[tuple[date, list]]:
    """Hourly buckets for each local day in [first_day, end)."""
    days: list[tuple[date, list]] = []
    day = first_day
    while day < end:
        try:
            consumption = await api.async_get_consumption(unit_id, "day", day)
        except SuruApiError as err:
            _LOGGER.debug("No hourly consumption for %s on %s: %s", unit_id, day, err)
        else:
            days.append((day, consumption.get("values") or []))
        day += timedelta(days=1)
    return days


@callback
def _async_write(
    hass: HomeAssistant,
    stat_id: str,
    name: str,
    series: list[tuple[datetime, float]],
    total: float,
) -> None:
    """Queue the series as statistics, continuing the running sum from `total`."""
    statistics: list[StatisticData] = []
    for start, litres in series:
        total += litres
        statistics.append(StatisticData(start=start, state=litres, sum=total))
    _LOGGER.debug(
        "Importing %d hourly values for %s (%s .. %s)",
        len(statistics), stat_id, series[0][0], series[-1][0],
    )
    async_add_external_statistics(hass, _metadata(stat_id, name), statistics)


async def async_rebuild_hourly_consumption(
    hass: HomeAssistant, api: SuruWaterApi, unit_id: str, name: str, start: date
) -> list[tuple[datetime, float]]:
    """Replace a unit's statistic with all hourly buckets since `start`.

    Fetches one day per request, so this takes a while for long periods; run
    it in the background. Returns the imported series (empty: nothing changed).
    """
    stat_id = statistic_id(unit_id)
    async with _lock(hass, stat_id):
        today = dt_util.now().date()
        days = await _async_fetch_days(api, unit_id, start, today + timedelta(days=1))
        series = hourly_series(days, dt_util.utcnow(), dt_util.get_default_time_zone())
        if not series:
            return series
        recorder = get_instance(hass)
        # A running sum only works from the very first row, so start over.
        recorder.async_clear_statistics([stat_id])
        _async_write(hass, stat_id, name, series, 0.0)
        hass.data.get(_CLEARED, set()).discard(stat_id)
        # Keep the lock until the rows are in the database, so the next
        # regular import continues from the rebuilt sums.
        await recorder.async_block_till_done()
    return series


async def async_clear_hourly_statistics(hass: HomeAssistant) -> None:
    """Delete all hourly consumption statistics of this integration.

    They are re-imported (with backfill) on the next update. The recorder only
    works off its queue once Home Assistant has started, so this must not wait
    for the deletion (it runs during startup); the next import ignores the old
    rows instead and is queued after the deletion.
    """
    stat_ids = [
        meta["statistic_id"]
        for meta in await async_list_statistic_ids(hass, statistic_type="sum")
        if meta.get("source") == DOMAIN
    ]
    if stat_ids:
        _LOGGER.info("Clearing hourly consumption statistics %s", stat_ids)
        get_instance(hass).async_clear_statistics(stat_ids)
        hass.data.setdefault(_CLEARED, set()).update(stat_ids)
