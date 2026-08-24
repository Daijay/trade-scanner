# backtest/report.py
"""Resolution stats via ``journal.py``, and the rendered report.

Every rate in this file is computed by ``journal._rate_block`` — the same
function that produces the live paper-trading digest's numbers. This module
segments the alert list and formats the output; it does not know what a win is,
and it must never learn.

The caveat that has to travel with the numbers
----------------------------------------------
:data:`PROXY_GATE_CAVEAT` is printed at the top of every report, not tucked into
the README. A hit rate from this engine that gets copied into a message, a
commit or a decision has to carry with it the fact that the alerts it counts
were selected by a **technical proxy gate**, not by ``analyst.py``.
"""

from __future__ import annotations

from collections import OrderedDict

import journal

#: Printed near the top of every rendered report. The wording is asserted in
#: ``tests/backtest/test_report.py`` so it cannot quietly soften over time.
PROXY_GATE_CAVEAT = (
    "TECHNICAL PROXY GATE -- READ BEFORE USING ANY NUMBER BELOW. "
    "Live, survivors of filter.run_filter are scored by analyst.py (Claude, "
    "with news headlines), floored at config.MIN_CONVICTION, sorted by "
    "conviction and capped at config.MAX_ALERTS. This backtest has no analyst: "
    "no free historical news archive covers this date range, so analyst.py "
    "cannot be replayed at all. In its place, each slot's survivors are ranked "
    "by filter.score_survivor and the top config.MAX_ALERTS are kept. "
    "That approximates live alert VOLUME, which is what makes these hit rates "
    "comparable to the live journal's at all -- roughly the same number of "
    "alerts per scan, rather than the 30-per-slot MAX_SURVIVORS cap. "
    "It does NOT replay Claude's conviction judgement. Signal SELECTION "
    "therefore differs from live even where volume matches, and the conviction "
    "column below is a within-slot rank, not a model score. Do not read these "
    "rates as a forecast of the live system's performance; read them as the "
    "quality of the technical filter's ordering, in isolation."
)

_METRICS = ("hit_rate", "adj_hit_rate", "scratch_rate", "avg_rr")


def _block(alerts: list[dict]) -> dict:
    """``journal._rate_block`` over *alerts*, plus the open count.

    ``journal`` reports on closed alerts only — correctly, since an unresolved
    alert has no outcome — but a backtest that ended with a third of its signals
    still open would be reporting on a biased subset without saying so, so the
    open count travels with every block.
    """
    closed = [a for a in alerts if a.get("status") == "closed"]
    block = dict(journal._rate_block(closed))
    block["open"] = sum(1 for a in alerts if a.get("status") != "closed")
    block["total"] = len(alerts)
    return block


def _month_of(alert: dict) -> str:
    return str(alert.get("timestamp", ""))[:7]


def segment_stats(alerts: list[dict]) -> dict:
    """Overall / by-strategy / by-month blocks, all from ``journal._rate_block``."""
    by_month: "OrderedDict[str, dict]" = OrderedDict()
    for month in sorted({_month_of(a) for a in alerts if _month_of(a)}):
        by_month[month] = _block([a for a in alerts if _month_of(a) == month])

    by_strategy: "OrderedDict[str, dict]" = OrderedDict()
    for name in sorted({a.get("strategy", "") for a in alerts}):
        by_strategy[name] = _block([a for a in alerts if a.get("strategy", "") == name])

    return {
        "overall": _block(alerts),
        "by_strategy": by_strategy,
        "by_month": by_month,
        "journal_stats": journal.compute_stats(alerts),
    }


def _fmt(value, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    if pct:
        return f"{value * 100:.1f}%"
    return f"{value:+.3f}"


def _table(rows: "OrderedDict[str, dict]", label: str) -> list[str]:
    out = [
        f"| {label} | hit_rate | adj_hit_rate | scratch_rate | avg_rr | wins | losses | scratches | open | resolved |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, b in rows.items():
        out.append(
            f"| {key or '(unnamed)'} | {_fmt(b['hit_rate'], True)} | {_fmt(b['adj_hit_rate'], True)} "
            f"| {_fmt(b['scratch_rate'], True)} | {_fmt(b['avg_rr'])} | {b['wins']} | {b['losses']} "
            f"| {b['scratches']} | {b['open']} | {b['total_resolved']} |"
        )
    return out


def render_report(results: dict) -> str:
    """The full markdown report for one ``run_backtest`` result."""
    alerts = results.get("alerts", [])
    seg = segment_stats(alerts)
    o = seg["overall"]

    lines: list[str] = []
    lines.append(f"# Backtest report — {results.get('strategy', '?')}")
    lines.append("")
    lines.append("> **" + PROXY_GATE_CAVEAT + "**")
    lines.append("")
    lines.append("## Run")
    lines.append("")
    lines.append(f"- window: `{results.get('start')}` → `{results.get('end')}`")
    lines.append(f"- scan slots replayed: {results.get('slots')}")
    lines.append(
        f"- universe: {results.get('universe_size')} tickers with cached 30m history"
    )
    lines.append(
        f"- signals before the {results.get('gate', 'gate')}: "
        f"{results.get('signals_before_gate')} "
        f"(MAX_SURVIVORS = {results.get('max_survivors')})"
    )
    lines.append(
        f"- alerts after the {results.get('gate', 'gate')}: "
        f"{results.get('signals_after_gate')} "
        f"(MAX_ALERTS = {results.get('max_alerts')} per slot)"
    )
    lines.append(f"- conviction column: {results.get('conviction_source')}")
    wall = results.get("wall_clock_s")
    if wall is not None:
        lines.append(f"- wall clock: {wall / 60:.1f} min")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.extend(_table(OrderedDict([("all", o)]), "segment"))
    lines.append("")
    lines.append(
        f"Resolved {o['total_resolved']} of {o['total']} alerts; {o['open']} still open at the "
        "end of the window (an open alert has no outcome and is excluded from every rate above)."
    )
    lines.append("")
    lines.append("## By strategy")
    lines.append("")
    lines.extend(_table(seg["by_strategy"], "strategy"))
    lines.append("")
    lines.append("## By month")
    lines.append("")
    lines.extend(_table(seg["by_month"], "month"))
    lines.append("")
    lines.append("## How to read these numbers")
    lines.append("")
    lines.append(
        "- `hit_rate` = wins / (wins + losses). `adj_hit_rate` = (wins + 0.5 x scratches) / "
        "resolved. `scratch_rate` = scratches / resolved. `avg_rr` realizes each signal's own "
        "`rr` on a win, -1.0 on a loss or an ambiguous bar, 0.0 on a scratch. All four come "
        "from `journal._rate_block`, unmodified — the same function that produces the live "
        "paper-trading digest."
    )
    lines.append(
        "- Every signal's target sits at exactly `config.MIN_RR`, so `rr` is constant across "
        "the run and `avg_rr` is a deterministic function of the hit and scratch rates rather "
        "than an independent result."
    )
    lines.append(
        "- A bar that touches both target and stop resolves as `ambiguous` and counts as a "
        "loss, exactly as it does live. 30m bars cannot say which came first."
    )
    lines.append("- See `backtest/README.md` for scope, data sources and methodological limits.")
    lines.append("")
    return "\n".join(lines)


__all__ = ["segment_stats", "render_report", "PROXY_GATE_CAVEAT"]
