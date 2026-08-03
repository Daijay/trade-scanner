"""Phase 1 entry point: guards + scan pipeline orchestration."""

import argparse
import datetime
import logging
import sys

import pytz

import analyst
import config
import data
import digest
import display
import filter as filter_mod
import journal
import news

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def is_weekday(now: datetime.datetime) -> bool:
    return now.weekday() < 5  # Mon=0 ... Sun=6


def market_open_today(now: datetime.datetime) -> bool:
    """Calendar-based guard: checks `now`'s date against config.MARKET_HOLIDAYS,
    a maintained list of full-day NYSE closures. No network call.

    Limitation: this only knows about scheduled holidays published in advance.
    It does not detect unscheduled emergency closures (e.g. a 9/11-style
    event) since those aren't in the list -- an accepted, explicitly-noted
    gap for Phase 1, not a regression (the prior bar-presence check couldn't
    reliably detect those either, and additionally false-negatived on every
    ordinary pre-market scan)."""
    return now.date().isoformat() not in config.MARKET_HOLIDAYS


def within_scan_tolerance(now: datetime.datetime) -> bool:
    """True if `now` is within config.SCAN_TOLERANCE_MINUTES minutes of any
    configured scan time in config.SCAN_TIMES_PT. GitHub Actions cron fires
    at both UTC offsets to cover DST, so this catches the "wrong" offset
    firing outside the real scheduled window."""
    for t in config.SCAN_TIMES_PT.values():
        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        diff_minutes = abs((now - target).total_seconds()) / 60
        if diff_minutes <= config.SCAN_TOLERANCE_MINUTES:
            return True
    return False


def within_daily_report_tolerance(now: datetime.datetime) -> bool:
    t = config.DAILY_REPORT_TIME_PT
    target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    diff_minutes = abs((now - target).total_seconds()) / 60
    return diff_minutes <= config.DAILY_REPORT_TOLERANCE_MINUTES


def previous_scan_time(scan: str, now: datetime.datetime) -> datetime.datetime:
    """Returns the nominal datetime of the scan immediately prior to `scan`
    at `now`, per config.SCAN_TIMES_PT. premarket's previous scan is
    preclose on the last prior weekday; preclose's previous scan is
    premarket earlier the same day."""
    tz = now.tzinfo
    if scan == "preclose":
        t = config.SCAN_TIMES_PT["premarket"]
        return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

    # scan == "premarket": walk back to the previous weekday's preclose.
    day = now
    while True:
        day = day - datetime.timedelta(days=1)
        if day.weekday() < 5:
            break
    t = config.SCAN_TIMES_PT["preclose"]
    return day.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0, tzinfo=tz)


def resolve_previous_alerts(now: datetime.datetime, scan: str) -> list[dict]:
    """Loads the journal, resolves every status=="open" alert using 30m bars
    strictly after the previous scan's nominal time, saves the updated
    journal, and returns the full alert list (open + closed)."""
    alerts = journal.load_journal()
    open_tickers = sorted({a["ticker"] for a in alerts if a["status"] == "open"})

    if not open_tickers:
        return alerts

    cutoff = previous_scan_time(scan, now)
    raw_bars = data.fetch_ohlcv(open_tickers, "30m")
    bars_by_ticker = {
        ticker: df[df.index > cutoff]
        for ticker, df in raw_bars.items()
    }

    journal.resolve_open_alerts(alerts, bars_by_ticker)
    journal.save_journal(alerts)
    return alerts


def _current_scan_label(now: datetime.datetime) -> str:
    """Picks whichever configured scan time is closest to `now` (used for
    labeling the journal entry / digest header; the actual +-tolerance guard
    against firing at the wrong time is out of scope for this task)."""
    best = min(
        config.SCAN_TIMES_PT.items(),
        key=lambda kv: abs(
            (now.hour * 60 + now.minute) - (kv[1].hour * 60 + kv[1].minute)
        ),
    )
    return best[0]


