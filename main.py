"""Phase 1 entry point: guards + scan pipeline orchestration."""

import argparse
import datetime
import logging
import sys

import pytz

import analyst
import config
import data
import display
import filter as filter_mod
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


def run_scan(dry_run: bool = False, limit: int | None = None) -> list[dict]:
    tz = pytz.timezone(config.MARKET_TZ)
    now = datetime.datetime.now(tz)

    if not dry_run:
        if not is_weekday(now):
            logger.info("Not a weekday (%s); exiting.", now.date())
            return []
        if not market_open_today(now):
            logger.info("Market not open today (%s); exiting.", now.date())
            return []

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
    logger.info(
        "Scanned %d -> data OK %d -> survivors %d",
        len(universe), len(universe_frames), len(survivors),
    )

    if limit is not None:
        survivors = survivors[:limit]

    if not survivors:
        logger.info("No survivors; skipping news + analyst.")
        return survivors

    survivors = news.attach_news(survivors, now)
    setups = analyst.analyze_survivors(survivors, now)

    logger.info("Analyst returned %d setup(s) from %d survivor(s).", len(setups), len(survivors))
    for setup in setups:
        logger.info(
            "%s: bias=%s conviction=%s entry=%s stop=%s target=%s rr=%s horizon=%s",
            setup.get("ticker"), setup.get("bias"), setup.get("conviction"),
            setup.get("entry"), setup.get("stop"), setup.get("target"),
            setup.get("rr"), setup.get("horizon"),
        )

    return survivors


def main() -> int:
    parser = argparse.ArgumentParser(description="Trade scanner Phase 1 pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip weekday/market-open guards")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Truncate survivors to the first N before calling news/analyst (manual cost-controlled testing)",
    )
    args = parser.parse_args()

    run_scan(dry_run=args.dry_run, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
