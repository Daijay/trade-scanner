# config.py
# Every tunable value lives here. No magic numbers anywhere else in the codebase.

import datetime

# -- Universe -----------------------------------------------------------
SP500_ENABLED = True
NASDAQ100_ENABLED = True
FUTURES = ["NQ=F", "ES=F", "YM=F", "RTY=F"]
EXTRA: list[str] = []          # manual adds
UNIVERSE_CAP = 500              # hard ceiling after dedupe

# -- Timeframes (Phase 1: data + indicators) -----------------------------
# yfinance interval strings and how much history to pull for each.
TIMEFRAMES = {
    "30m": {"interval": "30m", "period": "60d"},
    "4h":  {"interval": "1h",  "period": "180d"},   # yfinance has no native 4h; resampled from 1h
    "daily": {"interval": "1d", "period": "400d"},
}

# -- Filter thresholds (tune these, not the code) ------------------------
MIN_AVG_VOLUME = 1_000_000      # liquidity floor
MIN_PRICE = 5.00                # no penny stocks
MAX_SURVIVORS = 30              # hard cap into Claude
MIN_ATR_PCT = 0.5               # skip dead, non-moving names

# -- Alerting -------------------------------------------------------------
MAX_ALERTS = 8
MIN_CONVICTION = 6              # out of 10
MIN_RR = 1.5

# -- News -------------------------------------------------------------------
NEWS_WINDOW_HOURS = 24           # lookback for headlines
NEWS_WINDOW_HOURS_MONDAY = 48    # wider lookback Monday to cover the weekend gap
NEWS_HEADLINE_CAP = 5            # max headlines kept (per symbol / market feed)

# -- Claude ----------------------------------------------------------------
MODEL = "claude-sonnet-5"                  # verify current string in console
MODEL_CHEAP = "claude-haiku-4-5-20251001"  # fallback if cost climbs
BATCH_SIZE = 10                  # symbols per API call
MAX_TOKENS = 4000

# -- Journal ----------------------------------------------------------------
SCRATCH_AFTER_BARS = 12         # bars open with no hit -> scratch
STATS_EVERY = 30                # resolved alerts per review
PAPER_MODE = True               # flip only after 30 reviewed sessions
MIN_PAPER_SESSIONS = 30

# -- Display -----------------------------------------------------------------
SCAN_ANIMATION = True           # auto-disabled when CI env var present
TICKER_DELAY = 0.2              # seconds per ticker

# -- Schedule / market guards --------------------------------------------
MARKET_TZ = "America/Vancouver"
MARKET_OPEN = datetime.time(6, 30)
MARKET_CLOSE = datetime.time(13, 0)
# (scan label -> nominal PT time)
SCAN_TIMES_PT = {
    "premarket": datetime.time(6, 0),
    "midsession": datetime.time(10, 30),
    "preclose": datetime.time(12, 30),
}
SCAN_TOLERANCE_MINUTES = 20

# Full-day NYSE closures. Must be updated yearly (see nyse.com holiday calendar).
MARKET_HOLIDAYS = [
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Washington's Birthday
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed, Jul 4 falls on Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
]