def run_scan(dry_run: bool = False, limit: int | None = None, force_run: bool = False) -> dict:
    tz = pytz.timezone(config.MARKET_TZ)
    now = datetime.datetime.now(tz)

    if not dry_run:
        if not is_weekday(now):
            logger.info("Not a weekday (%s); exiting.", now.date())
            return {"setups": [], "digest": None}
        if not market_open_today(now):
            logger.info("Market not open today (%s); exiting.", now.date())
            return {"setups": [], "digest": None}
        if not force_run and not within_scan_tolerance(now):
            logger.info("Outside scheduled scan window (%s); exiting.", now.time())
            return {"setups": [], "digest": None}
        if force_run:
            logger.info("force_run set: bypassing within_scan_tolerance (weekday/holiday guards still apply).")

    scan = _current_scan_label(now)

    logger.info("Resolving outcomes of previously open alerts...")
    alerts = resolve_previous_alerts(now, scan)
    just_resolved = [a for a in alerts if a["status"] == "closed" and a.get("resolved_at")]

    logger.info("Building universe...")
    universe = data.build_universe()
    logger.info("Universe size: %d", len(universe))

    logger.info("Fetching multi-timeframe OHLCV (this may take a while for the full universe)...")
    universe_frames = data.fetch_all_timeframes(universe)
    logger.info("Symbols with complete data: %d", len(universe_frames))

    survivors, filtered_out = filter_mod.run_filter(universe_frames)

    for entry in survivors:
        display.flash_ticker(entry["symbol"], True, "", entry["analysis"]["alignment"])
    for entry in filtered_out:
        if entry["reason"] != "excess":
            display.flash_ticker(entry["symbol"], False, entry["reason"], entry["analysis"]["alignment"])

    display.print_survivor_table(survivors)
    scan_counts = {"scanned": len(universe), "filtered": len(survivors), "alerts": 0}
    logger.info(
        "Scanned %d -> data OK %d -> survivors %d",
        len(universe), len(universe_frames), len(survivors),
    )

    if limit is not None:
        survivors = survivors[:limit]

    setups: list[dict] = []
    if survivors:
        survivors = news.attach_news(survivors, now)
        setups = analyst.analyze_survivors(survivors, now)
        logger.info("Analyst returned %d setup(s) from %d survivor(s).", len(setups), len(survivors))

    journal.log_alerts(setups, scan, now)

    alertable = [s for s in setups if s.get("conviction", 0) >= config.MIN_CONVICTION]
    alertable.sort(key=lambda s: s.get("conviction", 0), reverse=True)
    alertable = alertable[: config.MAX_ALERTS]
    scan_counts["alerts"] = len(alertable)

    alerts = journal.load_journal()
    stats = journal.compute_stats(alerts)
    closed_count = sum(1 for a in alerts if a["status"] == "closed")
    session_number = min(closed_count, config.MIN_PAPER_SESSIONS) if closed_count else 0

    stats["session"] = {
        "wins": sum(1 for a in just_resolved if a["outcome"] == "win"),
        "losses": sum(1 for a in just_resolved if a["outcome"] in ("loss", "ambiguous")),
        "scratches": sum(1 for a in just_resolved if a["outcome"] == "scratch"),
        "when": "this scan",
        "session_number": session_number,
    }

    summary_text = None
    if journal.should_compute_stats(alerts):
        logger.info("Stats crossed a %d-resolved-alert boundary; requesting summary...", config.STATS_EVERY)
        summary_text = journal.summarize_stats(stats)

    if alertable:
        market_context = data.fetch_market_context()
        message = digest.build_digest(scan, now, alertable, scan_counts, stats, market_context=market_context)
    else:
        reason = "No setups. No survivors reached the alert threshold this scan."
        message = digest.build_no_setup_digest(scan, now, scan_counts, reason, stats, session_number)

    if summary_text:
        message = f"{message}\n\n{summary_text}"

    return {"setups": alertable, "digest": message, "scan": scan, "now": now}


def run_daily_report(dry_run: bool = False, force_run: bool = False) -> dict:
    tz = pytz.timezone(config.MARKET_TZ)
    now = datetime.datetime.now(tz)

    if not dry_run:
        if not is_weekday(now):
            logger.info("Not a weekday (%s); exiting daily report.", now.date())
            return {"report": None}
        if not market_open_today(now):
            logger.info("Market not open today (%s); exiting daily report.", now.date())
            return {"report": None}
        if not force_run and not within_daily_report_tolerance(now):
            logger.info("Outside daily report window (%s); exiting.", now.time())
            return {"report": None}

    alerts = journal.load_journal()
    report = digest.build_daily_report(alerts, now.date())

    return {"report": report}


def main() -> int:
    parser = argparse.ArgumentParser(description="Trade scanner pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip weekday/market-open guards")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Truncate survivors to the first N before calling news/analyst (manual cost-controlled testing)",
    )
    parser.add_argument(
        "--force-run", action="store_true",
        help="Bypass only the within_scan_tolerance time-of-day check for on-demand manual "
             "testing (e.g. workflow_dispatch); weekday/holiday guards still apply. Never set "
             "this for scheduled runs.",
    )
    parser.add_argument(
        "--daily-report", action="store_true",
        help="Run the once-daily summary email instead of the scan pipeline. Reads journal.json "
             "only -- does not run data/filter/news/analyst.",
    )
    args = parser.parse_args()

    if args.daily_report:
        result = run_daily_report(dry_run=args.dry_run, force_run=args.force_run)
        if result.get("report"):
            import notify
            notify.send_daily_report(result["report"])
        return 0

    result = run_scan(dry_run=args.dry_run, limit=args.limit, force_run=args.force_run)
    if result.get("digest"):
        import notify
        notify.send_digest(result["digest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
