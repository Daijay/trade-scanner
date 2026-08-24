# tests/backtest/test_calendar.py
"""The simulation clock: which instants a replayed scan happens at.

Two things have to be true or the replay is not comparable to live trading:

1. The clock only ticks on days the market was actually open. Holidays are
   *derived from the data* — the distinct dates present in a liquid ticker's
   daily parquet — not from a hardcoded list, because the cache is the ground
   truth for what the engine can see and a hardcoded calendar drifts.
2. The slot times match ``config.SCAN_TIMES_PT``, so a simulated scan lands
   exactly where a live scan lands. Read from config; never hardcoded here.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import config
from backtest import calendar as bt_calendar

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REAL_ROOT = REPO_ROOT / "data"
HAS_REAL_CACHE = (REAL_ROOT / "ohlcv_daily" / "AAPL.parquet").exists()
needs_cache = pytest.mark.skipif(HAS_REAL_CACHE is False, reason="real bar cache not present")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture()
def fake_root(tmp_path):
    """A cache whose AAPL daily frame omits Mon 2026-03-16 — a synthetic holiday.

    Weekends are absent simply because the frame is built from business days,
    which is exactly how the real feed behaves.
    """
    d = tmp_path / "ohlcv_daily"
    d.mkdir(parents=True)
    days = pd.bdate_range("2026-03-02", "2026-03-31")
    days = days[days != pd.Timestamp("2026-03-16")]
    pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1},
        index=pd.DatetimeIndex(days, name="timestamp"),
    ).to_parquet(d / "AAPL.parquet")
    return tmp_path


def _expected_et_times() -> list[time]:
    """``config.SCAN_TIMES_PT`` expressed in the cache's US/Eastern wall clock."""
    pt, et = ZoneInfo(config.MARKET_TZ), ZoneInfo("America/New_York")
    ref = pd.Timestamp("2026-03-17")
    out = []
    for t in config.SCAN_TIMES_PT.values():
        aware = ref.to_pydatetime().replace(hour=t.hour, minute=t.minute, tzinfo=pt)
        out.append(aware.astimezone(et).time())
    return sorted(out)


# --------------------------------------------------------------------------
# trading days
# --------------------------------------------------------------------------

def test_trading_days_excludes_weekends(fake_root):
    days = list(bt_calendar.trading_days("2026-03-02", "2026-03-31", root=fake_root))
    assert days, "no trading days derived at all"
    assert all(d.weekday() < 5 for d in days)


def test_trading_days_excludes_a_day_missing_from_the_data(fake_root):
    days = list(bt_calendar.trading_days("2026-03-02", "2026-03-31", root=fake_root))
    assert pd.Timestamp("2026-03-16").date() not in {d.date() for d in days}
    assert pd.Timestamp("2026-03-17").date() in {d.date() for d in days}


def test_trading_days_are_sorted_and_unique(fake_root):
    days = list(bt_calendar.trading_days("2026-03-02", "2026-03-31", root=fake_root))
    assert days == sorted(days)
    assert len(days) == len(set(days))


# --------------------------------------------------------------------------
# scan slots
# --------------------------------------------------------------------------

def test_scan_slots_is_an_iterator_not_a_list(fake_root):
    slots = bt_calendar.scan_slots("2026-03-02", "2026-03-06", root=fake_root)
    assert iter(slots) is iter(slots), "scan_slots must return a lazy iterator"


def test_slot_times_match_config_scan_times(fake_root):
    slots = list(bt_calendar.scan_slots("2026-03-02", "2026-03-06", root=fake_root))
    assert slots
    assert {s.time() for s in slots} == set(_expected_et_times())


def test_one_slot_per_configured_time_per_trading_day(fake_root):
    slots = list(bt_calendar.scan_slots("2026-03-09", "2026-03-20", root=fake_root))
    days = list(bt_calendar.trading_days("2026-03-09", "2026-03-20", root=fake_root))
    assert len(slots) == len(days) * len(config.SCAN_TIMES_PT)


def test_slots_are_ordered_and_tz_naive(fake_root):
    slots = list(bt_calendar.scan_slots("2026-03-02", "2026-03-31", root=fake_root))
    assert slots == sorted(slots)
    assert all(s.tz is None for s in slots), "as_of must be tz-naive US/Eastern wall clock"


def test_slots_fall_only_within_the_requested_range(fake_root):
    lo, hi = pd.Timestamp("2026-03-09"), pd.Timestamp("2026-03-13 23:59:59")
    slots = list(bt_calendar.scan_slots("2026-03-09", "2026-03-13", root=fake_root))
    assert slots
    assert all(lo <= s <= hi for s in slots)


def test_no_slots_on_a_weekend(fake_root):
    slots = list(bt_calendar.scan_slots("2026-03-07", "2026-03-08", root=fake_root))
    assert slots == []


def test_no_slots_on_a_day_absent_from_the_data(fake_root):
    slots = list(bt_calendar.scan_slots("2026-03-16", "2026-03-16", root=fake_root))
    assert slots == []


def test_empty_range_yields_nothing(fake_root):
    assert list(bt_calendar.scan_slots("2026-03-20", "2026-03-10", root=fake_root)) == []


def test_unknown_reference_ticker_raises(fake_root):
    with pytest.raises(FileNotFoundError):
        list(bt_calendar.scan_slots("2026-03-02", "2026-03-06", root=fake_root,
                                    ticker="NOSUCHTICKER"))


# --------------------------------------------------------------------------
# against the real cache — the point of deriving from data
# --------------------------------------------------------------------------

@needs_cache
def test_known_2026_holiday_produces_no_slots():
    """Memorial Day, Mon 2026-05-25 — a weekday the NYSE is shut."""
    holiday = "2026-05-25"
    assert pd.Timestamp(holiday).weekday() < 5, "fixture assumption: this is a weekday"
    assert list(bt_calendar.scan_slots(holiday, holiday, root=REAL_ROOT)) == []
    # the surrounding sessions are open, so this is a holiday and not an empty cache
    assert list(bt_calendar.scan_slots("2026-05-26", "2026-05-26", root=REAL_ROOT))


@needs_cache
def test_real_calendar_skips_every_configured_market_holiday():
    """Cross-check the data-derived calendar against ``config.MARKET_HOLIDAYS``.

    config is the assertion, not the source: the calendar is still derived from
    the parquet. Only holidays inside the cached window are checked.
    """
    days = {d.date() for d in bt_calendar.trading_days("2026-01-01", "2026-08-24",
                                                       root=REAL_ROOT)}
    assert days, "real cache produced no trading days"
    lo, hi = min(days), max(days)
    checked = 0
    for h in config.MARKET_HOLIDAYS:
        hd = pd.Timestamp(h).date()
        if lo <= hd <= hi and pd.Timestamp(h).weekday() < 5:
            assert hd not in days, f"{h} is a market holiday but appears as a trading day"
            checked += 1
    assert checked >= 3, f"expected several holidays inside the window, checked {checked}"


@needs_cache
def test_real_slots_land_on_configured_times():
    slots = list(bt_calendar.scan_slots("2026-02-02", "2026-02-06", root=REAL_ROOT))
    assert slots
    assert {s.time() for s in slots} == set(_expected_et_times())
