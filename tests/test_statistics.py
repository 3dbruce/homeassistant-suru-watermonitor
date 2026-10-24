"""Hourly consumption -> long-term statistics (require pytest-homeassistant-custom-component)."""
import asyncio
import threading
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.recorder.tasks import RecorderTask
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.suru_water_monitor.api import SuruApiError
from custom_components.suru_water_monitor.const import DOMAIN
from custom_components.suru_water_monitor.statistics import (
    async_import_hourly_consumption,
    hourly_series,
    statistic_id,
)

BERLIN = ZoneInfo("Europe/Berlin")
DAY = date(2026, 6, 24)
DAY_START = datetime(2026, 6, 23, 22, tzinfo=timezone.utc)  # 00:00 CEST


def test_statistic_id_is_valid() -> None:
    assert statistic_id("144675") == "suru_water_monitor:144675_consumption"
    assert statistic_id("AB-12") == "suru_water_monitor:ab_12_consumption"


def test_hourly_series_starts_at_local_midnight() -> None:
    days = [(DAY, [1, None, 3] + [0] * 21), (DAY + timedelta(days=1), [5, None, None])]
    series = hourly_series(days, DAY_START + timedelta(days=1, hours=10), BERLIN)
    assert series[0] == (DAY_START, 1.0)
    assert series[1] == (DAY_START + timedelta(hours=1), 0.0)  # gap -> 0
    assert series[-1] == (DAY_START + timedelta(days=1), 5.0)  # trailing None dropped
    assert len(series) == 25


def test_hourly_series_drops_hours_before_activation() -> None:
    series = hourly_series([(DAY, [None, None, 4, None, 5])], DAY_START + timedelta(days=1), BERLIN)
    assert series == [
        (DAY_START + timedelta(hours=2), 4.0),
        (DAY_START + timedelta(hours=3), 0.0),
        (DAY_START + timedelta(hours=4), 5.0),
    ]


def test_hourly_series_skips_unfinished_hours() -> None:
    now = DAY_START + timedelta(hours=2, minutes=30)
    series = hourly_series([(DAY, [1, 2, 3, 4])], now, BERLIN)
    assert series == [(DAY_START, 1.0), (DAY_START + timedelta(hours=1), 2.0)]


def test_hourly_series_across_dst_end() -> None:
    # 2026-10-25 has 25 hours in Germany (CEST -> CET at 03:00).
    dst_day = date(2026, 10, 25)
    days = [(dst_day, list(range(25))), (dst_day + timedelta(days=1), [99])]
    series = hourly_series(days, datetime(2027, 1, 1, tzinfo=timezone.utc), BERLIN)
    starts = [start for start, _ in series]
    assert starts[0] == datetime(2026, 10, 24, 22, tzinfo=timezone.utc)
    assert series[24] == (datetime(2026, 10, 25, 22, tzinfo=timezone.utc), 24.0)
    assert series[25] == (datetime(2026, 10, 25, 23, tzinfo=timezone.utc), 99.0)
    assert len(set(starts)) == len(starts) == 26


def test_hourly_series_drops_extra_bucket_on_short_day() -> None:
    # 2026-03-29 has only 23 hours; a 24th bucket must not overlap the next day.
    dst_day = date(2026, 3, 29)
    days = [(dst_day, [1] * 24), (dst_day + timedelta(days=1), [2])]
    series = hourly_series(days, datetime(2027, 1, 1, tzinfo=timezone.utc), BERLIN)
    assert len(series) == 24
    assert series[-1] == (datetime(2026, 3, 29, 22, tzinfo=timezone.utc), 2.0)


def _api(days: dict[date, list]) -> AsyncMock:
    api = AsyncMock()

    async def consumption(unit_id, range_="day", day=None):
        if day not in days:
            raise SuruApiError("HTTP 400")
        return {"values": days[day], "range": "day", "groupBy": "hour"}

    api.async_get_consumption = AsyncMock(side_effect=consumption)
    return api


