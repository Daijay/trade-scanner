# digest.py
"""Builds the plain-text scan digest message sent to Telegram. PLAN.md SS10.

Pure string-building only -- no network/API calls, no market-index price
fetching (the caller passes market_context as a plain string it already
computed) and no scanned/filtered/alerts counting (the caller passes
scan_counts, since main.py already has those numbers from run_filter).

Judgment calls (documented per the brief):
- setups: this module accepts analyst.py's setup dicts directly (ticker,
  bias, conviction, entry, stop, target, rr, horizon, timeframes, news_read,
  reasoning) -- not log_alerts's persisted alert records -- since those are
  what main.py has in hand right before logging/alerting, and they carry
  strictly more information (timeframes, news_read, reasoning) than the
  persisted record.
- Per-timeframe pass/fail (checkmark/cross): "this timeframe agrees with the
  setup's bias" -- i.e. trend == "up" counts as agreeing with a long bias,
  trend == "down" agrees with a short bias. A timeframe with no trend info
  renders neither: it's simply omitted from the line.
- The 📰 news line: analyst.py's setup schema only carries a free-text
  "news_read" string (Claude's own summary), not the structured
  net_sentiment/headline_count the news module computed -- that structured
  data isn't threaded through analyst.py's output. Rather than re-deriving
  sentiment/counts that were never preserved, this module renders
  news_read verbatim after the 📰 prefix. If a setup happens to carry an
  attached "news" dict (net_sentiment/headline_count), prefer rendering
  from that instead, since it's more precise -- this keeps the door open
  for a future analyst.py change that threads the structured data through.
- The per-scan resolved counts ("N win / N loss / N scratch") and the
  session number are passed as a separate small dict via `stats["session"]`
  (see build_digest's `stats` parameter) rather than inventing a second
  top-level parameter -- the caller already builds `stats` from
  compute_stats() and can attach this one extra small dict onto it before
  calling build_digest, since compute_stats() itself has no notion of
  "this scan's just-resolved counts" (that's a diff the caller computes
  from before/after alert lists).
"""

import datetime

import config
import journal

_HEADER_LABELS = {
    "premarket": "PRE-MARKET SCAN",
    "preclose": "PRE-CLOSE SCAN",
}

_DIVIDER = "━━━━━━━━━━"
_SETUPS_DIVIDER = f"{_DIVIDER} SETUPS {_DIVIDER}"
_JOURNAL_DIVIDER = f"{_DIVIDER} JOURNAL {_DIVIDER}"

_CONVICTION_HIGH = 8  # >= this -> green
_CONVICTION_MID = 6   # >= this (and < high) -> yellow; below -> red (shouldn't happen)

_TF_ORDER = ["30m", "4h", "daily"]
_TF_LABELS = {"30m": "30M", "4h": "4H", "daily": "1D"}


def _header_line(scan: str, now: datetime.datetime) -> str:
    label = _HEADER_LABELS.get(scan, scan.upper())
    hour12 = now.hour % 12 or 12
    timestamp = now.strftime(f"%a %b {now.day}, {hour12}:%M %p")
    return f"📊 {label} — {timestamp} PT"


def _conviction_emoji(conviction: int) -> str:
    if conviction >= _CONVICTION_HIGH:
        return "🟢"
    if conviction >= _CONVICTION_MID:
        return "🟡"
    return "🔴"


def _timeframe_checks(setup: dict) -> str:
    bias = setup.get("bias")
    timeframes = setup.get("timeframes") or {}
    parts = []
    for tf in _TF_ORDER:
        snap = timeframes.get(tf)
        if not snap or not isinstance(snap, dict):
            continue
        trend = snap.get("trend")
        if trend is None:
            continue
        agrees = (trend == "up" and bias == "long") or (trend == "down" and bias == "short")
        mark = "✅" if agrees else "❌"
        parts.append(f"{mark} {_TF_LABELS.get(tf, tf.upper())}")
    return "  ".join(parts)


def _news_line(setup: dict) -> str:
    news = setup.get("news")
    if isinstance(news, dict):
        sentiment = news.get("net_sentiment")
        count = news.get("headline_count", len(news.get("headlines", []) or []))
        sentiment_label = sentiment if sentiment else "no clear read"
        return f"📰 {sentiment_label}, {count} headlines"
    news_read = setup.get("news_read")
    if news_read:
        return f"📰 {news_read}"
    return "📰 no news data"


def _setup_block(setup: dict) -> str:
    emoji = _conviction_emoji(setup.get("conviction", 0))
    ticker = setup.get("ticker", "?")
    conviction = setup.get("conviction", 0)
    bias = (setup.get("bias") or "").upper()
    horizon = setup.get("horizon", "")

    lines = [f"{emoji} {ticker} — {bias} ({conviction}/10, {horizon})"]

    checks = _timeframe_checks(setup)
    if checks:
        lines.append(checks)

    entry = setup.get("entry")
    stop = setup.get("stop")
    target = setup.get("target")
    rr = setup.get("rr")
    lines.append(f"Entry {entry} │ Stop {stop} │ Target {target} │ R:R {rr}")

    lines.append(_news_line(setup))
    lines.append(str(setup.get("reasoning", "")))

    return "\n".join(lines)


