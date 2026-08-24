# backtest/calendar.py
"""The simulation clock: the instants at which a replayed scan runs.

Two independent facts define a slot, and both are read rather than invented.

**Which days.** Trading days are derived from the *data* — the distinct dates
present in a liquid reference ticker's daily parquet (AAPL). A hardcoded
holiday table is a second source of truth that drifts: it needs a yearly edit,
it says nothing about half-days or feed outages, and when it disagrees with the
cache the cache wins anyway, because the engine can only ever see bars that are
actually there. Deriving from the cache makes the calendar and the bars
incapable of disagreeing. ``config.MARKET_HOLIDAYS`` still exists for the live
scanner's own guards; this module cross-checks against it in tests but never
reads it.

**Which times.** Slot times come from ``config.SCAN_TIMES_PT`` so a simulated
scan lands exactly where a live scan lands — the whole point of the backtest is
comparability with the live journal, and a replay that scanned at times the
live bot never scans would measure a different strategy. The times are
converted from ``config.MARKET_TZ`` (Pacific) into US/Eastern **per date**, so
the two zones' DST transitions are handled properly rather than assumed to be a
fixed offset.

Timestamps are returned tz-naive in US/Eastern wall clock, which is the
convention the parquet cache and :class:`~backtest.pit.PointInTimeView` use.

Note on look-ahead: this module reads full daily history, not a truncated view.
That is deliberate and is not a leak — the *schedule* of trading sessions is
public knowledge weeks ahead, and the live scanner likewise knows on Friday
that Monday is a holiday. Nothing here returns prices.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

import pandas as pd

import config
from backtest.store import _read_raw

#: Liquid, continuously listed name whose daily bars define the session calendar.
REFERENCE_TICKER = "AAPL"

#: The cache's wall clock. Slots are emitted tz-naive in this zone.
CACHE_TZ = ZoneInfo("America/New_York")

DEFAULT_ROOT = "data"


def _scan_times_et(on: date) -> list[time]:
    """``config.SCAN_TIMES_PT`` as US/Eastern wall-clock times **on that date**.

    Converted per date rather than by a fixed offset: Pacific and Eastern shift
    on the same days in the US, but that is a property of current law, not an
    invariant, and the conversion costs nothing.
    """
    market_tz = ZoneInfo(config.MARKET_TZ)
    out = []
    for t in config.SCAN_TIMES_PT.values():
        aware = pd.Timestamp(
            year=on.year, month=on.month, day=on.day,
            hour=t.hour, minute=t.minute, tz=market_tz,
        )
        out.append(aware.tz_convert(CACHE_TZ).time())
    return sorted(set(out))


def _bounds(start, end) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Normalise the range. A bare date as *end* means the whole of that day."""
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end)
    if hi == hi.normalize():
        hi = hi + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return lo, hi


def trading_days(
    start,
    end,
    root: str | Path = DEFAULT_ROOT,
    ticker: str = REFERENCE_TICKER,
) -> list[pd.Timestamp]:
    """Sessions between *start* and *end* inclusive, midnight-normalised.

    Weekends and market holidays fall out for free: they are simply dates with
    no bar in the reference ticker's daily frame.

    Raises ``FileNotFoundError`` if *ticker* has no daily bars cached — an
    empty calendar would silently produce a backtest over zero slots, which
    looks like "the strategy found nothing" rather than "the data is missing".
    """
    lo, hi = _bounds(start, end)
    df = _read_raw(Path(root), ticker, "daily")
    idx = df.index.normalize()
    days = pd.DatetimeIndex(sorted(set(idx[(idx >= lo.normalize()) & (idx <= hi)])))
    return list(days)


def scan_slots(
    start,
    end,
    root: str | Path = DEFAULT_ROOT,
    ticker: str = REFERENCE_TICKER,
) -> Iterator[pd.Timestamp]:
    """Yield each simulated scan instant in ``[start, end]``, in order.

    One slot per entry in ``config.SCAN_TIMES_PT`` per trading day. Slots
    outside the requested range are not emitted even when their day is in
    range, so a range ending mid-session stops mid-session.

    Lazy on purpose: a multi-year run is tens of thousands of slots, each of
    which builds a ``PointInTimeView``, and the engine consumes them one at a
    time.
    """
    lo, hi = _bounds(start, end)
    for day in trading_days(start, end, root=root, ticker=ticker):
        for t in _scan_times_et(day.date()):
            slot = day + pd.Timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)
            if lo <= slot <= hi:
                yield slot


__all__ = ["scan_slots", "trading_days", "REFERENCE_TICKER"]
