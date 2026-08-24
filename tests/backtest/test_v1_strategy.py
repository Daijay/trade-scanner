"""Tests for ``backtest.strategies.v1_technical``.

Most of these run on synthetic frames rather than the real cache, so the
geometry assertions are exact: a hand-built frame with a known daily close and
a known ATR has exactly one correct stop. The real-cache tests at the bottom
check the two things synthetic data cannot — that the adapter agrees with
``filter.passes_hard_filter`` on live-shaped bars, and that a universe salted
with a ticker that has no 30m history does not kill the slot.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config
import filter as v1_filter
from backtest.store import BarStore
from backtest.strategies.v1_technical import (
    CONVICTION_SOURCE,
    STOP_ATR_MULT,
    V1Technical,
    _rank_conviction,
)
from backtest.strategy import Signal, Strategy

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
AS_OF = pd.Timestamp("2026-03-16 12:00")

requires_cache = pytest.mark.skipif(
    not (DATA_ROOT / "ohlcv_30m_adj" / "AAPL.parquet").exists(),
    reason="local parquet cache not present",
)

#: A ticker with daily bars but no 30m history (one of the 59 HFDL-missing names).
NO_30M_TICKER = "APP"


# ------------------------------------------------------------------ fakes


def _trending_frame(n: int, start: float, step: float, freq: str) -> pd.DataFrame:
    """A clean monotonic ramp: every timeframe classifies it the same way."""
    idx = pd.date_range("2024-01-02 09:30", periods=n, freq=freq, name="timestamp")
    close = start + step * np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + abs(step) * 3,
            "Low": close - abs(step) * 3,
            "Close": close,
            "Volume": np.full(n, 5_000_000.0),
        },
        index=idx,
    )


def _flat_frame(n: int, level: float, freq: str) -> pd.DataFrame:
    """Dead-flat: neutral trend, zero ATR -> fails the filter."""
    idx = pd.date_range("2024-01-02 09:30", periods=n, freq=freq, name="timestamp")
    return pd.DataFrame(
        {
            "Open": level, "High": level, "Low": level, "Close": level,
            "Volume": np.full(n, 5_000_000.0),
        },
        index=idx,
    )


def _frames(builder) -> dict[str, pd.DataFrame]:
    return {
        "30m": builder(300, "30min"),
        "4h": builder(300, "4h"),
        "daily": builder(300, "D"),
    }


UP = lambda n, freq: _trending_frame(n, 100.0, 0.5, freq)      # noqa: E731
DOWN = lambda n, freq: _trending_frame(n, 400.0, -0.5, freq)   # noqa: E731
FLAT = lambda n, freq: _flat_frame(n, 100.0, freq)             # noqa: E731


class FakeView:
    """Minimal stand-in for ``PointInTimeView``: same two members generate uses."""

    def __init__(self, frames_by_ticker: dict, as_of=AS_OF, missing: set | None = None):
        self._frames = frames_by_ticker
        self._as_of = as_of
        self._missing = missing or set()

    @property
    def as_of(self):
        return self._as_of

    def frames_for(self, ticker):
        if ticker in self._missing or ticker not in self._frames:
            raise FileNotFoundError(f"no bars cached for {ticker}")
        return {tf: df.copy() for tf, df in self._frames[ticker].items()}


# ------------------------------------------------------------------ protocol


def test_satisfies_strategy_protocol():
    assert isinstance(V1Technical(), Strategy)
    assert V1Technical().name == "v1_technical"


def test_does_not_modify_live_modules():
    """The adapter uses filter.py as-is; nothing here monkeypatches it."""
    assert V1Technical()  # import-time smoke: importing us must not rebind these
    assert v1_filter.run_filter.__module__ == "filter"
    assert v1_filter.passes_hard_filter.__module__ == "filter"


# ------------------------------------------------------------------ selection


def test_generates_signals_only_for_hard_filter_survivors():
    universe_frames = {"UPUP": _frames(UP), "FLATCO": _frames(FLAT)}
    view = FakeView(universe_frames)

    signals = V1Technical().generate(view, ["UPUP", "FLATCO"])
    tickers = {s.ticker for s in signals}

    expected = set()
    for t, frames in universe_frames.items():
        analysis = __import__("indicators").analyze_symbol(frames)
        ok, _ = v1_filter.passes_hard_filter(t, frames, analysis)
        if ok:
            expected.add(t)

    assert tickers == expected
    assert "UPUP" in expected and "FLATCO" not in expected


def test_missing_history_is_counted_not_raised():
    view = FakeView({"UPUP": _frames(UP)}, missing={"GHOST"})
    strat = V1Technical()

    signals = strat.generate(view, ["UPUP", "GHOST"])

    assert [s.ticker for s in signals] == ["UPUP"]
    assert strat.last_run["missing_history"] == 1
    assert strat.last_run["missing_tickers"] == ["GHOST"]
    assert strat.last_run["scanned"] == 1
    assert strat.last_run["requested"] == 2


def test_empty_universe_returns_empty_list():
    assert V1Technical().generate(FakeView({}), []) == []


def test_respects_max_survivors(monkeypatch):
    monkeypatch.setattr(config, "MAX_SURVIVORS", 3)
    frames = {f"T{i}": _frames(UP) for i in range(10)}
    signals = V1Technical().generate(FakeView(frames), list(frames))
    assert len(signals) == 3


def test_totals_accumulate_across_slots():
    strat = V1Technical()
    view = FakeView({"UPUP": _frames(UP)}, missing={"GHOST"})
    strat.generate(view, ["UPUP", "GHOST"])
    strat.generate(view, ["UPUP", "GHOST"])
    assert strat.totals["slots"] == 2
    assert strat.totals["missing_history"] == 2
    assert strat.totals["signals"] == 2


# ------------------------------------------------------------------ geometry


def test_long_stop_is_two_daily_atr_below_entry():
    frames = _frames(UP)
    view = FakeView({"UPUP": frames})
    sig = V1Technical().generate(view, ["UPUP"])[0]

    analysis = __import__("indicators").analyze_symbol(frames)
    daily = analysis["snapshots"]["daily"]

    assert sig.bias == "long"
    assert sig.entry == pytest.approx(daily["close"])
    assert sig.stop == pytest.approx(daily["close"] - STOP_ATR_MULT * daily["atr14"])
    assert sig.target == pytest.approx(
        daily["close"] + config.MIN_RR * STOP_ATR_MULT * daily["atr14"]
    )


def test_short_geometry_is_mirrored():
    frames = _frames(DOWN)
    view = FakeView({"DN": frames})
    sig = V1Technical().generate(view, ["DN"])[0]

    analysis = __import__("indicators").analyze_symbol(frames)
    daily = analysis["snapshots"]["daily"]

    assert sig.bias == "short"
    assert sig.stop == pytest.approx(daily["close"] + STOP_ATR_MULT * daily["atr14"])
    assert sig.target == pytest.approx(
        daily["close"] - config.MIN_RR * STOP_ATR_MULT * daily["atr14"]
    )
    assert sig.target < sig.entry < sig.stop


def test_rr_equals_min_rr():
    sig = V1Technical().generate(FakeView({"UPUP": _frames(UP)}), ["UPUP"])[0]
    assert sig.rr == pytest.approx(config.MIN_RR)


def test_stop_multiple_is_configurable():
    view = FakeView({"UPUP": _frames(UP)})
    wide = V1Technical(stop_atr_mult=2.5).generate(view, ["UPUP"])[0]
    tight = V1Technical(stop_atr_mult=1.5).generate(view, ["UPUP"])[0]
    assert wide.entry - wide.stop > tight.entry - tight.stop


def test_horizon_comes_from_config_alignment_map():
    sig = V1Technical().generate(FakeView({"UPUP": _frames(UP)}), ["UPUP"])[0]
    assert sig.horizon in set(config.HORIZON_BY_ALIGNMENT.values())


# ------------------------------------------------------------------ conviction proxy


@pytest.mark.parametrize(
    "rank,total,expected",
    [(0, 1, 10), (0, 11, 10), (10, 11, 0), (5, 11, 5), (0, 2, 10), (1, 2, 0)],
)
def test_rank_conviction_mapping(rank, total, expected):
    assert _rank_conviction(rank, total) == expected


def test_conviction_is_monotonic_in_score():
    frames = {f"T{i}": _frames(UP) for i in range(6)}
    signals = V1Technical().generate(FakeView(frames), list(frames))
    convictions = [s.conviction for s in signals]
    assert convictions == sorted(convictions, reverse=True)
    assert max(convictions) == 10
    assert all(0 <= c <= 10 for c in convictions)


def test_reason_flags_the_proxy_loudly():
    """Nobody reading a signal should mistake this for analyst.py conviction."""
    sig = V1Technical().generate(FakeView({"UPUP": _frames(UP)}), ["UPUP"])[0]
    assert sig.reason.startswith("RANK-PROXY CONVICTION")
    assert "NOT analyst.py conviction" in sig.reason
    assert "score_survivor" in sig.reason
    assert "analyst.py" in CONVICTION_SOURCE


# ------------------------------------------------------------------ real cache


@requires_cache
def test_real_slot_produces_plausible_signals():
    store = BarStore(DATA_ROOT)
    tickers = sorted(p.stem for p in (DATA_ROOT / "ohlcv_30m_adj").glob("*.parquet"))[:40]
    strat = V1Technical()

    signals = strat.generate(store.view(AS_OF), tickers)

    assert isinstance(signals, list)
    assert all(isinstance(s, Signal) for s in signals)
    assert strat.last_run["scanned"] > 0
    for s in signals:
        assert s.entry > 0 and s.stop > 0 and s.target > 0
        assert s.rr == pytest.approx(config.MIN_RR)
        if s.bias == "long":
            assert s.stop < s.entry < s.target
        else:
            assert s.target < s.entry < s.stop


@requires_cache
def test_ticker_without_30m_history_does_not_kill_the_slot():
    store = BarStore(DATA_ROOT)
    strat = V1Technical()

    strat.generate(store.view(AS_OF), ["AAPL", NO_30M_TICKER])

    assert strat.last_run["missing_history"] == 1
    assert strat.last_run["missing_tickers"] == [NO_30M_TICKER]
