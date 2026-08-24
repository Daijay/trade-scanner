# backtest/screen.py
"""Data-integrity screen for the parquet bar cache.

The universe is scraped from *today's* index membership, so it contains symbols
that were reassigned or renamed inside the backtest window. Their cached history
splices two unrelated instruments together, which produces impossible returns
and holes in the middle of the series. Backtesting over such a series manufactures
signal out of a corporate action.

Three rejection rules, each matching a defect observed in the real cache:

``max_bar_return``
    Any single-bar close-to-close return above ``MAX_BAR_RETURN`` (500%).
    BNY shows a +1272% bar where the two instruments are joined.
``interior_gap``
    Any interior hole longer than ``MAX_GAP_DAYS`` (30 calendar days). Leading
    and trailing gaps are not interior and are caught by the bar-count rule
    instead.
``bar_count``
    Fewer than ``MIN_BAR_COUNT_FRACTION`` (50%) of the cohort's median bar
    count — a mid-window IPO, rename, or partial download.

Read-only: this module never modifies the cache and never touches the live scan
pipeline.
"""

from __future__ import annotations

import csv
import logging
import statistics
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

#: Reject a ticker whose largest single-bar return exceeds +500%.
MAX_BAR_RETURN = 5.0

#: Reject a ticker with an interior hole longer than 30 calendar days.
MAX_GAP_DAYS = 30

#: Reject a ticker holding under 50% of the cohort's median bar count.
MIN_BAR_COUNT_FRACTION = 0.5

DEFAULT_REPORT_NAME = "_screen_report.csv"


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"{path.name}: index is not a DatetimeIndex")
    return df.sort_index()


def max_bar_return(df: pd.DataFrame) -> float:
    """Largest single-bar close-to-close return, as a fraction. 0.0 if unmeasurable."""
    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    closes = closes[closes > 0]
    if len(closes) < 2:
        return 0.0
    return float(closes.pct_change().dropna().max() or 0.0)


def max_interior_gap_days(df: pd.DataFrame) -> float:
    """Longest gap in calendar days between consecutive bars. 0.0 if <2 bars."""
    if len(df.index) < 2:
        return 0.0
    deltas = pd.Series(df.index).diff().dropna()
    if deltas.empty:
        return 0.0
    return float(deltas.max().total_seconds() / 86400.0)


def screen_store(
    root: str | Path,
    report_path: str | Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Screen every parquet under *root*.

    Returns ``(clean_tickers, reasons_by_rejected_ticker)`` and writes a CSV
    report — one row per ticker, clean and rejected alike — to *report_path*
    (default ``<root>/../_screen_report.csv``).
    """
    root = Path(root)
    if report_path is None:
        report_path = root.parent / DEFAULT_REPORT_NAME
    report_path = Path(report_path)

    paths = sorted(root.glob("*.parquet"))
    frames: dict[str, pd.DataFrame] = {}
    unreadable: dict[str, str] = {}

    for path in paths:
        ticker = path.stem.upper()
        try:
            frames[ticker] = _load(path)
        except Exception as exc:
            unreadable[ticker] = f"unreadable: {type(exc).__name__}: {exc}"

    counts = [len(df) for df in frames.values() if len(df) > 0]
    median_count = statistics.median(counts) if counts else 0.0
    min_count = median_count * MIN_BAR_COUNT_FRACTION

    rows: list[dict] = []
    rejects: dict[str, str] = {}
    clean: list[str] = []

    for ticker in sorted(set(frames) | set(unreadable)):
        if ticker in unreadable:
            reason = unreadable[ticker]
            rejects[ticker] = reason
            rows.append(
                {
                    "ticker": ticker,
                    "status": "reject",
                    "reason": reason,
                    "rows": 0,
                    "first_bar": "",
                    "last_bar": "",
                    "max_bar_return_pct": "",
                    "max_gap_days": "",
                }
            )
            continue

        df = frames[ticker]
        n = len(df)
        ret = max_bar_return(df)
        gap = max_interior_gap_days(df)

        reason = ""
        if n == 0:
            reason = "empty: no bars"
        elif ret > MAX_BAR_RETURN:
            reason = (
                f"max single-bar return {ret * 100:.0f}% exceeds "
                f"{MAX_BAR_RETURN * 100:.0f}% (spliced instrument or bad print)"
            )
        elif gap > MAX_GAP_DAYS:
            reason = (
                f"interior gap of {gap:.0f} calendar days exceeds {MAX_GAP_DAYS} "
                f"(halt, delisting, or splice)"
            )
        elif n < min_count:
            reason = (
                f"bar count {n} is under {MIN_BAR_COUNT_FRACTION:.0%} of the "
                f"cohort median {median_count:.0f} (late listing, rename, or "
                f"partial download)"
            )

        if reason:
            rejects[ticker] = reason
        else:
            clean.append(ticker)

        rows.append(
            {
                "ticker": ticker,
                "status": "reject" if reason else "clean",
                "reason": reason,
                "rows": n,
                "first_bar": str(df.index.min()) if n else "",
                "last_bar": str(df.index.max()) if n else "",
                "max_bar_return_pct": f"{ret * 100:.2f}" if n else "",
                "max_gap_days": f"{gap:.2f}" if n else "",
            }
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker",
                "status",
                "reason",
                "rows",
                "first_bar",
                "last_bar",
                "max_bar_return_pct",
                "max_gap_days",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    logger.info(
        "screened %s: %d clean, %d rejected (median bar count %.0f)",
        root,
        len(clean),
        len(rejects),
        median_count,
    )
    return clean, rejects
