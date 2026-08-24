# backtest/strategies/v1_technical.py
"""v1's live technical filter, replayed against a point-in-time view.

This module **imports and calls** ``filter.py`` and ``indicators.py``; it edits
neither. Every hard reject, every survivor score and every alignment count is
the live code's own, computed on frames the view has already truncated. If the
live filter changes, this strategy changes with it, which is the point: the
backtest must measure the scanner that exists, not a re-implementation of it.

What this replays faithfully
----------------------------
``filter.run_filter`` end to end — ``passes_hard_filter``'s liquidity, price,
ATR% and alignment gates, ``score_survivor``'s ranking, and the
``config.MAX_SURVIVORS`` cap (applied inside ``run_filter``, not here).

What it does **not** replay, and why
------------------------------------
**Conviction is a proxy, not a model output.** Live conviction comes from
``analyst.py``, which sends each survivor's indicator snapshot *plus its recent
news headlines* to Claude. No free historical news archive covers Jan-Aug 2026,
so news is a documented scope exclusion for the whole engine — and without it
``analyst.py`` cannot be replayed at all.

In its place, each signal's conviction is the survivor's **rank by
``score_survivor`` within its own scan slot**, mapped linearly onto 0-10 with
the top-ranked name at 10. It is an ordering of technical attractiveness inside
one slot, nothing more. It is not calibrated, not comparable across slots (the
top name in a thin slot gets the same 10 as the top name in a strong one), and
carries no news or judgement. Two guards make it hard to mistake for the real
thing: ``CONVICTION_SOURCE`` is exported for the report to print, and every
signal's ``reason`` string opens with ``RANK-PROXY CONVICTION``.

**Entry, stop and target are mechanical.** Live, Claude picks them within an
ATR band. Here entry is the last *daily* close and the stop is
:data:`STOP_ATR_MULT` x the **daily** ``atr14``, on both horizons.

Daily ATR specifically, including for intraday-horizon signals where
``analyst.py``'s prompt would use the 30m ``atr14``. The daily frame is the one
sourced from consolidated yfinance data; the 30m frame is IEX-only (~2-3% of
consolidated volume), so its ATR is measured off a different tape and would
size stops from a distorted range. Stop distance is the denominator of every
realized R multiple in the report, so it is the one number worth deliberately
sourcing from the better feed even at the cost of exactness against live.

Target is placed at exactly ``config.MIN_RR``, so every signal clears the live
floor by construction and ``rr`` is constant across the run. That makes
``avg_rr`` in the report a function of hit rate alone, which is a limitation to
state rather than a result to celebrate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

import config
import filter as v1_filter
from backtest.strategy import Signal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backtest.pit import PointInTimeView

logger = logging.getLogger(__name__)

#: Stop distance in daily ATRs. ``analyst.py``'s prompt gives Claude the band
#: "roughly 1.5x to 2.5x that atr14"; 2.0 is its midpoint, chosen so the
#: mechanical stop sits where the live instruction centres rather than at
#: either edge of what it permits.
STOP_ATR_MULT = 2.0

#: Printed by the report next to every conviction number. See the module
#: docstring: this is a within-slot rank, not ``analyst.py``'s judgement.
CONVICTION_SOURCE = (
    "rank proxy: score_survivor rank within the scan slot, mapped 0-10 "
    "(top-ranked = 10). NOT analyst.py model conviction; no news input."
)

#: Prefix on every ``Signal.reason``, so the proxy is visible in any dump of
#: the signals themselves and not only in the report header.
_REASON_PREFIX = "RANK-PROXY CONVICTION"

#: Trend label -> trade direction. ``analysis["alignment"]`` is non-zero only
#: when every non-neutral timeframe agrees, so a survivor has exactly one.
_BIAS_BY_TREND = {"bullish": "long", "bearish": "short"}

#: Bars of *already-truncated* history handed to ``indicators.analyze_symbol``,
#: per timeframe. Purely a cost bound, applied strictly **after** the
#: point-in-time cut: taking a tail can only ever discard older rows, so it
#: cannot leak — the leakage guarantee lives entirely in ``pit.py`` and is
#: untouched here.
#:
#: Why it exists. ``compute_indicators`` builds full indicator *series* over
#: whatever frame it is given and reads only ``.iloc[-1]``. By Aug 2026 the 30m
#: frame is ~5,300 rows, so every slot computed ema9/21/50/200, RSI, MACD,
#: Bollinger, ATR, ADX and 20-period rolling stats across thousands of bars, for
#: 441 tickers x 3 timeframes, to read one value each. Measured: ~53 s per slot,
#: ~4.7 h for the 2026 replay.
#:
#: Why these numbers. Every window in use is <= 200 bars; 1,000 gives ema200 5x
#: its window to converge (EMA error decays geometrically) and everything else
#: 50x or more. Validated rather than assumed: on 30 real tickers x 2 instants
#: (60 pairs), full-frame vs bounded ``compute_indicators`` agreed on every
#: field to <= 9.0e-06 relative (worst: 30m ``ema200``, which no gate reads),
#: with **zero** differences in ``classify_trend``, ``alignment`` or
#: ``passes_hard_filter`` outcomes. Do not lower these without re-running that
#: check — a faster backtest that returns different answers is worthless.
INDICATOR_LOOKBACK: dict[str, int] = {"30m": 1000, "4h": 1000, "daily": 800}


def _bound(frames: dict, lookback: dict[str, int] | None) -> dict:
    """Last *lookback[tf]* rows of each frame. ``None`` disables the bound."""
    if not lookback:
        return frames
    return {
        tf: (df.tail(lookback[tf]) if tf in lookback else df)
        for tf, df in frames.items()
    }


def _rank_conviction(rank: int, total: int) -> int:
    """Rank 0 (best score in the slot) -> 10, worst -> 0, linear between.

    A single survivor scores 10: it is the best name in its slot, and mapping
    it to 0 for lack of anything to compare against would be perverse.
    """
    if total <= 1:
        return 10
    return int(round(10.0 * (total - 1 - rank) / (total - 1)))


def _bias_for(analysis: dict) -> str | None:
    directions = {t for t in analysis["trends"].values() if t != "neutral"}
    if len(directions) != 1:
        return None
    return _BIAS_BY_TREND.get(directions.pop())


class V1Technical:
    """The live v1 technical filter as a backtest :class:`~backtest.strategy.Strategy`."""

    name = "v1_technical"

    #: Re-exported so a report can print it without importing module internals.
    conviction_source = CONVICTION_SOURCE

    def __init__(
        self,
        stop_atr_mult: float = STOP_ATR_MULT,
        lookback: dict[str, int] | None = None,
    ):
        self.stop_atr_mult = stop_atr_mult
        #: See :data:`INDICATOR_LOOKBACK`. Pass ``{}`` to disable the bound.
        self.lookback = INDICATOR_LOOKBACK if lookback is None else lookback
        #: Per-slot bookkeeping from the most recent :meth:`generate`. Read by
        #: the engine's run summary; a missing-history count of zero is itself
        #: a finding, and one silently swallowed would hide a broken cache.
        self.last_run: dict = {}
        #: Cumulative across every slot this instance has generated for.
        self.totals: dict[str, int] = {
            "slots": 0,
            "requested": 0,
            "scanned": 0,
            "missing_history": 0,
            "survivors": 0,
            "signals": 0,
            "unusable_geometry": 0,
        }

    # ------------------------------------------------------------------

    def generate(self, view: "PointInTimeView", universe: list[str]) -> list[Signal]:
        """Signals for one scan slot.

        A ticker with no cached history for some timeframe is skipped and
        counted, never raised: 59 of the 441 universe names have daily bars but
        no 30m history, and a slot that died on the first of them would report
        zero signals for the day rather than an error.
        """
        frames_by_ticker: dict[str, dict] = {}
        missing: list[str] = []

        for ticker in universe:
            try:
                frames_by_ticker[ticker] = _bound(view.frames_for(ticker), self.lookback)
            except FileNotFoundError:
                missing.append(ticker)
            except Exception:
                # Same containment rationale as filter.run_filter's own
                # try/except: one malformed name must not cost the slot.
                logger.warning("skipping %s: frame build failed", ticker, exc_info=True)
                missing.append(ticker)

        survivors, _filtered_out = v1_filter.run_filter(frames_by_ticker)

        signals: list[Signal] = []
        unusable = 0
        total = len(survivors)
        for rank, survivor in enumerate(survivors):
            signal = self._to_signal(survivor, rank, total, view)
            if signal is None:
                unusable += 1
                continue
            signals.append(signal)

        self.last_run = {
            "as_of": view.as_of,
            "requested": len(universe),
            "scanned": len(frames_by_ticker),
            "missing_history": len(missing),
            "missing_tickers": missing,
            "survivors": total,
            "signals": len(signals),
            "unusable_geometry": unusable,
        }
        self.totals["slots"] += 1
        self.totals["requested"] += len(universe)
        self.totals["scanned"] += len(frames_by_ticker)
        self.totals["missing_history"] += len(missing)
        self.totals["survivors"] += total
        self.totals["signals"] += len(signals)
        self.totals["unusable_geometry"] += unusable
        return signals

    # ------------------------------------------------------------------

    def _to_signal(self, survivor: dict, rank: int, total: int, view) -> Signal | None:
        ticker = survivor["symbol"]
        analysis = survivor["analysis"]
        daily = analysis["snapshots"]["daily"]

        bias = _bias_for(analysis)
        if bias is None:
            logger.warning("%s: survivor with no single direction; skipping", ticker)
            return None

        entry = float(daily["close"])
        atr = float(daily["atr14"])
        if not np.isfinite(entry) or not np.isfinite(atr) or atr <= 0 or entry <= 0:
            logger.warning("%s: unusable daily close/atr14 (%r/%r); skipping", ticker, entry, atr)
            return None

        risk = self.stop_atr_mult * atr
        if bias == "long":
            stop, target = entry - risk, entry + config.MIN_RR * risk
        else:
            stop, target = entry + risk, entry - config.MIN_RR * risk
        if stop <= 0 or target <= 0:
            # A 2-ATR stop below entry can go negative on a name whose daily
            # ATR is a large fraction of its price. Such a stop cannot be hit,
            # so the trade would resolve as a guaranteed win or a scratch.
            logger.warning("%s: non-positive stop/target (%r/%r); skipping", ticker, stop, target)
            return None
        rr = abs(target - entry) / abs(entry - stop)

        alignment = analysis["alignment"]
        horizon = config.HORIZON_BY_ALIGNMENT.get(alignment)
        if horizon is None:
            logger.warning("%s: alignment %r has no horizon; skipping", ticker, alignment)
            return None

        conviction = _rank_conviction(rank, total)
        trends = analysis["trends"]
        reason = (
            f"{_REASON_PREFIX} {conviction}/10 = rank {rank + 1} of {total} by score_survivor "
            f"({survivor['score']:.1f}) at {view.as_of}, NOT analyst.py conviction. "
            f"alignment {alignment} ("
            + ", ".join(f"{tf}={trends[tf]}" for tf in ("30m", "4h", "daily") if tf in trends)
            + f"); stop = {self.stop_atr_mult:g}x daily atr14 {atr:.2f}; "
            f"target at MIN_RR {config.MIN_RR:g}"
        )

        return Signal(
            ticker=ticker,
            bias=bias,
            entry=entry,
            stop=stop,
            target=target,
            rr=rr,
            horizon=horizon,
            conviction=conviction,
            reason=reason,
        )


__all__ = ["V1Technical", "STOP_ATR_MULT", "CONVICTION_SOURCE", "INDICATOR_LOOKBACK"]
