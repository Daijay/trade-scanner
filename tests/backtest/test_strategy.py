"""Contract tests for ``backtest.strategy``.

The important one is :func:`test_signal_carries_every_journal_setup_key`: it
reads the keys ``journal.log_alerts`` actually indexes out of a setup dict
rather than trusting a list copied by hand, so if ``journal.py`` grows a
required key this fails instead of the backtest quietly logging alerts with a
``KeyError`` — or worse, with a default.

The slot-driver test stands in for the engine, which does not exist until Task
11. It drives a fake strategy over real scan slots exactly as the engine will
and asserts the two things the protocol promises: one ``generate`` call per
slot, and a view whose clock is that slot.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path

import pandas as pd
import pytest

import journal
from backtest.market_calendar import scan_slots
from backtest.store import BarStore
from backtest.strategy import BIASES, JOURNAL_SETUP_KEYS, Signal, Strategy

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

requires_cache = pytest.mark.skipif(
    not (DATA_ROOT / "ohlcv_daily" / "AAPL.parquet").exists(),
    reason="local parquet cache not present",
)


def make_signal(**overrides) -> Signal:
    kwargs = dict(
        ticker="AAPL",
        bias="long",
        entry=100.0,
        stop=96.0,
        target=106.0,
        rr=1.5,
        horizon="swing",
        conviction=8,
        reason="test",
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


# ---------------------------------------------------------------- Signal


def test_signal_is_frozen():
    sig = make_signal()
    with pytest.raises(FrozenInstanceError):
        sig.entry = 999.0


def test_signal_carries_every_journal_setup_key():
    """The keys log_alerts reads, read out of log_alerts' own source."""
    src = inspect.getsource(journal.log_alerts)
    setup = make_signal().as_setup()
    for key in JOURNAL_SETUP_KEYS:
        assert f'setup["{key}"]' in src, f"{key} is not read by journal.log_alerts any more"
        assert key in setup, f"Signal is missing journal setup key {key}"


def test_as_setup_is_accepted_by_journal_log_alerts(tmp_path, monkeypatch):
    """End to end: a Signal's setup dict survives log_alerts unchanged."""
    path = tmp_path / "journal.json"
    monkeypatch.setattr(journal, "load_journal", lambda *a, **k: [])
    saved = {}
    monkeypatch.setattr(journal, "save_journal", lambda alerts, *a, **k: saved.setdefault("a", alerts))

    sig = make_signal()
    now = pd.Timestamp("2026-03-16 12:00").to_pydatetime()
    records = journal.log_alerts([sig.as_setup()], "preclose", now)

    assert len(records) == 1
    rec = records[0]
    assert rec["ticker"] == "AAPL"
    assert rec["bias"] == "long"
    assert rec["entry"] == 100.0
    assert rec["stop"] == 96.0
    assert rec["target"] == 106.0
    assert rec["rr"] == 1.5
    assert rec["horizon"] == "swing"
    assert rec["conviction"] == 8
    assert rec["status"] == "open"
    assert path  # tmp_path fixture used only to keep the real journal untouched


@pytest.mark.parametrize("bias", BIASES)
def test_valid_geometry_accepted(bias):
    if bias == "long":
        make_signal(bias="long", entry=100.0, stop=96.0, target=106.0)
    else:
        make_signal(bias="short", entry=100.0, stop=104.0, target=94.0)


def test_rejects_unknown_bias():
    with pytest.raises(ValueError, match="bias"):
        make_signal(bias="sideways")


def test_rejects_inverted_long_geometry():
    with pytest.raises(ValueError, match="stop < entry < target"):
        make_signal(bias="long", entry=100.0, stop=106.0, target=96.0)


def test_rejects_inverted_short_geometry():
    with pytest.raises(ValueError, match="target < entry < stop"):
        make_signal(bias="short", entry=100.0, stop=96.0, target=106.0)


# ---------------------------------------------------------------- Strategy


class FakeStrategy:
    """Two-line strategy, per the plan's Task 8 Step 1."""

    name = "fake"

    def __init__(self):
        self.seen: list[pd.Timestamp] = []

    def generate(self, view, universe):
        self.seen.append(view.as_of)
        return []


def test_fake_strategy_satisfies_protocol():
    assert isinstance(FakeStrategy(), Strategy)


def test_object_without_generate_does_not_satisfy_protocol():
    class NotAStrategy:
        name = "nope"

    assert not isinstance(NotAStrategy(), Strategy)


@requires_cache
def test_strategy_called_once_per_slot_with_matching_as_of():
    """The engine's contract, driven by hand until Task 11 builds the engine."""
    slots = list(scan_slots("2026-03-02", "2026-03-06", root=DATA_ROOT))
    assert slots, "expected scan slots for the first week of March 2026"

    store = BarStore(DATA_ROOT)
    strat = FakeStrategy()
    for slot in slots:
        strat.generate(store.view(slot), ["AAPL"])

    assert len(strat.seen) == len(slots)
    assert strat.seen == slots
