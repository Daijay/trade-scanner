# backtest/store.py
"""``BarStore`` — the parquet bar cache, and the only object that sees full history.

Design constraint (plan Task 5)
-------------------------------
A backtest leaks future data the moment any object a strategy can touch is able
to reach an untruncated frame. ``BarStore`` therefore exposes **no public method
returning untruncated bars**: its entire public surface is ``tickers``, ``view``
and ``root``. The raw reader is :func:`_read_raw`, a module-level *private
function* rather than a bound method, so a :class:`~backtest.pit.PointInTimeView`
holding a closure over ``root`` still has no attribute chain leading back to
full history.

Timeframe -> directory
----------------------
``30m`` resolves to ``data/ohlcv_30m_adj/``, the **reconciled** cache written by
``backtest.reconcile``, never ``data/ohlcv_30m/``. The raw vendor download is
not back-adjusted for corporate actions (BKNG sits at ~24.9x the true price
scale before 2026-04-06), and reading it silently poisons every indicator that
crosses the boundary. The raw directory is kept only as the audit record of what
the vendor sent.

``4h`` is not stored. It is *derived* from truncated 30m bars inside
``PointInTimeView`` — resampling stored 4h buckets would let the bucket
containing ``as_of`` absorb post-``as_of`` bars.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backtest.pit import PointInTimeView

#: Stored timeframes and the sub-directory of ``root`` each lives in.
#: ``4h`` is deliberately absent: it is derived, not stored.
TIMEFRAME_DIRS: dict[str, str] = {
    "30m": "ohlcv_30m_adj",
    "daily": "ohlcv_daily",
}


def _timeframe_dir(root: Path, timeframe: str) -> Path:
    try:
        sub = TIMEFRAME_DIRS[timeframe]
    except KeyError:
        raise ValueError(
            f"unknown stored timeframe {timeframe!r}; expected one of {sorted(TIMEFRAME_DIRS)}"
        ) from None
    return Path(root) / sub


#: Parsed frames, keyed by absolute path. The store is *allowed* to hold full
#: history -- only a ``PointInTimeView`` must not -- so caching here preserves
#: the leakage guarantee while removing the dominant cost of a replay: without
#: it a single scan slot re-reads and re-parses 882 parquet files, and an
#: 8-month run spends hours in pandas I/O re-reading identical bytes.
#:
#: ``_read_raw`` still returns a *copy*, so its original contract holds: callers
#: may mutate what they get back without corrupting later reads. Only the parse
#: is shared, which is the expensive half -- a copy costs microseconds against
#: milliseconds to decode parquet.
_FRAME_CACHE: dict[tuple[str], pd.DataFrame] = {}


def clear_frame_cache() -> None:
    """Drop the parsed-frame cache. For tests that rewrite cache files on disk."""
    _FRAME_CACHE.clear()


def _read_raw(root: Path, ticker: str, timeframe: str) -> pd.DataFrame:
    """Read one ticker's **full, untruncated** history for a stored timeframe.

    Module-private on purpose. Nothing outside this package should call it, and
    nothing at all should call it without immediately applying a point-in-time
    cut — see :func:`backtest.pit._make_truncated_reader`, its only caller in
    the engine.

    Raises ``FileNotFoundError`` if the ticker is not cached for that timeframe,
    and ``ValueError`` for an unknown timeframe.
    """
    path = _timeframe_dir(root, timeframe) / f"{ticker}.parquet"
    key = (str(path),)
    hit = _FRAME_CACHE.get(key)
    if hit is not None:
        return hit.copy()
    if not path.exists():
        raise FileNotFoundError(f"no {timeframe} bars cached for {ticker}: {path}")
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"{path.name}: index is not a DatetimeIndex")
    if df.index.tz is not None:
        raise ValueError(f"{path.name}: index is tz-aware; the cache is US/Eastern wall clock")
    df = df.sort_index()
    df.index.name = "timestamp"
    _FRAME_CACHE[key] = df
    return df.copy()


class BarStore:
    """Read-only owner of the parquet cache.

    Public API is exactly ``tickers``, ``view`` and ``root``. Anything that
    wants bars goes through :meth:`view`, which is truncated at the storage
    boundary.
    """

    def __init__(self, root: Path):
        root = Path(root)
        if not root.is_dir():
            raise FileNotFoundError(f"bar cache root does not exist: {root}")
        self.root = root

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"BarStore({str(self.root)!r})"

    def tickers(self, timeframe: str = "30m") -> list[str]:
        """Sorted symbols cached for *timeframe*."""
        d = _timeframe_dir(self.root, timeframe)
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.parquet"))

    def view(self, as_of) -> "PointInTimeView":
        """A view of the cache frozen at *as_of*.

        Imported lazily so ``store`` does not import ``pit`` at module scope
        while ``pit`` imports :func:`_read_raw` from here.
        """
        from backtest.pit import PointInTimeView

        return PointInTimeView(self.root, as_of)
