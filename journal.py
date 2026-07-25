# journal.py
"""Alert logging, outcome resolution, and stats aggregation. PLAN.md SS9.

Persistence (log_alerts, load_journal, save_journal) is the only I/O in this
module; resolve_alert/resolve_open_alerts/compute_stats are pure functions
over in-memory lists so they're trivially testable. The caller (main.py,
Phase 4) owns the overall load -> resolve -> save -> stats sequence.
"""

import datetime
import json
import logging

import config

from dotenv import load_dotenv

load_dotenv()

import anthropic  # noqa: E402  (import after load_dotenv so ANTHROPIC_API_KEY is set)

logger = logging.getLogger(__name__)

_CONVICTION_BUCKETS = [6, 7, 8, 9, 10]
_SCAN_LABELS = ["premarket", "preclose"]
_DIRECTIONS = ["long", "short"]
_HORIZONS = ["intraday", "swing"]

# Realized R multiple convention (not spelled out in PLAN.md SS9): wins
# realize their own rr, losses and ambiguous realize -1.0 (stopped out),
# scratches realize 0.0 (an exit near entry, not a stop-out loss).
_SCRATCH_R = 0.0
_LOSS_R = -1.0


def _alert_id(timestamp: datetime.datetime, ticker: str) -> str:
    return f"{timestamp.strftime('%Y-%m-%dT%H:%M')}-{ticker}"


def log_alerts(setups: list[dict], scan: str, now: datetime.datetime) -> list[dict]:
    """Build one alert record per setup, append to the persisted journal,
    and return the newly created records. Caller is responsible for
    filtering setups to conviction >= config.MIN_CONVICTION and capping at
    config.MAX_ALERTS before calling this."""
    new_records = []
    for setup in setups:
        ticker = setup["ticker"]
        record = {
            "id": _alert_id(now, ticker),
            "timestamp": now.isoformat(),
            "scan": scan,
            "ticker": ticker,
            "bias": setup["bias"],
            "conviction": setup["conviction"],
            "entry": setup["entry"],
            "stop": setup["stop"],
            "target": setup["target"],
            "rr": setup["rr"],
            "horizon": setup["horizon"],
            "alerted": True,
            "status": "open",
            "position": None,
            "resolved_at": None,
            "outcome": None,
            "bars_open": 0,
        }
        new_records.append(record)

    journal = load_journal()
    journal.extend(new_records)
    save_journal(journal)

    return new_records


def resolve_alert(alert: dict, bars) -> dict:
    """Pure function, no I/O. Walk bars in chronological order checking
    whether each bar's High/Low range touches target and/or stop
    (direction-aware for long vs short). Mutates and returns the same dict."""
    is_long = alert["bias"] == "long"
    entry = alert["entry"]
    stop = alert["stop"]
    target = alert["target"]

    for _, bar in bars.iterrows():
        high = bar["High"]
        low = bar["Low"]

        if is_long:
            hit_target = high >= target
            hit_stop = low <= stop
        else:
            hit_target = low <= target
            hit_stop = high >= stop

        if hit_target and hit_stop:
            alert["outcome"] = "ambiguous"
            alert["status"] = "closed"
            alert["resolved_at"] = bar.name.isoformat() if hasattr(bar.name, "isoformat") else str(bar.name)
            return alert
        if hit_target:
            alert["outcome"] = "win"
            alert["status"] = "closed"
            alert["resolved_at"] = bar.name.isoformat() if hasattr(bar.name, "isoformat") else str(bar.name)
            return alert
        if hit_stop:
            alert["outcome"] = "loss"
            alert["status"] = "closed"
            alert["resolved_at"] = bar.name.isoformat() if hasattr(bar.name, "isoformat") else str(bar.name)
            return alert

    alert["bars_open"] += len(bars)

    if alert["bars_open"] > config.SCRATCH_AFTER_BARS:
        alert["outcome"] = "scratch"
        alert["status"] = "closed"
        return alert

    if len(bars) > 0:
        last_close = bars.iloc[-1]["Close"]
        # Tie-break: exactly at entry counts as "upper".
        alert["position"] = "upper" if last_close >= entry else "lower"

    return alert


def resolve_open_alerts(alerts: list[dict], bars_by_ticker: dict) -> list[dict]:
    """Filters to status == "open", resolves each one found in
    bars_by_ticker (skipping any open alert with no bars supplied), returns
    the full input list with matched entries updated in place."""
    for alert in alerts:
        if alert["status"] != "open":
            continue
        bars = bars_by_ticker.get(alert["ticker"])
        if bars is None:
            continue
        resolve_alert(alert, bars)
    return alerts


