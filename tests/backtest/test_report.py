"""Task 11: the technical-proxy gate, resolution through journal.py, and the report.

Two things are being pinned here, and they are the two things that make the
backtest's numbers mean anything:

1. **Stats come from ``journal.py``, not from a re-implementation.** Every
   assertion about hit_rate / adj_hit_rate / scratch_rate / avg_rr is checked
   against ``journal.compute_stats`` on equivalent records, so if this module
   ever grew its own outcome arithmetic the test would catch the divergence.

2. **The gate is a volume proxy, and is labelled as one.** It caps a slot at
   ``config.MAX_ALERTS`` and keeps the highest ``score_survivor`` names, so the
   replay's alert volume is comparable to live. It is emphatically *not*
   ``analyst.py`` conviction, and the tests assert the report says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
import journal
from backtest.engine import (
    PROXY_GATE_NAME,
    scan_label,
    signal_to_alert,
    technical_proxy_gate,
)
from backtest.report import PROXY_GATE_CAVEAT, render_report, segment_stats
from backtest.strategy import Signal

AS_OF = pd.Timestamp("2026-03-16 09:00")


def _signal(ticker: str, conviction: int = 5, entry: float = 100.0) -> Signal:
    return Signal(
        ticker=ticker,
        bias="long",
        entry=entry,
        stop=entry - 2.0,
        target=entry + 3.0,
        rr=1.5,
        horizon="swing",
        conviction=conviction,
        reason=f"RANK-PROXY CONVICTION {conviction}/10",
    )


def _bars(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """(high, low, close) rows on a 30m index starting after AS_OF."""
    idx = pd.date_range(AS_OF + pd.Timedelta(minutes=30), periods=len(rows), freq="30min")
    return pd.DataFrame(
        {
            "Open": [c for _, _, c in rows],
            "High": [h for h, _, _ in rows],
            "Low": [lo for _, lo, _ in rows],
            "Close": [c for _, _, c in rows],
            "Volume": np.full(len(rows), 1_000_000.0),
        },
        index=idx,
    )


# ------------------------------------------------------------ the proxy gate


def test_gate_caps_at_max_alerts_and_keeps_the_best_scores():
    """The whole point: live volume, not live selection.

    ``filter.run_filter`` returns survivors already sorted by
    ``score_survivor`` descending, and ``V1Technical`` maps that rank onto
    conviction, so keeping the highest convictions keeps the highest scores.
    """
    signals = [_signal(f"T{i}", conviction=c) for i, c in enumerate([10, 9, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0])]
    assert len(signals) > config.MAX_ALERTS, "fixture must exercise the cap"

    kept = technical_proxy_gate(signals)

    assert len(kept) == config.MAX_ALERTS
    assert [s.conviction for s in kept] == sorted(
        (s.conviction for s in signals), reverse=True
    )[: config.MAX_ALERTS]
    assert [s.ticker for s in kept] == ["T0", "T1", "T2", "T3", "T4", "T5", "T6", "T7"]


def test_gate_reads_max_alerts_from_config(monkeypatch):
    monkeypatch.setattr(config, "MAX_ALERTS", 3)
    kept = technical_proxy_gate([_signal(f"T{i}", conviction=10 - i) for i in range(9)])
    assert len(kept) == 3


def test_gate_is_stable_within_a_conviction_tie():
    """Ties keep the strategy's own order, which is score_survivor order."""
    signals = [_signal("A", 7), _signal("B", 7), _signal("C", 7)]
    assert [s.ticker for s in technical_proxy_gate(signals)[:3]] == ["A", "B", "C"]


def test_gate_passes_through_when_under_the_cap():
    signals = [_signal("A", 7), _signal("B", 6)]
    assert technical_proxy_gate(signals) == signals


def test_gate_is_not_named_conviction():
    """A reader must never mistake this for analyst.py's judgement."""
    assert "conviction" not in PROXY_GATE_NAME.lower()
    assert PROXY_GATE_NAME == "technical_proxy_gate"


# ------------------------------------------------------------ alert records


def test_signal_to_alert_produces_a_journal_shaped_record():
    alert = signal_to_alert(_signal("AAPL", 9), AS_OF, "premarket")
    reference = journal.log_alerts.__doc__  # smoke: the function we are shaped for
    assert reference
    for key in (
        "id", "timestamp", "scan", "ticker", "bias", "conviction", "entry",
        "stop", "target", "rr", "horizon", "alerted", "status", "position",
        "resolved_at", "outcome", "bars_open",
    ):
        assert key in alert, key
    assert alert["status"] == "open"
    assert alert["scan"] == "premarket"
    assert alert["ticker"] == "AAPL"


def test_scan_label_maps_slots_onto_live_labels():
    slots = sorted({scan_label(pd.Timestamp(f"2026-03-16 {h}")) for h in ("09:00", "15:30")})
    assert slots == ["preclose", "premarket"]


# ------------------------------------------------- plan Task 11 Step 1 test