def _pct(value) -> str:
    if value is None:
        return "N/A"
    return f"{round(value * 100)}%"


def _paper_suffix() -> str:
    return " (paper)" if config.PAPER_MODE else ""


def _journal_section(stats: dict | None) -> str:
    lines = [_JOURNAL_DIVIDER]

    session_info = (stats or {}).get("session") if stats else None
    if session_info:
        w = session_info.get("wins", 0)
        l = session_info.get("losses", 0)
        s = session_info.get("scratches", 0)
        when = session_info.get("when", "this scan")
        lines.append(f"Resolved {when}: {w} win · {l} loss · {s} scratch")

    session_number = (session_info or {}).get("session_number") if session_info else None
    if session_number is not None:
        lines.append(f"Session {session_number} of {config.MIN_PAPER_SESSIONS}{_paper_suffix()}")
    else:
        lines.append(f"Session ? of {config.MIN_PAPER_SESSIONS}{_paper_suffix()}")

    if stats is not None:
        lines.append(
            f"Session Hit rate {_pct(stats.get('hit_rate'))} │ "
            f"Adjusted {_pct(stats.get('adj_hit_rate'))} │ "
            f"Scratch {_pct(stats.get('scratch_rate'))}"
        )
        overall = stats.get("overall")
        if overall:
            lines.append(
                f"Overall Hit rate {_pct(overall.get('hit_rate'))} │ "
                f"Adjusted {_pct(overall.get('adj_hit_rate'))} │ "
                f"Scratch {_pct(overall.get('scratch_rate'))}"
            )

    return "\n".join(lines)


def build_digest(
    scan: str,
    now: datetime.datetime,
    setups: list[dict],
    scan_counts: dict,
    stats: dict | None,
    market_context: str = "",
) -> str:
    lines = [_header_line(scan, now)]

    if market_context:
        lines.append(market_context)

    lines.append(
        f"Scanned {scan_counts.get('scanned', 0)} → "
        f"filtered {scan_counts.get('filtered', 0)} → "
        f"alerts {scan_counts.get('alerts', 0)}"
    )

    lines.append(_SETUPS_DIVIDER)
    for setup in setups:
        lines.append(_setup_block(setup))

    lines.append(_journal_section(stats))

    return "\n\n".join(lines)


def build_no_setup_digest(
    scan: str,
    now: datetime.datetime,
    scan_counts: dict,
    reason: str,
    stats: dict | None,
    session_number: int,
) -> str:
    lines = [_header_line(scan, now)]

    lines.append(
        f"Scanned {scan_counts.get('scanned', 0)} → "
        f"filtered {scan_counts.get('filtered', 0)} → "
        f"alerts {scan_counts.get('alerts', 0)}"
    )

    lines.append(reason)

    journal_line = f"Session {session_number} of {config.MIN_PAPER_SESSIONS}{_paper_suffix()}"
    if stats is not None:
        journal_line += f" · Hit rate {_pct(stats.get('hit_rate'))}"
    lines.append(journal_line)

    return "\n\n".join(lines)


def _split_digest_sections(message: str) -> tuple[str, str, list[str], str]:
    """Splits a build_digest message into (header_block, setups_divider,
    setup_blocks, journal_block). header_block is everything before the
    SETUPS divider (header + market context + scan counts)."""
    parts = message.split("\n\n")
    setups_idx = parts.index(_SETUPS_DIVIDER)
    journal_idx = next(i for i, p in enumerate(parts) if p.startswith(_JOURNAL_DIVIDER))

    header_block = "\n\n".join(parts[:setups_idx])
    setup_blocks = parts[setups_idx + 1:journal_idx]
    journal_block = "\n\n".join(parts[journal_idx:])

    return header_block, _SETUPS_DIVIDER, setup_blocks, journal_block


def split_for_telegram(message: str, limit: int = 4096) -> list[str]:
    if len(message) <= limit:
        return [message]

    try:
        header_block, setups_divider, setup_blocks, journal_block = _split_digest_sections(message)
    except (ValueError, StopIteration):
        # Not a recognizable build_digest layout (e.g. build_no_setup_digest
        # output, which is already short) -- fall back to a hard chunker
        # that never splits mid-line.
        chunks = []
        current = ""
        for line in message.split("\n\n"):
            candidate = f"{current}\n\n{line}" if current else line
            if len(candidate) > limit and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    chunks = []
    current_blocks: list[str] = []
    current_prefix = f"{header_block}\n\n{setups_divider}"

    def _flush():
        if current_blocks:
            chunks.append("\n\n".join([current_prefix] + current_blocks))

    for block in setup_blocks:
        candidate_len = len(current_prefix) + 2 + len("\n\n".join(current_blocks + [block]))
        if current_blocks and candidate_len > limit:
            _flush()
            current_blocks = [block]
        else:
            current_blocks.append(block)

    _flush()

    if not chunks:
        chunks.append(current_prefix)

    # Journal section goes on the last chunk only.
    chunks[-1] = f"{chunks[-1]}\n\n{journal_block}"

    return chunks