def _empty_rate_block() -> dict:
    return {
        "hit_rate": None,
        "adj_hit_rate": None,
        "scratch_rate": None,
        "avg_rr": None,
        "wins": 0,
        "losses": 0,
        "scratches": 0,
        "total_resolved": 0,
    }


def _rate_block(closed: list[dict]) -> dict:
    wins = sum(1 for a in closed if a["outcome"] == "win")
    # ambiguous counts as a loss in every stats aggregate.
    losses = sum(1 for a in closed if a["outcome"] in ("loss", "ambiguous"))
    scratches = sum(1 for a in closed if a["outcome"] == "scratch")
    total_resolved = len(closed)

    if total_resolved == 0:
        return _empty_rate_block()

    hit_rate = wins / (wins + losses) if (wins + losses) > 0 else None
    adj_hit_rate = (wins + 0.5 * scratches) / total_resolved
    scratch_rate = scratches / total_resolved

    r_values = []
    for a in closed:
        if a["outcome"] == "win":
            r_values.append(a["rr"])
        elif a["outcome"] in ("loss", "ambiguous"):
            r_values.append(_LOSS_R)
        elif a["outcome"] == "scratch":
            r_values.append(_SCRATCH_R)
    avg_rr = sum(r_values) / len(r_values) if r_values else None

    return {
        "hit_rate": hit_rate,
        "adj_hit_rate": adj_hit_rate,
        "scratch_rate": scratch_rate,
        "avg_rr": avg_rr,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "total_resolved": total_resolved,
    }


def compute_stats(alerts: list[dict]) -> dict:
    """Operates on status == "closed" alerts only. Returns whole-set numbers
    at the top level plus a "breakdowns" key with the same numbers
    recomputed per conviction/scan/direction/horizon bucket."""
    closed = [a for a in alerts if a["status"] == "closed"]

    stats = _rate_block(closed)

    breakdowns = {
        "by_conviction": {
            bucket: _rate_block([a for a in closed if a["conviction"] == bucket])
            for bucket in _CONVICTION_BUCKETS
        },
        "by_scan": {
            scan: _rate_block([a for a in closed if a["scan"] == scan])
            for scan in _SCAN_LABELS
        },
        "by_direction": {
            direction: _rate_block([a for a in closed if a["bias"] == direction])
            for direction in _DIRECTIONS
        },
        "by_horizon": {
            horizon: _rate_block([a for a in closed if a["horizon"] == horizon])
            for horizon in _HORIZONS
        },
    }
    stats["breakdowns"] = breakdowns

    return stats


def should_compute_stats(alerts: list[dict]) -> bool:
    """True when the count of closed alerts is a positive multiple of
    config.STATS_EVERY."""
    closed_count = sum(1 for a in alerts if a["status"] == "closed")
    return closed_count > 0 and closed_count % config.STATS_EVERY == 0


def summarize_stats(stats: dict) -> str | None:
    """Sends the compute_stats() dict to Claude and returns a short
    plain-English paragraph: what's working, what isn't, what a human might
    consider changing in config.py. Read-only commentary -- never edits
    config.py itself. Returns None on any API failure; caller (main.py)
    must treat that as "no summary this scan," not a crash."""
    prompt = (
        "You are reviewing paper-trading performance stats for an equity/futures "
        "swing/intraday alert system. Below is a JSON stats block: overall "
        "hit_rate, adj_hit_rate (scratches counted as half), scratch_rate, avg_rr, "
        "and breakdowns by conviction level, scan time, direction, and horizon.\n\n"
        f"{json.dumps(stats, indent=2)}\n\n"
        "Write a short plain-English paragraph (3-5 sentences, no markdown, no "
        "JSON) covering: what's working, what isn't, and what a human might "
        "consider changing in config.py. Do not edit or propose exact config "
        "values -- describe the pattern, not the fix. This is a relative "
        "performance summary, not a probability or certainty claim: never use "
        "words like guaranteed, certain, will definitely, or can't lose."
    )
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=500,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        text = "".join(parts) if parts else None
        return text.strip() if text else None
    except Exception as e:
        logger.warning("summarize_stats Claude call failed: %r", e)
        return None


def load_journal(path: str = "journal.json") -> list[dict]:
    """Returns [] if the file doesn't exist yet (first run)."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_journal(alerts: list[dict], path: str = "journal.json") -> None:
    with open(path, "w") as f:
        json.dump(alerts, f, indent=2)