async def _rows(hass: HomeAssistant, start: datetime, end: datetime) -> list:
    stat_id = statistic_id("1")
    stats = await hass.async_add_executor_job(
        statistics_during_period, hass, start, end, {stat_id}, "hour", None,
        {"state", "sum"},
    )
    return [
        (dt_util.utc_from_timestamp(r["start"]), r["state"], r["sum"])
        for r in stats.get(stat_id, [])
    ]


async def test_import_backfills_then_refreshes(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    today = dt_util.now().date()
    yesterday = today - timedelta(days=1)
    yesterday_start = dt_util.start_of_local_day(yesterday)
    now = dt_util.start_of_local_day(today) + timedelta(hours=23, minutes=30)
    window = (yesterday_start - timedelta(days=1), now + timedelta(days=1))

    # First run: yesterday fully known, today not yet delivered by the utility.
    with patch(
        "custom_components.suru_water_monitor.statistics.dt_util.utcnow",
        return_value=now,
    ):
        await async_import_hourly_consumption(
            hass, _api({yesterday: [10] * 24}), "1", "SURU test", today, [None] * 24
        )
        await async_wait_recording_done(hass)
        rows = await _rows(hass, *window)
        assert len(rows) == 24
        assert rows[0] == (yesterday_start, 10, 10)
        assert rows[-1][1:] == (10, 240)

        # Later run: today's hours arrive; yesterday is re-imported unchanged.
        await async_import_hourly_consumption(
            hass, _api({yesterday: [10] * 24}), "1", "SURU test", today,
            [1] * 23 + [None],
        )
        await async_wait_recording_done(hass)
        rows = await _rows(hass, *window)
    assert len(rows) == 47
    assert rows[23][1:] == (10, 240)
    assert rows[24][0] == dt_util.start_of_local_day(today)
    assert rows[-1][1:] == (1, 263)


def _setup_api(days: dict[date, list]) -> AsyncMock:
    api = _api(days)
    api.async_get_units = AsyncMock(return_value=[{"id": "1"}])
    api.async_get_unit = AsyncMock(return_value={"devices": [{"id": "d1"}]})
    api.async_get_reading = AsyncMock(return_value={"reading": 12.3})
    api.async_get_incidents = AsyncMock(return_value={})
    return api


async def _setup(
    hass: HomeAssistant, api: AsyncMock, minor_version: int = 2
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, minor_version=minor_version,
        data={CONF_EMAIL: "u@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.suru_water_monitor.SuruWaterApi", return_value=api):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.minor_version == 2
    return entry


async def test_setup_entry_imports_statistics(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    today = dt_util.now().date()
    yesterday = today - timedelta(days=1)
    await _setup(hass, _setup_api({yesterday: [2] * 24, today: [None] * 24}))
    await async_wait_recording_done(hass)

    rows = await _rows(hass, dt_util.utcnow() - timedelta(days=31), dt_util.utcnow())
    assert len(rows) == 24
    assert rows[0][0] == dt_util.start_of_local_day(yesterday)
    assert rows[-1][1:] == (2, 48)


async def test_migration_clears_misaligned_statistics(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    today = dt_util.now().date()
    yesterday = today - timedelta(days=1)
    # Statistics as written by the pre-release build: bucket 0 at 00:00 UTC.
    utc_midnight = datetime.combine(yesterday, datetime.min.time(), timezone.utc)
    with patch(
        "custom_components.suru_water_monitor.statistics.dt_util.get_default_time_zone",
        return_value=timezone.utc,
    ):
        await async_import_hourly_consumption(
            hass, _api({yesterday: [7] * 24}), "1", "SURU test", today, [None] * 24
        )
    await async_wait_recording_done(hass)
    old = await _rows(hass, utc_midnight, utc_midnight + timedelta(hours=1))
    assert old[0][0] == utc_midnight

    # During Home Assistant startup the recorder does not work off its queue
    # yet; simulate that by parking its thread until setup has finished.
    release = threading.Event()

    class _Park(RecorderTask):
        def run(self, instance: Recorder) -> None:
            release.wait(10)

    recorder_mock.queue_task(_Park())
    try:
        await asyncio.wait_for(
            _setup(
                hass, _setup_api({yesterday: [2] * 24, today: [None] * 24}),
                minor_version=1,
            ),
            timeout=5,
        )
    finally:
        release.set()
    await async_wait_recording_done(hass)

    rows = await _rows(hass, dt_util.utcnow() - timedelta(days=31), dt_util.utcnow())
    assert len(rows) == 24
    assert rows[0][0] == dt_util.start_of_local_day(yesterday)
    assert rows[-1][1:] == (2, 48)


async def _import_history(hass: HomeAssistant, start: date) -> MagicMock:
    """Run the action, wait for the background rebuild, return notifications."""
    with patch(
        "custom_components.suru_water_monitor.persistent_notification.async_create"
    ) as notify:
        await hass.services.async_call(
            DOMAIN, "import_history", {"start_date": start.isoformat()}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    return notify


async def test_import_history_rebuilds_from_start_date(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    today = dt_util.now().date()
    activated = today - timedelta(days=45)
    # Cloud: no data before activation (None buckets / HTTP 400), 1 L per hour after.
    days = {today - timedelta(days=n): [1] * 24 for n in range(1, 46)}
    days |= {today - timedelta(days=n): [None] * 24 for n in range(46, 50)}
    days[today] = [None] * 24
    api = _setup_api(days)
    entry = await _setup(hass, api)
    await async_wait_recording_done(hass)
    # Initial backfill: the 29 past days of the 30-day window (today is empty).
    assert len(await _rows(hass, dt_util.utcnow() - timedelta(days=90), dt_util.utcnow())) == 29 * 24

    notify = await _import_history(hass, today - timedelta(days=60))

    rows = await _rows(hass, dt_util.utcnow() - timedelta(days=90), dt_util.utcnow())
    assert len(rows) == 45 * 24
    assert rows[0] == (dt_util.start_of_local_day(activated), 1, 1)
    assert rows[-1][1:] == (1, 45 * 24)
    message = notify.call_args_list[-1].args[1]
    assert "Imported 1080 hourly values" in message
    assert f"data since {activated.isoformat()} 00:00" in message

    # The regular update continues the rebuilt running sum.
    days[today] = [2] * 3 + [None] * 21
    with patch(
        "custom_components.suru_water_monitor.statistics.dt_util.utcnow",
        return_value=dt_util.start_of_local_day(today) + timedelta(hours=3),
    ):
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await async_wait_recording_done(hass)
    rows = await _rows(hass, dt_util.utcnow() - timedelta(days=90), dt_util.utcnow() + timedelta(days=1))
    assert rows[-1] == (dt_util.start_of_local_day(today) + timedelta(hours=2), 2, 45 * 24 + 6)


async def test_import_history_without_data_keeps_statistic(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    today = dt_util.now().date()
    api = _setup_api({today - timedelta(days=1): [3] * 24, today: [None] * 24})
    await _setup(hass, api)
    await async_wait_recording_done(hass)
    api.async_get_consumption.side_effect = SuruApiError("HTTP 400")

    notify = await _import_history(hass, today - timedelta(days=10))

    rows = await _rows(hass, dt_util.utcnow() - timedelta(days=31), dt_util.utcnow())
    assert len(rows) == 24
    assert "returned no hourly values" in notify.call_args_list[-1].args[1]


@pytest.mark.parametrize("days_ago", [0, -1, 5 * 366 + 1])
async def test_import_history_rejects_bad_start_date(
    recorder_mock: Recorder, hass: HomeAssistant, days_ago: int
) -> None:
    await _setup(hass, _setup_api({dt_util.now().date(): [None] * 24}))
    start = dt_util.now().date() - timedelta(days=days_ago)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "import_history", {"start_date": start.isoformat()}, blocking=True
        )
