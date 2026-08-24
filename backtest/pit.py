# backtest/pit.py
"""``PointInTimeView`` — the truncating read boundary. **The critical file.**

Every bar the engine ever sees passes through here. If one post-``as_of`` row
gets through, every hit rate the backtest reports is fiction, and it will be a
flattering fiction, which is the dangerous kind.

Structural guarantees
---------------------
*The view does not hold the store.* It holds a closure built by
:func:`_make_truncated_reader`, which applies the cut inside itself before
returning anything. There is no code path from a view to an untruncated frame:
no ``_store`` attribute, no frame captured in the closure (only ``root`` and
``as_of``, both immutable values).

*The clock cannot be retargeted.* ``__slots__`` blocks new attributes,
``__setattr__``/``__delattr__`` are overridden to reject writes, and ``as_of``
is a read-only property.

*No caching.* Each call re-reads and re-cuts. Two views therefore cannot
contaminate each other through shared state, and a caller who mutates a frame
handed to it mutates only its own copy.

The boundary: bar *completeness*, not just the timestamp
--------------------------------------------------------
A stored bar is visible only once the interval it summarises has **closed** by
``as_of`` — not merely once its timestamp is ``<= as_of``.

This matters most for daily bars. A daily bar stamped ``2026-03-16 00:00``
describes the entire 2026-03-16 session, close included. Its timestamp is
trivially ``<= 2026-03-16 12:00``, so a naive index cut hands a strategy
scanning at noon that day's *finished* high, low and close — a six-and-a-half
hour look-ahead on the single frame that drives ``MIN_AVG_VOLUME``,
``MIN_PRICE`` and the ATR the stops are sized from. The live scanner does not
have this problem: yfinance returns today's daily bar partially formed. The
cache stores it finished, so the cut has to do the work instead.

The same rule applies to 30m bars (a bar stamped 09:30 is complete at 10:00);
there it is usually a no-op, because scan slots land on 30-minute boundaries.

Derived frames are different. ``4h`` is built *from already-truncated* 30m bars,
so a partial final bucket contains only knowable data and is kept — that is the
correct point-in-time answer at a mid-bucket ``as_of``. What is forbidden is
resampling full history and cutting afterwards, which lets the bucket holding
``as_of`` absorb post-``as_of`` bars into its High/Low/Close. Order matters:
**truncate, then resample.**
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.store import TIMEFRAME_DIRS, _read_raw

#: Timeframes a view can serve. ``4h`` is derived from ``30m``, not stored.
TIMEFRAMES: tuple[str, ...] = ("30m", "4h", "daily")

#: Frames handed to ``filter.run_filter``, in the order it expects them.
FILTER_TIMEFRAMES: tuple[str, ...] = ("30m", "4h", "daily")

#: How long after its timestamp each *stored* bar is complete. A daily bar is
#: stamped at midnight and closes with the 16:00 US/Eastern session close.
_BAR_SPAN: dict[str, pd.Timedelta] = {
    "30m": pd.Timedelta(minutes=30),
    "daily": pd.Timedelta(hours=16),
}

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]
_RESAMPLE_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}

#: ``4h`` bucket width, anchored on the day start exactly as
#: ``indicators.resample_to_4h`` anchors it, so the engine's 4h bars line up
#: with the live scanner's.
_FOUR_HOUR = "4h"


def _normalise_as_of(as_of) -> pd.Timestamp:
    ts = pd.Timestamp(as_of)
    if ts.tz is not None:
        raise ValueError(
            "as_of must be tz-naive US/Eastern wall clock to match the cache index; "
            f"got {ts!r}"
        )
    if pd.isna(ts):
        raise ValueError("as_of must be a real timestamp")
    return ts


def _make_truncated_reader(root: Path, as_of: pd.Timestamp):
    """A reader for one fixed instant. Closes over ``root`` and ``as_of`` only.

    The returned function is the single door between the cache and the engine.
    It applies the cut before anything else sees the frame, so no caller — not
    even a buggy one — can be handed a row it should not know about.
    """
    root = Path(root)

    def read(ticker: str, timeframe: str) -> pd.DataFrame:
        df = _read_raw(root, ticker, timeframe)  # module-private, full history
        span = _BAR_SPAN[timeframe]
        return df.loc[df.index <= as_of - span]  # the cut, before anything else sees it

    return read


def _empty_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], name="timestamp")
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in _OHLCV}, index=idx)


def _resample_4h(intraday: pd.DataFrame) -> pd.DataFrame:
    if intraday.empty:
        return _empty_frame()
    out = intraday[_OHLCV].resample(_FOUR_HOUR).agg(_RESAMPLE_AGG).dropna(how="any")
    out.index.name = "timestamp"
    return out


class PointInTimeView:
    """A read-only view of the bar cache frozen at ``as_of``.

    Construct via :meth:`backtest.store.BarStore.view`.
    """

    __slots__ = ("_read", "_as_of")

    def __init__(self, root: Path, as_of):
        as_of = _normalise_as_of(as_of)
        object.__setattr__(self, "_read", _make_truncated_reader(root, as_of))
        object.__setattr__(self, "_as_of", as_of)

    # -- immutability ----------------------------------------------------

    def __setattr__(self, name, value):
        raise AttributeError(f"PointInTimeView is frozen; cannot set {name!r}")

    def __delattr__(self, name):
        raise AttributeError(f"PointInTimeView is frozen; cannot delete {name!r}")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PointInTimeView(as_of={self._as_of!s})"

    # -- clock -----------------------------------------------------------

    @property
    def as_of(self) -> pd.Timestamp:
        return self._as_of

    # -- reads -----------------------------------------------------------

    def bars(self, ticker: str, timeframe: str, lookback: int | None = None) -> pd.DataFrame:
        """Bars for *ticker* on *timeframe*, complete as of the view's clock.

        ``30m`` and ``daily`` are read from the cache and cut. ``4h`` is built
        by resampling the **already cut** 30m frame — never the other way round.

        Raises ``ValueError`` for an unknown timeframe and ``FileNotFoundError``
        when the ticker has no bars cached for it. Returns an empty OHLCV frame,
        rather than raising, when the clock predates the ticker's history.
        """
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")

        if timeframe == "4h":
            df = _resample_4h(self._read(ticker, "30m"))
        else:
            df = self._read(ticker, timeframe)
            if df.empty:
                df = _empty_frame()

        if lookback is not None:
            if lookback < 0:
                raise ValueError(f"lookback must be non-negative, got {lookback}")
            df = df.tail(lookback)
        return df

    def frames_for(self, ticker: str) -> dict[str, pd.DataFrame]:
        """``{'30m': df, '4h': df, 'daily': df}`` — the shape ``filter.run_filter`` eats.

        All or nothing. If a timeframe is not cached for *ticker* this raises
        ``FileNotFoundError`` rather than returning a partial dict: ``alignment``
        is a count of *agreeing timeframes*, so a missing frame would not make
        the ticker look unavailable, it would make it look like a ticker that
        merely failed the filter, and that bias is invisible in the output. The
        caller decides whether to skip the name or abort the run.
        """
        return {tf: self.bars(ticker, tf) for tf in FILTER_TIMEFRAMES}


__all__ = ["PointInTimeView", "TIMEFRAMES", "FILTER_TIMEFRAMES"]

# ``TIMEFRAME_DIRS`` is re-exported for callers that need to know which stored
# directories back the view without importing the store's private reader.
STORED_TIMEFRAME_DIRS = TIMEFRAME_DIRS
