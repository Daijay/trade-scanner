# backtest/engine.py
"""The simulation loop, and the gate that makes its output comparable to live.

clock -> point-in-time view -> strategy -> **technical proxy gate** -> journal
records -> resolution against later views.

Why a gate exists at all
------------------------
``filter.run_filter`` caps every scan slot at ``config.MAX_SURVIVORS`` (30), and
in the real 2026 cache that cap **binds on every single slot**: the replay
produces exactly 30 signals per slot, ~1,300 a month. The live journal fired
~560 alerts in three weeks. Those are not the same population, and hit rates
computed over them are not comparable — which would defeat the entire reason
for routing this module's arithmetic through ``journal.py``.

Live, survivors do not become alerts directly. They go::

    filter.run_filter -> analyst.py (Claude conviction, WITH news)
                      -> conviction >= config.MIN_CONVICTION
                      -> sort by conviction -> [: config.MAX_ALERTS]

The backtest has no analyst by design: ``analyst.py`` needs news headlines, and
no free historical news archive covers Jan-Aug 2026. See :data:`PROXY_GATE_NAME`
and ``backtest/report.py``'s ``PROXY_GATE_CAVEAT`` for exactly what stands in
for it and what that does and does not buy.

Resolution
----------
Outcomes come from ``journal.resolve_alert`` and rates from
``journal.compute_stats``/``journal._rate_block`` — the live functions,
unmodified. Nothing in ``backtest/`` decides what a win is.

Resolution mirrors ``main.resolve_previous_alerts`` exactly: at each slot, every
still-open alert is fed the 30m bars **strictly after the previous slot**, read
from a view built at *that* slot. Two consequences, both deliberate:

* a signal is never resolved against a bar its generating view could see, and
* ``bars_open`` accumulates in the same chunks live accumulates it in, so
  ``config.SCRATCH_AFTER_BARS`` scratches at the same point it does live.
  Feeding all post-entry bars at once instead would scratch everything that had
  not already hit, at a different time and for a different reason.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

import config
import journal
from backtest.market_calendar import scan_slots
from backtest.store import BarStore
from backtest.strategy import Signal

logger = logging.getLogger(__name__)

#: The gate's name, deliberately not containing the word "conviction".
#: Asserted in ``tests/backtest/test_report.py``: a reader skimming a stack
#: trace, a log line or the report must not be able to mistake a rank-based
#: volume cap for ``analyst.py``'s judgement.
PROXY_GATE_NAME = "technical_proxy_gate"

#: The timeframe resolution walks. Matches ``main.resolve_previous_alerts``,
#: which resolves live alerts on 30m bars.
RESOLUTION_TIMEFRAME = "30m"

_CACHE_TZ = ZoneInfo("America/New_York")


# --------------------------------------------------------------- the gate


def technical_proxy_gate(
    signals: Sequence[Signal], max_alerts: int | None = None
) -> list[Signal]:
    """Keep the best ``config.MAX_ALERTS`` signals of one scan slot.

    **This is a technical proxy gate. It is not conviction.** It stands where
    ``analyst.py`` stands live, and it reproduces one property of that stage and
    one only: *how many* alerts a slot emits. It reproduces nothing about *which*
    ones, because the input it would need — Claude's reading of each survivor's
    news — does not exist for this date range.

    Ranking is by ``filter.score_survivor``, reached through the strategy's
    conviction field: ``run_filter`` returns survivors sorted by score
    descending and ``V1Technical`` maps that rank monotonically onto 0-10, so
    ordering by conviction *is* ordering by ``score_survivor``. The sort is
    stable, so signals tied on the rounded rank keep the strategy's own
    score order.

    ``config.MIN_CONVICTION`` is deliberately **not** applied. Live it is a
    threshold on a calibrated 0-10 judgement; here the same numbers are an
    ordinal rank within a slot, and a rank of 6 carries no claim about quality
    at all. Thresholding it would look like the live floor while meaning
    something entirely different. The cap alone provides the volume match.
    """
    limit = config.MAX_ALERTS if max_alerts is None else max_alerts
    ranked = sorted(signals, key=lambda s: s.conviction, reverse=True)  # stable
    return ranked[:limit]


# ------------------------------------------------------------ journal glue


def scan_label(slot) -> str:
    """The live scan label (``premarket``/``preclose``) a slot corresponds to.

    ``config.SCAN_TIMES_PT`` is Pacific; slots are US/Eastern wall clock. The
    conversion is done per date so DST is handled rather than assumed, matching
    ``backtest.market_calendar``.
    """
    ts = pd.Timestamp(slot)
    market_tz = ZoneInfo(config.MARKET_TZ)
    best, best_delta = None, None
    for label, t in config.SCAN_TIMES_PT.items():
        aware = pd.Timestamp(
            year=ts.year, month=ts.month, day=ts.day,
            hour=t.hour, minute=t.minute, tz=market_tz,
        ).tz_convert(_CACHE_TZ)
        delta = abs((ts - aware.tz_localize(None)).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = label, delta
    return best


def signal_to_alert(signal: Signal, slot, scan: str, strategy: str = "") -> dict:
    """One journal-shaped record.

    The field set is ``journal.log_alerts``' output verbatim — checked in
    ``tests/backtest/test_report.py`` — so ``journal.resolve_alert`` and
    ``journal.compute_stats`` accept it unchanged. ``log_alerts`` itself is not
    called because it writes ``journal.json``: a backtest must never touch the
    live paper-trading record.

    Two backtest-only keys ride along and are ignored by ``journal.py``:
    ``strategy`` (report segmentation) and ``reason`` (audit trail).
    """
    ts = pd.Timestamp(slot)
    when = ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts
    return {
        "id": f"{ts.strftime('%Y-%m-%dT%H:%M')}-{signal.ticker}",
        "timestamp": when.isoformat() if isinstance(when, datetime) else str(when),
        "scan": scan,
        "ticker": signal.ticker,
        "bias": signal.bias,
        "conviction": signal.conviction,
        "entry": signal.entry,
        "stop": signal.stop,
        "target": signal.target,
        "rr": signal.rr,
        "horizon": signal.horizon,
        "alerted": True,
        "status": "open",
        "position": None,
        "resolved_at": None,
        "outcome": None,
        "bars_open": 0,
        # backtest-only
        "strategy": strategy,
        "reason": signal.reason,
    }


# ---------------------------------------------------------------- the loop


def _resolve_open(alerts: list[dict], view, since: pd.Timestamp) -> None:
    """Feed each open alert the bars in ``(since, view.as_of]``.

    A ticker with no cached bars is skipped, exactly as
    ``journal.resolve_open_alerts`` skips one missing from ``bars_by_ticker``:
    an alert whose data went missing must stay open, not silently scratch.
    """
    open_alerts = [a for a in alerts if a["status"] == "open"]
    if not open_alerts:
        return
    bars_by_ticker: dict[str, pd.DataFrame] = {}
    for ticker in {a["ticker"] for a in open_alerts}:
        try:
            df = view.bars(ticker, RESOLUTION_TIMEFRAME)
        except (FileNotFoundError, KeyError):
            continue
        except Exception:
            logger.warning("resolution read failed for %s", ticker, exc_info=True)
            continue
        bars_by_ticker[ticker] = df.loc[df.index > since]
    journal.resolve_open_alerts(open_alerts, bars_by_ticker)


def run_backtest(
    strategy,
    start=None,
    end=None,
    root: str = "data",
    universe: Sequence[str] | None = None,
    slots: Iterable | None = None,
    store: BarStore | None = None,
    progress=None,
) -> dict:
    """Replay *strategy* over every scan slot in ``[start, end]``.

    Returns the run's records and metadata; rendering is ``backtest.report``'s
    job. ``slots`` and ``store`` exist so tests can drive the loop without a
    parquet cache — production callers pass ``start``/``end``.
    """
    t0 = time.time()
    store = store or BarStore(root)
    slot_list = list(slots) if slots is not None else list(scan_slots(start, end, root=root))
    if universe is None:
        universe = store.tickers("30m")
    universe = list(universe)

    alerts: list[dict] = []
    before_gate = 0
    previous_slot = None

    for i, slot in enumerate(slot_list):
        view = store.view(slot)

        # Resolve first, then scan -- the order main.py runs them in.
        if previous_slot is not None:
            _resolve_open(alerts, view, previous_slot)

        signals = strategy.generate(view, universe)
        before_gate += len(signals)
        kept = technical_proxy_gate(signals)
        label = scan_label(slot)
        alerts.extend(
            signal_to_alert(s, slot, label, strategy=getattr(strategy, "name", ""))
            for s in kept
        )

        previous_slot = slot
        if progress is not None:
            progress(i + 1, len(slot_list), slot, len(alerts))

    return {
        "strategy": getattr(strategy, "name", ""),
        "start": str(slot_list[0]) if slot_list else str(start),
        "end": str(slot_list[-1]) if slot_list else str(end),
        "slots": len(slot_list),
        "universe_size": len(universe),
        "signals_before_gate": before_gate,
        "signals_after_gate": len(alerts),
        "gate": PROXY_GATE_NAME,
        "max_alerts": config.MAX_ALERTS,
        "max_survivors": config.MAX_SURVIVORS,
        "alerts": alerts,
        "conviction_source": getattr(strategy, "conviction_source", "unspecified"),
        "strategy_totals": dict(getattr(strategy, "totals", {}) or {}),
        "wall_clock_s": time.time() - t0,
    }


__all__ = [
    "run_backtest",
    "technical_proxy_gate",
    "signal_to_alert",
    "scan_label",
    "PROXY_GATE_NAME",
    "RESOLUTION_TIMEFRAME",
]
