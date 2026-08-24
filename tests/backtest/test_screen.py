"""Offline tests for backtest.screen, using synthetic parquet stores."""

import csv

import pandas as pd
import pytest

from backtest.screen import (
    MAX_BAR_RETURN,
    MAX_GAP_DAYS,
    MIN_BAR_COUNT_FRACTION,
    screen_store,
)


# --------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------

def _frame(closes, index):
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes.values,
            "High": closes.values * 1.01,
            "Low": closes.values * 0.99,
            "Close": closes.values,
            "Volume": [1_000_000] * len(closes),
        },
        index=pd.DatetimeIndex(index, name="timestamp"),
    )


def _clean_index(n, start="2026-01-02"):
    return pd.bdate_range(start, periods=n)


def _write(root, ticker, df):
    root.mkdir(parents=True, exist_ok=True)
    df.to_parquet(root / f"{ticker}.parquet")


@pytest.fixture
def store(tmp_path):
    """A store of 5 healthy 120-bar tickers; defects are added per-test."""
    root = tmp_path / "ohlcv_daily"
    for ticker in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        idx = _clean_index(120)
        _write(root, ticker, _frame([100.0 + i * 0.1 for i in range(120)], idx))
    return root


# --------------------------------------------------------------------------
# the three defects
# --------------------------------------------------------------------------

def test_rejects_single_bar_return_above_500_percent(store):
    """BNY: two instruments concatenated, producing a +1272% phantom bar."""
    idx = _clean_index(120)
    closes = [10.0] * 60 + [137.2] * 60          # +1272% in one bar
    _write(store, "BNY", _frame(closes, idx))

    clean, rejects = screen_store(store)

    assert "BNY" in rejects
    assert "return" in rejects["BNY"].lower()
    assert "BNY" not in clean
    assert "AAA" in clean


def test_accepts_a_large_but_sub_threshold_move(store):
    idx = _clean_index(120)
    closes = [10.0] * 60 + [30.0] * 60           # +200%, ugly but under the cut
    _write(store, "JUMP", _frame(closes, idx))

    clean, rejects = screen_store(store)

    assert "JUMP" not in rejects
    assert "JUMP" in clean


def test_rejects_interior_gap_longer_than_30_days(store):
    """A trading halt or a splice leaves a hole in the middle of history."""
    left = _clean_index(60)
    right = pd.bdate_range(left[-1] + pd.Timedelta(days=95), periods=60)
    idx = left.append(right)
    _write(store, "GAPY", _frame([100.0] * 120, idx))

    clean, rejects = screen_store(store)

    assert "GAPY" in rejects
    assert "gap" in rejects["GAPY"].lower()
    assert "GAPY" not in clean


def test_normal_weekend_and_holiday_gaps_are_not_rejected(store):
    clean, rejects = screen_store(store)
    assert rejects == {}
    assert sorted(clean) == ["AAA", "BBB", "CCC", "DDD", "EEE"]


def test_rejects_bar_count_under_half_the_cohort_median(store):
    """A mid-period IPO or a rename holds far fewer bars than its cohort."""
    idx = _clean_index(40, start="2026-05-01")    # 40 of a 120-bar median
    _write(store, "IPOX", _frame([50.0 + i * 0.1 for i in range(40)], idx))

    clean, rejects = screen_store(store)

    assert "IPOX" in rejects
    assert "bar count" in rejects["IPOX"].lower()
    assert "IPOX" not in clean


def test_bar_count_just_above_the_floor_survives(store):
    idx = _clean_index(70)                        # 70/120 = 58% of median
    _write(store, "SHORTY", _frame([50.0] * 70, idx))

    clean, rejects = screen_store(store)

    assert "SHORTY" not in rejects
    assert "SHORTY" in clean


# --------------------------------------------------------------------------
# report and edge cases
# --------------------------------------------------------------------------

def test_writes_a_screen_report_csv(store, tmp_path):
    idx = _clean_index(120)
    _write(store, "BNY", _frame([10.0] * 60 + [137.2] * 60, idx))
    report = tmp_path / "_screen_report.csv"

    screen_store(store, report_path=report)

    rows = list(csv.DictReader(report.open()))
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["BNY"]["status"] == "reject"
    assert by_ticker["AAA"]["status"] == "clean"
    assert "return" in by_ticker["BNY"]["reason"].lower()
    assert int(by_ticker["AAA"]["rows"]) == 120


def test_empty_frame_is_rejected_not_crashed(store):
    _write(store, "NULL", _frame([], pd.DatetimeIndex([], name="timestamp")))

    clean, rejects = screen_store(store)

    assert "NULL" in rejects
    assert "NULL" not in clean


def test_thresholds_are_the_documented_values():
    assert MAX_BAR_RETURN == 5.0
    assert MAX_GAP_DAYS == 30
    assert MIN_BAR_COUNT_FRACTION == 0.5
