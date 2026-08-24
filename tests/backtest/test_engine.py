"""Task 11: the simulation loop — clock -> view -> strategy -> gate -> resolution.

The loop's one non-obvious obligation is that a signal is resolved against bars
the generating view could not see. These tests pin that: the fake view records
every ``as_of`` it is asked for, and resolution is asserted to use a *later*
view than generation, feeding it only bars strictly after the generating slot.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
from backtest.engine import run_backtest
from backtest.strategy import Signal

SLOTS = [
    pd.Timestamp("2026-01-05 09:00"),
    pd.Timestamp("2026-01-05 15:30"),
    pd.Timestamp("2026-01-06 09:00"),
    pd.Timestamp("2026-01-06 15:30"),
]


def _series(highs, lows, closes, start):
    idx = pd.date_range(start, periods=len(highs), freq="30min")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(len(highs), 1e6),
        },
        index=idx,
    )


class FakeView:
    def __init__(self, as_of, bars_by_ticker, log):
        self._as_of = as_of
        self._bars = bars_by_ticker
        self._log = log

    @property
    def as_of(self):
        return self._as_of

    def bars(self, ticker, timeframe, lookback=None):
        self._log.append((self._as_of, ticker, timeframe))
        df = self._bars[ticker]
        return df.loc[df.index <= self._as_of]


class FakeStore:
    def __init__(self, bars_by_ticker):
        self._bars = bars_by_ticker
        self.reads: list = []
        self.views: list = []

    def tickers(self, timeframe="30m"):
        return sorted(self._bars)

    def view(self, as_of):
        self.views.append(pd.Timestamp(as_of))
        return FakeView(pd.Timestamp(as_of), self._bars, self.reads)


class OneSignalStrategy:
    """Fires the same long on the first slot only."""

    name = "fake"
    conviction_source = "test fixture"

    def __init__(self, tickers, fire_on=None):
        self.tickers = tickers
        self.fire_on = fire_on or SLOTS[:1]
        self.seen: list = []

    def generate(self, view, universe):
        self.seen.append(view.as_of)
        if view.as_of not in self.fire_on:
            return []
        return [
            Signal(
                ticker=t, bias="long", entry=100.0, stop=98.0, target=103.0,
                rr=1.5, horizon="swing", conviction=10 - i, reason="fixture",
            )
            for i, t in enumerate(self.tickers)
        ]


def _flat_bars(start="2026-01-05 09:00", n=40, high=100.5, low=99.5, close=100.0):
    return _series([high] * n, [low] * n, [close] * n, start)


def test_engine_calls_generate_once_per_slot_with_that_slots_clock():
    store = FakeStore({"AAA": _flat_bars()})
    strat = OneSignalStrategy(["AAA"])
    run_backtest(strat, slots=SLOTS, store=store, universe=["AAA"])
    assert strat.seen == SLOTS


def test_gate_is_applied_and_counted():
    tickers = [f"T{i}" for i in range(config.MAX_ALERTS + 4)]
    store = FakeStore({t: _flat_bars() for t in tickers})
    res = run_backtest(OneSignalStrategy(tickers), slots=SLOTS, store=store, universe=tickers)
    assert res["signals_before_gate"] == len(tickers)
    assert res["signals_after_gate"] == config.MAX_ALERTS
    assert len(res["alerts"]) == config.MAX_ALERTS


def test_resolution_uses_a_later_view_and_only_post_entry_bars():
    """A win that only exists *after* the generating slot must still be found,
    and the bar that produced it must never have been visible at generation."""
    idx_start = SLOTS[0]
    # flat through the generating slot, then a spike to the target at slot 2
    highs = [100.5] * 13 + [104.0] * 13
    bars = _series(highs, [99.5] * 26, [100.0] * 26, idx_start)
    store = FakeStore({"AAA": bars})

    res = run_backtest(OneSignalStrategy(["AAA"]), slots=SLOTS, store=store, universe=["AAA"])

    alert = res["alerts"][0]
    assert alert["outcome"] == "win"
    assert pd.Timestamp(alert["resolved_at"]) > SLOTS[0]
    # resolution views were built at later slots, never at the generating slot
    resolution_reads = [a for a in store.reads if a[2] == "30m"]
    assert resolution_reads, "resolution never read bars"
    assert all(as_of > SLOTS[0] for as_of, _, _ in resolution_reads)


def test_unresolved_signal_stays_open_and_is_reported():
    store = FakeStore({"AAA": _flat_bars(n=4)})
    res = run_backtest(
        OneSignalStrategy(["AAA"]), slots=SLOTS[:2], store=store, universe=["AAA"]
    )
    assert res["alerts"][0]["status"] == "open"


def test_missing_bars_do_not_crash_resolution():
    store = FakeStore({"AAA": _flat_bars()})
    strat = OneSignalStrategy(["AAA"])

    class Missing(FakeStore):
        def view(self, as_of):
            self.views.append(pd.Timestamp(as_of))
            return FakeView(pd.Timestamp(as_of), {}, self.reads)

    res = run_backtest(strat, slots=SLOTS, store=Missing({"AAA": _flat_bars()}), universe=["AAA"])
    assert res["alerts"][0]["status"] == "open"


def test_results_carry_run_metadata():
    store = FakeStore({"AAA": _flat_bars()})
    res = run_backtest(OneSignalStrategy(["AAA"]), slots=SLOTS, store=store, universe=["AAA"])
    for key in (
        "strategy", "slots", "universe_size", "signals_before_gate",
        "signals_after_gate", "alerts", "wall_clock_s", "conviction_source",
    ):
        assert key in res, key
    assert res["strategy"] == "fake"
    assert res["slots"] == len(SLOTS)
    assert res["universe_size"] == 1


def test_alerts_record_their_strategy_and_slot():
    store = FakeStore({"AAA": _flat_bars()})
    res = run_backtest(OneSignalStrategy(["AAA"]), slots=SLOTS, store=store, universe=["AAA"])
    alert = res["alerts"][0]
    assert alert["strategy"] == "fake"
    assert pd.Timestamp(alert["timestamp"]) == SLOTS[0]
    assert alert["scan"] in ("premarket", "preclose")
