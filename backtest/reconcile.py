# backtest/reconcile.py
"""Cross-frame price reconciliation between the 30m and daily bar caches.

The defect
----------
The two frames in the hybrid cache disagree about corporate actions:

* ``data/ohlcv_30m/`` (hfdatalibrary, IEX) is delivered **as traded** — a split
  or spinoff is *not* propagated backwards through the history.
* ``data/ohlcv_daily/`` (yfinance, ``auto_adjust=False``) is nevertheless
  **restated** for splits, so the whole daily series lives on today's scale.

So for any ticker with a corporate action inside the window, the 30m series sits
on one price scale before the action and a different one after, while the daily
series runs continuously. Measured on the real cache as the ratio of the
30m-aggregated daily close to the daily close:

===========  ==================  =============
ticker       before the action   after
===========  ==================  =============
BKNG         24.94               1.00
KLAC          9.99               1.00
CVNA          5.00               1.00
CRWD          4.00               1.00
DD            0.332              1.00
===========  ==================  =============

Anything reading both frames — ``alignment``, an ATR-derived stop taken off
daily and compared to a 30m entry, a gap check — is silently wrong for those
names across the boundary.

A second, much smaller cohort (~33 tickers) shows a single step of 0.9%-1.7%.
That one *is* diagnosed: the step lands exactly on the **first ex-dividend date
inside the requested window**, and its size equals that single dividend divided
by the price, to within a few basis points. Checked on eight names:

=======  ============  ==========  ================  ==================
ticker   step date     step size   first ex-div      dividend / price
=======  ============  ==========  ================  ==================
VZ       2026-01-12    1.708%      2026-01-12        1.732%
MO       2026-03-25    1.615%      2026-03-25        1.662%
KHC      2026-03-06    1.591%      2026-03-06        1.630%
PFE      2026-01-23    1.560%      2026-01-23        1.676%
VICI     2026-03-19    1.538%      2026-03-19        1.608%
TROW     2026-03-16    1.463%      2026-03-16        1.488%
BX       2026-02-09    1.103%      2026-02-09        1.134%
TGT      2026-02-11    0.938%      2026-02-11        0.995%
=======  ============  ==========  ================  ==================

Later ex-dividend dates in the same window produce no step at all, so the 30m
feed is back-adjusted for exactly one dividend — the earliest in the delivered
range — and for none after it. *Why* only the first is affected is a property
of the vendor's pipeline that we have not established and do not guess at; the
empirical pattern above is what the correction relies on, and it does not
depend on knowing the mechanism.

The correction
--------------
Reconciliation is deliberately *empirical*: it never consults a split table. It
measures the per-date ratio between the frames, splits that ratio into
contiguous segments at genuine step changes, and rescales each segment onto the
daily scale by its own median ratio. That catches splits, spinoffs, and whatever
else produces a persistent step, including causes we have not diagnosed.

Two properties matter:

``robust to isolated outliers``
    A single day's ratio can be off because the last 30m bar of a thin session
    is a stale print. A one-day spike must not be mistaken for a corporate
    action, so segmentation runs on a 3-point median filter — which passes a
    true step through unchanged but annihilates a lone spike — and any resulting
    segment shorter than :data:`MIN_SEGMENT_DAYS` is merged away.

``notional preserving``
    Prices are divided by the segment factor, so volume is multiplied by it.
    A 25:1 split quarters-of-a-percent aside, ``price * volume`` across the
    boundary is then continuous, which is what ``vol_ratio``'s 20-period mean
    needs.

The raw download is never touched. :func:`reconcile_all` writes corrected files
to a sibling directory (``data/ohlcv_30m_adj/``) so the correction stays
auditable and reversible.

Read-only with respect to the live scan pipeline: this module imports nothing
from it.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: Relative change in the median-filtered daily ratio that counts as a real
#: step rather than noise.
#:
#: Measured on the real 441-ticker cache. Each ticker has at most one step, so
#: its *second*-largest filtered jump estimates its noise floor: median 0.0019,
#: p95 0.0048, p99 0.0068. The steps we must catch run from 2394% (BKNG) down
#: to 0.94% (TGT's dividend). 0.008 sits above 99% of the noise floor and below
#: every step that matters, and below the 1% drift bar the cache is accepted
#: against. Only 8 of 441 tickers have any second jump above it.
DEFAULT_JUMP_TOL = 0.008

#: Segments shorter than this many observations are merged into a neighbour.
#: A real corporate action separates two multi-week regimes; anything briefer is
#: a data artefact, including a spike sitting on the first or last day where the
#: median filter has no room to work.
MIN_SEGMENT_DAYS = 3

PRICE_COLUMNS = ("Open", "High", "Low", "Close")
VOLUME_COLUMN = "Volume"

RAW_DIR = "ohlcv_30m"
DAILY_DIR = "ohlcv_daily"
ADJ_DIR = "ohlcv_30m_adj"
DEFAULT_REPORT_NAME = "_reconcile_report.csv"

Segment = tuple[pd.Timestamp, pd.Timestamp, float]


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------


def daily_ratio(h30: pd.DataFrame, daily: pd.DataFrame) -> pd.Series:
    """Per-date ratio of the 30m session's last close to the daily close.

    The 30m frame is aggregated to one observation per calendar date (its last
    bar's ``Close``) and divided by the daily frame's ``Close`` for the same
    date. Only dates present in both frames, with a positive daily close,
    appear in the result.

    A flat series at 1.0 means the two frames agree on the price scale. A step
    means a corporate action was propagated into one frame and not the other.
    """
    if h30.empty or daily.empty:
        return pd.Series(dtype="float64", name="ratio")

    intraday = pd.to_numeric(h30["Close"], errors="coerce").dropna()
    if intraday.empty:
        return pd.Series(dtype="float64", name="ratio")
    last_close = intraday.groupby(intraday.index.normalize()).last()

    ref = pd.to_numeric(daily["Close"], errors="coerce").dropna()
    ref = ref[ref > 0]
    ref.index = pd.DatetimeIndex(ref.index).normalize()
    ref = ref[~ref.index.duplicated(keep="last")]

    common = last_close.index.intersection(ref.index)
    if len(common) == 0:
        return pd.Series(dtype="float64", name="ratio")

    ratio = last_close.loc[common] / ref.loc[common]
    ratio = ratio.sort_index()
    ratio.index.name = "date"
    ratio.name = "ratio"
    return ratio.astype("float64")


def _median_filter3(values: np.ndarray) -> np.ndarray:
    """3-point centred median with edge replication.

    Chosen because it is exactly the filter this problem wants: a true step
    ``a a a b b b`` survives with its boundary in place, while an isolated
    spike ``a a s a a`` is erased. Longer windows would start to smear real
    boundaries.
    """
    if values.size < 3:
        return values.copy()
    padded = np.concatenate([values[:1], values, values[-1:]])
    stacked = np.stack([padded[:-2], padded[1:-1], padded[2:]])
    return np.median(stacked, axis=0)


def detect_segments(
    ratio: pd.Series,
    jump_tol: float = DEFAULT_JUMP_TOL,
    min_segment: int = MIN_SEGMENT_DAYS,
) -> list[Segment]:
    """Split *ratio* into contiguous segments at genuine step changes.

    Returns one ``(start_date, end_date, median_ratio)`` tuple per segment, in
    chronological order. ``median_ratio`` is taken over the *raw* ratios inside
    the segment, so it is unaffected by outliers that the filter suppressed.

    A boundary is placed between consecutive observations whose median-filtered
    ratio changes by more than *jump_tol* in relative terms. Segments holding
    fewer than *min_segment* observations are merged into a neighbour, which is
    what stops a spike at the very first or last date — where the median filter
    has no room to work — from inventing a regime.
    """
    ratio = ratio.dropna()
    ratio = ratio[ratio > 0]
    if ratio.empty:
        return []
    if len(ratio) == 1:
        only = pd.Timestamp(ratio.index[0])
        return [(only, only, float(ratio.iloc[0]))]

    values = ratio.to_numpy(dtype="float64")
    smoothed = _median_filter3(values)

    rel_change = np.abs(smoothed[1:] / smoothed[:-1] - 1.0)
    # boundary i means: a new segment starts at position i
    starts = [0, *(int(i) + 1 for i in np.flatnonzero(rel_change > jump_tol))]

    # merge away segments too short to be a real regime
    while len(starts) > 1:
        lengths = [
            (starts[i + 1] if i + 1 < len(starts) else len(values)) - starts[i]
            for i in range(len(starts))
        ]
        shortest = int(np.argmin(lengths))
        if lengths[shortest] >= min_segment:
            break
        # dropping start[0] merges segment 0 forward; dropping start[k] merges
        # segment k backward into k-1
        starts.pop(1 if shortest == 0 else shortest)

    segments: list[Segment] = []
    for i, begin in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(values)
        segments.append(
            (
                pd.Timestamp(ratio.index[begin]),
                pd.Timestamp(ratio.index[end - 1]),
                float(np.median(values[begin:end])),
            )
        )
    return segments


# ---------------------------------------------------------------------------
# correction
# ---------------------------------------------------------------------------


def reconcile_30m(
    h30: pd.DataFrame,
    daily: pd.DataFrame,
    jump_tol: float = DEFAULT_JUMP_TOL,
) -> tuple[pd.DataFrame, dict]:
    """Rescale *h30* onto the daily price scale, segment by segment.

    Every 30m bar is assigned to a segment, prices are divided by that
    segment's median ratio and volume multiplied by it, so ``price * volume``
    notional is preserved and the volume level is continuous across a split
    boundary.

    Bars falling outside the dates the ratio could be measured on — a session
    the daily frame is missing, or bars before/after the daily frame's range —
    are assigned to the nearest segment in time, so no bar is left on the wrong
    scale.

    Returns ``(corrected_frame, report)``. *h30* is never modified in place; the
    returned frame is a fresh copy with the same index and columns.

    ``report`` keys: ``segments`` (the detected segments), ``factors``, the
    convenience counters ``n_segments`` / ``n_ratio_days``, and
    ``max_residual`` — the largest ``|ratio - 1|`` remaining after correction,
    which is the honest measure of whether it worked.
    """
    out = h30.copy(deep=True)
    ratio = daily_ratio(h30, daily)
    segments = detect_segments(ratio, jump_tol=jump_tol)

    report: dict = {
        "segments": segments,
        "factors": [f for _, _, f in segments],
        "n_segments": len(segments),
        "n_ratio_days": int(len(ratio)),
        "max_residual": float("nan"),
        "pre_max_residual": (
            float(np.abs(ratio.to_numpy() - 1.0).max()) if len(ratio) else float("nan")
        ),
    }
    if not segments or out.empty:
        return out, report

    # assign each bar to a segment: interior cut points are the segment starts,
    # with the first segment extended backwards and the last forwards.
    bar_dates = out.index.normalize().to_numpy()
    cuts = np.array([np.datetime64(s) for s, _, _ in segments[1:]])
    which = (
        np.searchsorted(cuts, bar_dates, side="right")
        if cuts.size
        else np.zeros(len(out), dtype="int64")
    )
    factors = np.array([f for _, _, f in segments], dtype="float64")[which]

    for column in PRICE_COLUMNS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").to_numpy() / factors
    if VOLUME_COLUMN in out.columns:
        out[VOLUME_COLUMN] = (
            pd.to_numeric(out[VOLUME_COLUMN], errors="coerce").to_numpy() * factors
        )

    residual = daily_ratio(out, daily)
    if len(residual):
        report["max_residual"] = float(np.abs(residual.to_numpy() - 1.0).max())
        report["median_residual"] = float(np.abs(np.median(residual.to_numpy()) - 1.0))
    return out, report


# ---------------------------------------------------------------------------
# batch driver
# ---------------------------------------------------------------------------


def _drift(ratio: pd.Series, window: int = 20) -> float:
    """Relative drift between the first and last *window* days of a ratio series.

    This is the acceptance metric: a residual corporate action shows up as the
    early median and late median disagreeing, whereas symmetric noise does not.
    """
    if len(ratio) < 2 * window:
        window = max(1, len(ratio) // 2)
    if len(ratio) < 2:
        return float("nan")
    head = float(np.median(ratio.iloc[:window].to_numpy()))
    tail = float(np.median(ratio.iloc[-window:].to_numpy()))
    if tail == 0:
        return float("nan")
    return abs(head / tail - 1.0)


def reconcile_all(
    root: str | Path,
    report_path: str | Path | None = None,
    jump_tol: float = DEFAULT_JUMP_TOL,
) -> dict[str, dict]:
    """Reconcile every 30m parquet under ``<root>/ohlcv_30m`` against daily.

    Corrected files are written to ``<root>/ohlcv_30m_adj/{TICKER}.parquet``.
    ``<root>/ohlcv_30m/`` is left untouched: the raw download is the record of
    what the vendor actually sent, and keeping it makes the correction
    auditable and reversible.

    A ticker with no daily counterpart cannot be reconciled; it is copied
    through unchanged and flagged ``no_daily`` in the report so it is visible
    rather than silently trusted.

    Writes ``<root>/_reconcile_report.csv`` and returns the per-ticker reports.
    """
    root = Path(root)
    raw_dir = root / RAW_DIR
    daily_dir = root / DAILY_DIR
    out_dir = root / ADJ_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if report_path is None:
        report_path = root / DEFAULT_REPORT_NAME
    report_path = Path(report_path)

    results: dict[str, dict] = {}
    rows: list[dict] = []

    for path in sorted(raw_dir.glob("*.parquet")):
        ticker = path.stem.upper()
        status = "reconciled"
        try:
            h30 = pd.read_parquet(path).sort_index()
        except Exception as exc:  # pragma: no cover - corrupt cache
            logger.warning("%s: unreadable 30m parquet: %s", ticker, exc)
            results[ticker] = {"status": "unreadable", "error": str(exc)}
            rows.append({"ticker": ticker, "status": "unreadable", "reason": str(exc)})
            continue

        daily_path = daily_dir / f"{ticker}.parquet"
        if daily_path.exists():
            daily = pd.read_parquet(daily_path).sort_index()
            fixed, report = reconcile_30m(h30, daily, jump_tol=jump_tol)
        else:
            fixed, report = h30.copy(deep=True), {
                "segments": [],
                "factors": [],
                "n_segments": 0,
                "n_ratio_days": 0,
                "max_residual": float("nan"),
                "pre_max_residual": float("nan"),
            }
            status = "no_daily"

        fixed.to_parquet(out_dir / f"{ticker}.parquet", compression="snappy")

        pre_drift = post_drift = float("nan")
        if status == "reconciled":
            pre_drift = _drift(daily_ratio(h30, daily))
            post_drift = _drift(daily_ratio(fixed, daily))
        report["status"] = status
        report["pre_drift"] = pre_drift
        report["post_drift"] = post_drift
        results[ticker] = report

        rows.append(
            {
                "ticker": ticker,
                "status": status,
                "n_segments": report["n_segments"],
                "factors": "|".join(f"{f:.6g}" for f in report["factors"]),
                "boundaries": "|".join(
                    s.date().isoformat() for s, _, _ in report["segments"][1:]
                ),
                "n_ratio_days": report["n_ratio_days"],
                "pre_drift_pct": "" if np.isnan(pre_drift) else f"{pre_drift * 100:.4f}",
                "post_drift_pct": "" if np.isnan(post_drift) else f"{post_drift * 100:.4f}",
                "max_residual_pct": (
                    ""
                    if np.isnan(report.get("max_residual", float("nan")))
                    else f"{report['max_residual'] * 100:.4f}"
                ),
            }
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker",
                "status",
                "n_segments",
                "factors",
                "boundaries",
                "n_ratio_days",
                "pre_drift_pct",
                "post_drift_pct",
                "max_residual_pct",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    multi = sum(1 for r in results.values() if r.get("n_segments", 0) > 1)
    logger.info(
        "reconciled %d tickers into %s (%d with a corporate-action boundary)",
        len(results),
        out_dir,
        multi,
    )
    return results
