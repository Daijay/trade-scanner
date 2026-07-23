"""Phase 1 entry point: guards + scan pipeline orchestration."""

import argparse
import datetime
import logging
import sys

import pytz

import config
import data
import display
import filter as filter_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def is_weekday(now: datetime.datetime) -> bool:
    return now.weekday() < 5  # Mon=0 ... Sun=6


def market_open_today(now: datetime.datetime) -> bool:
    """Fail-open: a network/data error assumes the market is open rather than silently skipping."""
    try:
        spy = data.fetch_ohlcv(["SPY"], "daily")
        if "SPY" not in spy or spy["SPY"].empty:
            logger.warning("Could not fetch SPY to confirm market is open; assuming open.")
            return True
        last_bar_date = spy["SPY"].index[-1].date()
        return last_bar_date == now.date()
    except Exception as e:
        logger.warning("market_open_today check failed (%r); assuming open.", e)
        return True


def run_scan(dry_run: bool = False) -> list[dict]:
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
    return survivors


def main() -> int:
    parser = argparse.ArgumentParser(description="Trade scanner Phase 1 pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip weekday/market-open guards")
    args = parser.parse_args()

    run_scan(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
