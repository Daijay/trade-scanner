"""Tests for backtest.store — the parquet cache reader.

``BarStore`` is the only object in the engine that ever sees full history, so
its whole job is to hand out *nothing* untruncated. These tests pin the two
properties that make that structurally true rather than merely intended:

1. the public API is exactly ``{tickers, view, root}`` — no reader on it;
2. the raw reader is a module-level *function*, so a ``PointInTimeView``
   cannot reach full history through an attribute chain on anything it holds.

Plus one data-correctness test: the 30m timeframe must resolve to
``data/ohlcv_30m_adj/`` (the reconciled cache), never ``data/ohlcv_30m/``
(the raw vendor download, which carries uncorrected split discontinuities —
BKNG sits at 24.9x the true price scale before 2026-04-06).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import backtest.store as store_mod
from backtest.store import TIMEFRAME_DIRS, BarStore, _read_raw

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

requires_cache = pytest.mark.skipif(
    not (DATA_ROOT / "ohlcv_30m_adj").is_dir() or not (DATA_ROOT / "ohlcv_daily").is_dir(),
    reason="local parquet cache not present",
)


# --------------------------------------------------------------------------
# synthetic fixture root
# --------------------------------------------------------------------------


def _frame(n: int = 20, start: str = "2026-01-02", freq: str = "30min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, name="timestamp")
    close = pd.Series(np.linspace(100.0, 110.0, n), index=idx)
    return pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


@pytest.fixture()
def fixture_root(tmp_path: Path) -> Path:
    for tf, sub in TIMEFRAME_DIRS.items():
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        freq = "30min" if tf == "30m" else "D"
        for t in ("AAA", "BBB"):
            _frame(freq=freq).to_parquet(d / f"{t}.parquet")
    # a ticker present only in the daily cache, mirroring the 59 real symbols
    # that have no 30m history
    _frame(freq="D").to_parquet(tmp_path / TIMEFRAME_DIRS["daily"] / "DLY.parquet")
    return tmp_path


# --------------------------------------------------------------------------
# the design constraint
# --------------------------------------------------------------------------


def test_store_exposes_no_public_untruncated_reader(fixture_root):
    store = BarStore(fixture_root)
    public = [n for n in dir(store) if not n.startswith("_")]
    assert set(public) <= {"tickers", "view", "root"}, f"unexpected public API: {public}"


def test_raw_reader_is_a_module_level_function_not_a_method(fixture_root):
    """No attribute chain from a store (or a view holding one) to the reader."""
    store = BarStore(fixture_root)
    assert not hasattr(store, "_read_raw")
    assert not hasattr(type(store), "_read_raw")
    assert inspect.isfunction(store_mod._read_raw)
    # a plain function, not something bound to an instance
    assert getattr(store_mod._read_raw, "__self__", None) is None


def test_store_holds_no_frames(fixture_root):
    store = BarStore(fixture_root)
    for name in dir(store):
        assert not isinstance(getattr(store, name, None), pd.DataFrame)


# --------------------------------------------------------------------------
# behaviour
# --------------------------------------------------------------------------


def test_root_is_exposed_as_a_path(fixture_root):
    assert BarStore(fixture_root).root == Path(fixture_root)


def test_missing_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        BarStore(tmp_path / "nope")


def test_tickers_lists_cached_symbols_sorted(fixture_root):
    store = BarStore(fixture_root)
    assert store.tickers() == ["AAA", "BBB"]
    assert store.tickers("daily") == ["AAA", "BBB", "DLY"]


def test_tickers_rejects_unknown_timeframe(fixture_root):
    with pytest.raises(ValueError):
        BarStore(fixture_root).tickers("weekly")


def test_read_raw_returns_sorted_named_datetime_index(fixture_root):
    df = _read_raw(fixture_root, "AAA", "30m")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert df.index.tz is None
    assert {"Open", "High", "Low", "Close", "Volume"} <= set(df.columns)


def test_read_raw_missing_ticker_raises_filenotfound(fixture_root):
    with pytest.raises(FileNotFoundError):
        _read_raw(fixture_root, "ZZZ", "30m")


def test_read_raw_rejects_unknown_timeframe(fixture_root):
    with pytest.raises(ValueError):
        _read_raw(fixture_root, "AAA", "4h")  # derived, not stored


def test_read_raw_returns_a_fresh_frame_each_call(fixture_root):
    a = _read_raw(fixture_root, "AAA", "30m")
    a.loc[a.index[0], "Close"] = -999.0
    b = _read_raw(fixture_root, "AAA", "30m")
    assert b["Close"].iloc[0] != -999.0


# --------------------------------------------------------------------------
# the real cache: 30m must be the reconciled copy
# --------------------------------------------------------------------------


@requires_cache
def test_thirty_minute_timeframe_reads_the_adjusted_cache():
    assert TIMEFRAME_DIRS["30m"] == "ohlcv_30m_adj"
    assert TIMEFRAME_DIRS["daily"] == "ohlcv_daily"


@requires_cache
def test_bkng_has_no_split_discontinuity_via_the_store():
    """The raw 30m download puts BKNG at ~24.9x scale before 2026-04-06.

    Reading it would silently poison every indicator, so this asserts the
    store resolves to the reconciled copy by measuring the defect's absence.
    """
    if not (DATA_ROOT / TIMEFRAME_DIRS["30m"] / "BKNG.parquet").exists():
        pytest.skip("BKNG not in cache")
    df = _read_raw(DATA_ROOT, "BKNG", "30m")
    before = df.loc[:"2026-04-03", "Close"].tail(20).median()
    after = df.loc["2026-04-07":, "Close"].head(20).median()
    assert 0.5 < after / before < 2.0, f"split discontinuity survives: {before} -> {after}"
