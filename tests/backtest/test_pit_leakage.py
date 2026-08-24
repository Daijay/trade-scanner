"""Adversarial leakage tests for ``backtest.pit.PointInTimeView``.

This is the critical test file of the engine. Every other number the backtest
produces is worthless if a view can see one bar past its ``as_of``.

The suite runs against the **real** parquet cache (AAPL, a liquid name with
complete history) so it exercises the same code path the engine will, including
the real 30m -> 4h resample. It is skipped, not silently weakened, when the
cache is absent.

Two of these tests deliberately use an independent oracle — they re-read the raw
parquet and compute the expected answer themselves — rather than comparing the
view against itself. A test that asks a view whether it agrees with a view
cannot detect a widened cut, because both sides move together.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.store import BarStore, _read_raw

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
TICKER = "AAPL"
#: A ticker with daily bars but no 30m history (one of the 59 HFDL-missing names).
NO_30M_TICKER = "APP"

#: Monday 2026-03-16, mid-session. Chosen so that a one-day-wider cut moves the
#: answer on *every* stored timeframe — a weekend ``as_of`` would hide the leak
#: in the daily frame.
AS_OF = "2026-03-16 12:00"

requires_cache = pytest.mark.skipif(
    not (DATA_ROOT / "ohlcv_30m_adj" / f"{TICKER}.parquet").exists()
    or not (DATA_ROOT / "ohlcv_daily" / f"{TICKER}.parquet").exists(),
    reason="local parquet cache not present",
)

pytestmark = requires_cache


@pytest.fixture()
def view_factory():
    store = BarStore(DATA_ROOT)

    def make(as_of):
        return store.view(pd.Timestamp(as_of))

    return make


# --------------------------------------------------------------------------
# the seven adversarial tests from the plan
# --------------------------------------------------------------------------


def test_bars_never_exceed_as_of(view_factory):
    """Every stored and derived timeframe stops at or before ``as_of``.

    Checked on all three, not just daily: daily bars are stamped at midnight,
    so a same-day leak cannot show up in ``index.max()`` there at all.
    """
    v = view_factory(AS_OF)
    for tf in ("30m", "4h", "daily"):
        df = v.bars(TICKER, tf)
        assert not df.empty, tf
        assert df.index.max() <= v.as_of, f"{tf}: {df.index.max()} > {v.as_of}"


def test_explicit_future_request_returns_nothing(view_factory):
    """Asking for the future yields an empty selection, not a KeyError or rows."""
    v = view_factory(AS_OF)
    for tf in ("30m", "4h", "daily"):
        df = v.bars(TICKER, tf)
        assert df[df.index > v.as_of].empty, tf


def test_as_of_cannot_be_reassigned(view_factory):
    """The clock is fixed at construction; a strategy cannot retarget it."""
    v = view_factory(AS_OF)
    with pytest.raises((AttributeError, TypeError)):
        v.as_of = pd.Timestamp("2026-08-01")
    with pytest.raises((AttributeError, TypeError)):
        v._as_of = pd.Timestamp("2026-08-01")
    with pytest.raises((AttributeError, TypeError)):
        v.smuggled = pd.Timestamp("2026-08-01")  # __slots__ blocks new attributes
    assert v.as_of == pd.Timestamp(AS_OF)


def test_view_holds_no_reference_to_full_history(view_factory):
    """Strongest guarantee: no attribute chain from a view back to an
    untruncated frame."""
    v = view_factory(AS_OF)
    assert not hasattr(v, "_store")
    assert not hasattr(v, "__dict__")
    for name in v.__slots__:
        attr = getattr(v, name)
        assert not isinstance(attr, pd.DataFrame)
        assert not isinstance(attr, BarStore)
    # and nothing reachable from the closure's captured cells is a frame or a store
    cells = getattr(v._read, "__closure__", None) or ()
    for cell in cells:
        assert not isinstance(cell.cell_contents, (pd.DataFrame, BarStore))


def test_resample_truncates_before_aggregating(view_factory):
    """Subtle leak: resampling full history and truncating afterwards lets the
    bucket containing ``as_of`` absorb post-``as_of`` bars into its
    High/Low/Close. Truncation must happen first.

    Checked two ways: the last 4h bucket must be consistent with the 30m bars
    the view itself admits, *and* it must equal what an independent read of the
    raw parquet, cut at ``as_of``, produces. The second check is what detects a
    widened cut — the first alone moves with it.
    """
    v = view_factory("2026-03-16 11:00")  # deliberately mid-bucket
    bars = v.bars(TICKER, "4h")
    last = bars.iloc[-1]

    intraday = v.bars(TICKER, "30m")
    window = intraday[intraday.index >= bars.index[-1]]
    assert not window.empty
    assert last["High"] == window["High"].max()
    assert last["Low"] == window["Low"].min()
    assert last["Close"] == window["Close"].iloc[-1]

    # independent oracle, straight off the raw parquet
    raw = _read_raw(DATA_ROOT, TICKER, "30m")
    known = raw[raw.index + pd.Timedelta(minutes=30) <= v.as_of]
    expected = (
        known[["Open", "High", "Low", "Close", "Volume"]]
        .resample("4h")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(how="any")
    )
    # prove the scenario actually discriminates: resampling first and cutting
    # afterwards gives a *different* final bucket, so this is a real oracle and
    # not a tautology
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    leaky = (
        raw[["Open", "High", "Low", "Close", "Volume"]].resample("4h").agg(agg).dropna(how="any")
    )
    leaky = leaky[leaky.index <= v.as_of]
    assert not leaky.empty
    assert (
        leaky.iloc[-1]["Close"] != expected.iloc[-1]["Close"]
        or leaky.iloc[-1]["High"] != expected.iloc[-1]["High"]
        or leaky.iloc[-1]["Volume"] != expected.iloc[-1]["Volume"]
    ), "as_of is not mid-bucket; this test would pass on a leaky implementation"

    pd.testing.assert_frame_equal(bars, expected)


def test_earlier_view_unaffected_by_later_view(view_factory):
    """Guards against a caching bug where a later view mutates shared state and
    contaminates an earlier one."""
    early = view_factory("2026-02-02 12:00")
    before = early.bars(TICKER, "daily").copy()
    before_30m = early.bars(TICKER, "30m").copy()

    later = view_factory("2026-08-01 12:00")
    later_daily = later.bars(TICKER, "daily")
    _ = later.bars(TICKER, "30m")
    # mutating a frame handed out by one view must not touch another's
    later_daily.iloc[:, :] = -1.0

    pd.testing.assert_frame_equal(early.bars(TICKER, "daily"), before)
    pd.testing.assert_frame_equal(early.bars(TICKER, "30m"), before_30m)


def test_frames_for_all_timeframes_respect_as_of(view_factory):
    """``frames_for`` is the shape ``filter.run_filter`` consumes; all three of
    its frames obey the clock, and the stored ones contain only *closed* bars.

    A daily bar stamped 2026-03-16 00:00 describes the whole 2026-03-16 session,
    including its close. At 12:00 that day it is not knowable, even though its
    timestamp is technically ``<= as_of``. Bar-completeness, not just the
    timestamp, is the real boundary for stored frames.
    """
    v = view_factory(AS_OF)
    frames = v.frames_for(TICKER)
    assert set(frames) == {"30m", "4h", "daily"}
    for tf, df in frames.items():
        assert not df.empty, tf
        assert df.index.max() <= v.as_of, tf

    daily = frames["daily"]
    assert daily.index.max() + pd.Timedelta(hours=16) <= v.as_of, (
        f"daily bar {daily.index.max()} had not closed by {v.as_of}"
    )
    intraday = frames["30m"]
    assert intraday.index.max() + pd.Timedelta(minutes=30) <= v.as_of, (
        f"30m bar {intraday.index.max()} had not closed by {v.as_of}"
    )


# --------------------------------------------------------------------------
# two beyond the plan
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "as_of",
    [
        "2025-06-01 12:00",  # long before the cache begins
        "2026-01-02 09:00",  # 30 minutes before the very first bar closes
    ],
)
def test_as_of_before_all_data_returns_empty_frames(view_factory, as_of):
    """A clock earlier than the cache is a normal warm-up state, not an error.

    The second case sits just inside the first bar's span, so it also fails if
    the cut is widened — "empty" must mean empty, not "nearly empty".
    """
    v = view_factory(as_of)
    frames = v.frames_for(TICKER)
    assert set(frames) == {"30m", "4h", "daily"}
    for tf, df in frames.items():
        assert df.empty, tf
        assert isinstance(df.index, pd.DatetimeIndex), tf
        assert {"Open", "High", "Low", "Close", "Volume"} <= set(df.columns), tf


def test_frames_for_ticker_without_30m_history_raises(view_factory):
    """Decision: missing bars are an error, never a silently partial dict.

    59 universe names have daily bars but no 30m history. ``filter.run_filter``
    needs all three frames — ``alignment`` is a count of agreeing timeframes —
    so a partial dict would make an *unavailable* ticker look like one that
    merely failed the filter, quietly biasing the hit rate. The caller decides
    what to do with the ``FileNotFoundError``.
    """
    if not (DATA_ROOT / "ohlcv_daily" / f"{NO_30M_TICKER}.parquet").exists():
        pytest.skip(f"{NO_30M_TICKER} not in daily cache")
    if (DATA_ROOT / "ohlcv_30m_adj" / f"{NO_30M_TICKER}.parquet").exists():
        pytest.skip(f"{NO_30M_TICKER} unexpectedly has 30m history")

    v = view_factory(AS_OF)
    with pytest.raises(FileNotFoundError):
        v.frames_for(NO_30M_TICKER)
    with pytest.raises(FileNotFoundError):
        v.bars(NO_30M_TICKER, "30m")
    with pytest.raises(FileNotFoundError):
        v.bars(NO_30M_TICKER, "4h")
    # daily alone still works — the failure is specific, not blanket
    assert not v.bars(NO_30M_TICKER, "daily").empty


# --------------------------------------------------------------------------
# small surface checks
# --------------------------------------------------------------------------


def test_unknown_timeframe_raises(view_factory):
    with pytest.raises(ValueError):
        view_factory(AS_OF).bars(TICKER, "weekly")


def test_lookback_returns_the_most_recent_rows(view_factory):
    v = view_factory(AS_OF)
    full = v.bars(TICKER, "30m")
    tail = v.bars(TICKER, "30m", lookback=5)
    assert len(tail) == 5
    pd.testing.assert_frame_equal(tail, full.tail(5))


def test_tz_aware_as_of_is_rejected():
    with pytest.raises(ValueError):
        BarStore(DATA_ROOT).view(pd.Timestamp("2026-03-16 12:00", tz="US/Eastern"))
