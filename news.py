# news.py
"""Per-symbol and market-wide news fetching + VADER sentiment scoring.

Failures are non-fatal: a dead/unreachable feed for one symbol must never
crash the pipeline or affect any other symbol.
"""

import datetime
import logging

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import config

logger = logging.getLogger(__name__)

_SYMBOL_FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
_MARKET_FEED_URL = "https://finance.yahoo.com/news/rssindex"

_analyzer = SentimentIntensityAnalyzer()


def _lookback_hours(now: datetime.datetime) -> int:
    """24h lookback by default; 48h on Monday to cover the weekend gap."""
    if now.weekday() == 0:
        return config.NEWS_WINDOW_HOURS_MONDAY
    return config.NEWS_WINDOW_HOURS


def _entry_time(entry) -> datetime.datetime | None:
    """Convert a feedparser entry's published_parsed struct_time to a UTC datetime.
    Returns None if the entry has no usable timestamp (malformed entry)."""
    parsed = getattr(entry, "published_parsed", None)
    if parsed is None:
        return None
    return datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)


def _fetch_entries_in_window(url: str, now: datetime.datetime) -> list:
    """Fetch a feed and return entries within the lookback window,
    most-recent-first. Returns [] on any failure (network, parse, malformed)."""
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.info("News feed fetch failed for %s: %r", url, e)
        return []

    entries = getattr(feed, "entries", None) or []
    if not entries:
        return []

    cutoff = now - datetime.timedelta(hours=_lookback_hours(now))
    in_window = []
    for entry in entries:
        ts = _entry_time(entry)
        if ts is None or ts < cutoff:
            continue
        in_window.append((ts, entry))

    in_window.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in in_window]


def get_symbol_news(symbol: str, now: datetime.datetime) -> dict:
    """Fetch and score headlines for one symbol within the lookback window."""
    url = _SYMBOL_FEED_URL.format(symbol=symbol)
    entries = _fetch_entries_in_window(url, now)

    if not entries:
        return {"net_sentiment": None, "headline_count": 0, "headlines": []}

    titles = [entry.title for entry in entries if getattr(entry, "title", None)]
    if not titles:
        return {"net_sentiment": None, "headline_count": 0, "headlines": []}

    scores = [_analyzer.polarity_scores(title)["compound"] for title in titles]
    net_sentiment = sum(scores) / len(scores)

    return {
        "net_sentiment": net_sentiment,
        "headline_count": len(titles),
        "headlines": titles[: config.NEWS_HEADLINE_CAP],
    }


def get_market_headlines(now: datetime.datetime) -> list[str]:
    """Fetch general market headlines within the lookback window. [] on failure."""
    entries = _fetch_entries_in_window(_MARKET_FEED_URL, now)
    titles = [entry.title for entry in entries if getattr(entry, "title", None)]
    return titles[: config.NEWS_HEADLINE_CAP]


def attach_news(survivors: list[dict], now: datetime.datetime) -> list[dict]:
    """Attach a "news" key to each survivor dict. One symbol's failure never
    affects another's -- each fetch is independently wrapped."""
    for survivor in survivors:
        symbol = survivor["symbol"]
        try:
            survivor["news"] = get_symbol_news(symbol, now)
        except Exception as e:
            logger.info("attach_news failed for %s: %r", symbol, e)
            survivor["news"] = {"net_sentiment": None, "headline_count": 0, "headlines": []}
    return survivors
