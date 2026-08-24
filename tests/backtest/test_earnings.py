"""Offline tests for backtest.earnings. No network: yfinance is faked."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backtest.earnings import EarningsCalendar, build_earnings_cache


# --------------------------------------------------------------------------
# blackout-window semantics
# --------------------------------------------------------------------------

def test_in_blackout_window():
    cal = EarningsCalendar({"AAPL": [date(2026, 4, 30)]})
    assert cal.in_blackout("AAPL", date(2026, 4, 28), days_before=3, days_after=1)
    assert not cal.in_blackout("AAPL", date(2026, 4, 25), days_before=3, days_after=1)


def test_ticker_with_no_earnings_never_raises():
    cal = EarningsCalendar({})
    assert cal.dates_for("SPY") == []
    assert not cal.in_blackout("SPY", date(2026, 4, 28), 3, 1)


def test_blackout_covers_days_after_and_the_day_itself():
    cal = EarningsCalendar({"AAPL": [date(2026, 4, 30)]})
    assert cal.in_blackout("AAPL", date(2026, 4, 30), 3, 1)
    assert cal.in_blackout("AAPL", date(2026, 5, 1), 3, 1)
    assert not cal.in_blackout("AAPL", date(2026, 5, 2), 3, 1)


def test_dates_for_is_sorted_and_deduped():
    cal = EarningsCalendar({"AAPL": [date(2026, 4, 30), date(2026, 1, 29), date(2026, 4, 30)]})
    assert cal.dates_for("AAPL") == [date(2026, 1, 29), date(2026, 4, 30)]


def test_lookup_is_case_insensitive():
    cal = EarningsCalendar({"AAPL": [date(2026, 4, 30)]})
    assert cal.dates_for("aapl") == [date(2026, 4, 30)]


# --------------------------------------------------------------------------
# cache build / round-trip
# --------------------------------------------------------------------------

class _FakeTicker:
    """Stands in for yfinance.Ticker."""

    def __init__(self, symbol):
        self.symbol = symbol

    def get_earnings_dates(self, limit=None):
        if self.symbol == "BOOM":
            raise RuntimeError("yfinance exploded")
        if self.symbol == "SPY":
            return None
        idx = pd.DatetimeIndex(
            [pd.Timestamp("2026-04-30 16:30"), pd.Timestamp("2026-01-29 16:30")],
            name="Earnings Date",
        )
        return pd.DataFrame({"EPS Estimate": [1.0, 2.0]}, index=idx)


def test_build_cache_tolerates_a_ticker_whose_fetch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("backtest.earnings.yf.Ticker", _FakeTicker)
    out = tmp_path / "earnings.parquet"
    cal, failures = build_earnings_cache(["AAPL", "BOOM", "SPY"], out, sleep_seconds=0)

    assert "BOOM" in failures
    assert cal.dates_for("BOOM") == []
    assert not cal.in_blackout("BOOM", date(2026, 4, 28), 3, 1)
    assert cal.dates_for("SPY") == []
    assert cal.dates_for("AAPL") == [date(2026, 1, 29), date(2026, 4, 30)]
    assert out.exists()


def test_cache_round_trips_through_parquet(tmp_path, monkeypatch):
    monkeypatch.setattr("backtest.earnings.yf.Ticker", _FakeTicker)
    out = tmp_path / "earnings.parquet"
    build_earnings_cache(["AAPL", "SPY"], out, sleep_seconds=0)

    loaded = EarningsCalendar.load(out)
    assert loaded.dates_for("AAPL") == [date(2026, 1, 29), date(2026, 4, 30)]
    assert loaded.dates_for("SPY") == []
    assert loaded.in_blackout("AAPL", date(2026, 4, 28), 3, 1)


def test_load_missing_file_returns_empty_calendar(tmp_path):
    cal = EarningsCalendar.load(tmp_path / "nope.parquet")
    assert cal.dates_for("AAPL") == []