def test_four_signals_give_hit_rate_half_and_scratch_rate_one_third():
    """1 win, 1 loss, 1 scratch, 1 still open -> hit_rate 0.5, scratch_rate 1/3.

    Resolution goes through ``journal.resolve_alert`` and the rates through
    ``journal.compute_stats``; nothing here computes an outcome itself.
    """
    winner = signal_to_alert(_signal("WIN", 10), AS_OF, "premarket")
    loser = signal_to_alert(_signal("LOSE", 9), AS_OF, "premarket")
    scratch = signal_to_alert(_signal("SCRATCH", 8), AS_OF, "premarket")
    still_open = signal_to_alert(_signal("OPEN", 7), AS_OF, "premarket")

    journal.resolve_alert(winner, _bars([(101, 99, 100), (104, 100, 103)]))
    journal.resolve_alert(loser, _bars([(101, 99, 100), (100, 97, 98)]))
    # Never touches 103 or 98; more bars than SCRATCH_AFTER_BARS.
    flat = _bars([(100.5, 99.5, 100.0)] * (config.SCRATCH_AFTER_BARS + 1))
    journal.resolve_alert(scratch, flat)
    journal.resolve_alert(still_open, _bars([(100.5, 99.5, 100.0)]))

    assert winner["outcome"] == "win"
    assert loser["outcome"] == "loss"
    assert scratch["outcome"] == "scratch"
    assert still_open["status"] == "open"

    alerts = [winner, loser, scratch, still_open]
    stats = journal.compute_stats(alerts)
    assert stats["hit_rate"] == pytest.approx(0.5)
    assert stats["scratch_rate"] == pytest.approx(1 / 3)

    overall = segment_stats(alerts)["overall"]
    assert overall["hit_rate"] == pytest.approx(0.5)
    assert overall["scratch_rate"] == pytest.approx(1 / 3)
    assert overall["adj_hit_rate"] == pytest.approx(stats["adj_hit_rate"])
    assert overall["avg_rr"] == pytest.approx(stats["avg_rr"])
    assert overall["wins"] == 1 and overall["losses"] == 1 and overall["scratches"] == 1
    assert overall["open"] == 1


# ------------------------------------------------------------ segmentation


def _resolved(ticker: str, month: str, outcome: str, strategy: str = "v1_technical") -> dict:
    alert = signal_to_alert(_signal(ticker, 8), pd.Timestamp(f"{month}-16 09:00"), "premarket")
    alert["strategy"] = strategy
    alert["status"] = "closed"
    alert["outcome"] = outcome
    return alert


def test_segments_by_month_and_by_strategy():
    alerts = [
        _resolved("A", "2026-01", "win"),
        _resolved("B", "2026-01", "loss"),
        _resolved("C", "2026-02", "win"),
        _resolved("D", "2026-02", "win"),
        _resolved("E", "2026-02", "loss", strategy="other"),
    ]
    seg = segment_stats(alerts)

    assert seg["by_month"]["2026-01"]["hit_rate"] == pytest.approx(0.5)
    assert seg["by_month"]["2026-02"]["hit_rate"] == pytest.approx(2 / 3)
    assert seg["by_strategy"]["v1_technical"]["total_resolved"] == 4
    assert seg["by_strategy"]["other"]["total_resolved"] == 1
    # matches journal.py's own arithmetic on the same subset
    jan = [a for a in alerts if a["timestamp"].startswith("2026-01")]
    assert seg["by_month"]["2026-01"]["avg_rr"] == pytest.approx(
        journal.compute_stats(jan)["avg_rr"]
    )


def test_segment_counts_open_alerts_separately_from_resolved():
    alerts = [_resolved("A", "2026-01", "win")]
    alerts.append(signal_to_alert(_signal("B", 8), pd.Timestamp("2026-01-16 09:00"), "premarket"))
    seg = segment_stats(alerts)
    assert seg["overall"]["open"] == 1
    assert seg["overall"]["total_resolved"] == 1


# ------------------------------------------------------------ report text


def _results(alerts):
    return {
        "strategy": "v1_technical",
        "start": "2026-01-02",
        "end": "2026-08-21",
        "slots": 2,
        "universe_size": 441,
        "signals_before_gate": 60,
        "signals_after_gate": len(alerts),
        "alerts": alerts,
        "conviction_source": "rank proxy",
        "wall_clock_s": 12.5,
    }


def test_report_states_the_proxy_gate_prominently():
    text = render_report(_results([_resolved("A", "2026-01", "win")]))
    assert "TECHNICAL PROXY GATE" in text
    # It must be near the top, not buried in a footnote.
    assert text.index("TECHNICAL PROXY GATE") < len(text) // 2
    assert PROXY_GATE_CAVEAT in text
    lowered = PROXY_GATE_CAVEAT.lower()
    for phrase in ("volume", "does not", "conviction", "selection"):
        assert phrase in lowered, phrase


def test_report_contains_the_required_metrics_and_segments():
    alerts = [
        _resolved("A", "2026-01", "win"),
        _resolved("B", "2026-02", "loss"),
        _resolved("C", "2026-02", "scratch"),
    ]
    text = render_report(_results(alerts))
    for metric in ("hit_rate", "adj_hit_rate", "scratch_rate", "avg_rr"):
        assert metric in text, metric
    assert "2026-01" in text and "2026-02" in text
    assert "v1_technical" in text
    assert "441" in text  # universe coverage


def test_report_handles_a_run_with_no_resolved_alerts():
    """An empty report must render, not crash -- and must not claim a rate."""
    text = render_report(_results([]))
    assert "TECHNICAL PROXY GATE" in text
    assert "n/a" in text
