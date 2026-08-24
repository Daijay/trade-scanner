# backtest/earnings.py
"""Earnings-date cache and blackout-window lookup.

The cache is a flat parquet of ``(ticker, earnings_date)`` rows built from
``yfinance.Ticker(t).get_earnings_dates()``. Tickers with no earnings data —
ETFs, trusts, delisted or renamed symbols — are represented by simply having no
rows; every lookup for them returns an empty list and never raises.

**Methodological limit (see the plan, and Task 12's README):** yfinance returns
the earnings schedule *as it stands today*, not as it was known at the simulated
timestamp. Earnings are scheduled weeks ahead so the leak is small, but it is a
leak. It is documented, not silently accepted.

Nothing here touches the live scan pipeline.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = REPO_ROOT / "data" / "earnings.parquet"

#: yfinance rate-limits aggressively on repeated per-ticker calls.
DEFAULT_SLEEP_SECONDS = 0.3

#: How many earnings rows to request per ticker (past + upcoming).
DEFAULT_LIMIT = 24


class EarningsCalendar:
    """Ticker -> sorted list of earnings dates, with blackout-window lookup."""

    def __init__(self, dates_by_ticker: dict[str, list[date]] | None = None):
        self._dates: dict[str, list[date]] = {}
        for ticker, dates in (dates_by_ticker or {}).items():
            self._dates[ticker.upper()] = sorted({_as_date(d) for d in dates})

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CACHE_PATH) -> "EarningsCalendar":
        """Load a cache written by :func:`build_earnings_cache`.

        A missing or unreadable cache yields an empty calendar rather than an
        exception — a backtest without earnings data should degrade, not crash.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("earnings cache not found at %s; using empty calendar", path)
            return cls({})
        try:
            df = pd.read_parquet(path)
        except Exception as exc:  # pragma: no cover - corrupt file
            logger.warning("could not read earnings cache %s: %s", path, exc)
            return cls({})

        grouped: dict[str, list[date]] = {}
        for ticker, earnings_date in zip(df["ticker"], df["earnings_date"]):
            grouped.setdefault(str(ticker).upper(), []).append(_as_date(earnings_date))
        return cls(grouped)

    # ------------------------------------------------------------------
    # lookup
    # ------------------------------------------------------------------

    def tickers(self) -> list[str]:
        return sorted(self._dates)

    def dates_for(self, ticker: str) -> list[date]:
        """Earnings dates for *ticker*, sorted ascending. ``[]`` if unknown."""
        return list(self._dates.get(str(ticker).upper(), []))

    def in_blackout(
        self,
        ticker: str,
        on_date: date,
        days_before: int,
        days_after: int,
    ) -> bool:
        """True if *on_date* falls within ``[e - days_before, e + days_after]``
        of any known earnings date ``e`` for *ticker*.

        Bounds are inclusive on both sides, and the earnings day itself is
        always inside the window.
        """
        on = _as_date(on_date)
        before = timedelta(days=abs(int(days_before)))
        after = timedelta(days=abs(int(days_after)))
        for earnings in self._dates.get(str(ticker).upper(), []):
            if earnings - before <= on <= earnings + after:
                return True
        return False

    def to_frame(self) -> pd.DataFrame:
        rows = [
            {"ticker": ticker, "earnings_date": pd.Timestamp(d)}
            for ticker, dates in sorted(self._dates.items())
            for d in dates
        ]
        return pd.DataFrame(rows, columns=["ticker", "earnings_date"])


# ----------------------------------------------------------------------
# cache build
# ----------------------------------------------------------------------

def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.date()


def fetch_earnings_dates(ticker: str, limit: int = DEFAULT_LIMIT) -> list[date]:
    """Earnings dates for one ticker from yfinance. Raises on transport error."""
    frame = yf.Ticker(ticker).get_earnings_dates(limit=limit)
    if frame is None or len(frame) == 0:
        return []
    index = frame.index
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    return sorted({_as_date(ts) for ts in index})


def build_earnings_cache(
    tickers: list[str],
    out_path: str | Path = DEFAULT_CACHE_PATH,
    limit: int = DEFAULT_LIMIT,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> tuple[EarningsCalendar, dict[str, str]]:
    """Fetch earnings dates for *tickers* and cache them to *out_path*.

    Returns ``(calendar, failures)`` where ``failures`` maps ticker -> error
    string. A ticker that legitimately has no earnings (an ETF) is **not** a
    failure: it simply contributes no rows.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dates_by_ticker: dict[str, list[date]] = {}
    failures: dict[str, str] = {}

    for i, ticker in enumerate(tickers):
        try:
            dates = fetch_earnings_dates(ticker, limit=limit)
        except Exception as exc:
            failures[ticker.upper()] = f"{type(exc).__name__}: {exc}"
            logger.warning("earnings fetch failed for %s: %s", ticker, exc)
        else:
            if dates:
                dates_by_ticker[ticker.upper()] = dates
        if sleep_seconds and i < len(tickers) - 1:
            time.sleep(sleep_seconds)

    calendar = EarningsCalendar(dates_by_ticker)
    calendar.to_frame().to_parquet(out_path, index=False, compression="snappy")
    logger.info(
        "earnings cache written to %s: %d tickers with dates, %d without, %d failures",
        out_path,
        len(dates_by_ticker),
        len(tickers) - len(dates_by_ticker) - len(failures),
        len(failures),
    )
    return calendar, failures