# -- build_daily_report ------------------------------------------------------

_DAILY_ALERTS_DIVIDER = f"{_DIVIDER} TODAY'S ALERTS {_DIVIDER}"
_DAILY_RESOLVED_DIVIDER = f"{_DIVIDER} RESOLVED TODAY {_DIVIDER}"


def _daily_header_line(today: datetime.date) -> str:
    weekday_month = today.strftime("%a %b")
    timestamp = f"{weekday_month} {today.day}, {today.year}"
    return f"📊 DAILY REPORT — {timestamp}"


def _fired_today(journal_entries: list[dict], today: datetime.date) -> list[dict]:
    return [
        a for a in journal_entries
        if datetime.datetime.fromisoformat(a["timestamp"]).date() == today
    ]


def _resolved_today(journal_entries: list[dict], today: datetime.date) -> list[dict]:
    return [
        a for a in journal_entries
        if a["status"] == "closed"
        and a.get("resolved_at")
        and datetime.datetime.fromisoformat(a["resolved_at"]).date() == today
    ]


def _batch_summary_line(fired: list[dict]) -> str:
    if not fired:
        return "0 alerts fired today."

    longs = sum(1 for a in fired if a.get("bias") == "long")
    shorts = sum(1 for a in fired if a.get("bias") == "short")
    swings = sum(1 for a in fired if a.get("horizon") == "swing")
    intraday = sum(1 for a in fired if a.get("horizon") == "intraday")
    avg_conviction = sum(a.get("conviction", 0) for a in fired) / len(fired)

    return (
        f"{len(fired)} alerts fired — {longs} long / {shorts} short, "
        f"{swings} swing / {intraday} intraday, "
        f"avg conviction {avg_conviction:.1f}"
    )


def _fired_alert_line(alert: dict) -> str:
    ticker = alert.get("ticker", "?")
    bias = (alert.get("bias") or "").upper()
    conviction = alert.get("conviction", 0)
    horizon = alert.get("horizon", "")
    scan = alert.get("scan", "")
    return f"{ticker} — {bias} ({conviction}/10, {horizon}, {scan})"


def _resolution_line(alert: dict) -> str:
    ticker = alert.get("ticker", "?")
    outcome = alert.get("outcome") or "unknown"
    return f"{ticker} — {outcome}"


def _session_number(journal_entries: list[dict], today: datetime.date) -> int:
    dates = {
        datetime.datetime.fromisoformat(a["timestamp"]).date()
        for a in journal_entries
        if datetime.datetime.fromisoformat(a["timestamp"]).date() <= today
    }
    return min(len(dates), config.MIN_PAPER_SESSIONS)


def _daily_journal_section(journal_entries: list[dict], today: datetime.date) -> str:
    stats = journal.compute_stats(journal_entries)

    if stats.get("total_resolved", 0) == 0:
        open_count = len([a for a in journal_entries if a["status"] == "open"])
        session_number = _session_number(journal_entries, today)
        return (
            f"No alerts have resolved yet. Session {session_number} of "
            f"{config.MIN_PAPER_SESSIONS}{_paper_suffix()}. "
            f"{open_count} alerts currently open."
        )

    baseline_entries = [
        a for a in journal_entries
        if datetime.datetime.fromisoformat(a["timestamp"]).date() >= config.STATS_BASELINE_DATE
    ]
    overall_stats = journal.compute_stats(baseline_entries)

    return "\n".join([
        f"Session Hit rate {_pct(stats.get('hit_rate'))} │ "
        f"Adjusted {_pct(stats.get('adj_hit_rate'))} │ "
        f"Scratch {_pct(stats.get('scratch_rate'))}",
        f"Overall Hit rate {_pct(overall_stats.get('hit_rate'))} │ "
        f"Adjusted {_pct(overall_stats.get('adj_hit_rate'))} │ "
        f"Scratch {_pct(overall_stats.get('scratch_rate'))}",
    ])


def build_daily_report(journal: list[dict], today: datetime.date) -> str:
    fired = _fired_today(journal, today)
    resolved = _resolved_today(journal, today)

    lines = [_daily_header_line(today)]

    alerts_lines = [_DAILY_ALERTS_DIVIDER, _batch_summary_line(fired)]
    if fired:
        alerts_lines.extend(_fired_alert_line(a) for a in fired)
    else:
        alerts_lines.append("(none)")
    lines.append("\n".join(alerts_lines))

    resolved_lines = [_DAILY_RESOLVED_DIVIDER]
    if resolved:
        resolved_lines.extend(_resolution_line(a) for a in resolved)
    else:
        resolved_lines.append("(none)")
    lines.append("\n".join(resolved_lines))

    journal_lines = [_JOURNAL_DIVIDER, _daily_journal_section(journal, today)]
    lines.append("\n".join(journal_lines))

    return "\n\n".join(lines)
