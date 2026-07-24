# tests/test_news.py
import datetime

import feedparser

import config
import news


def _entry(title, hours_ago, now):
    ts = now - datetime.timedelta(hours=hours_ago)
    return feedparser.FeedParserDict(
        title=title,
        published_parsed=ts.utctimetuple(),
    )


class _FakeFeed:
    def __init__(self, entries, bozo=False):
        self.entries = entries
        self.bozo = bozo


def test_get_symbol_news_headlines_in_window(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)  # Tuesday
    entries = [
        _entry("great news for AAPL", 1, now),
        _entry("stock soars on earnings beat", 5, now),
    ]
    monkeypatch.setattr(feedparser, "parse", lambda url: _FakeFeed(entries))

    result = news.get_symbol_news("AAPL", now)

    assert result["headline_count"] == 2
    assert result["headlines"] == ["great news for AAPL", "stock soars on earnings beat"]
    assert isinstance(result["net_sentiment"], float)
    assert -1.0 <= result["net_sentiment"] <= 1.0
    assert result["net_sentiment"] > 0  # obviously positive headlines


def test_get_symbol_news_caps_headlines_and_orders_most_recent_first(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)  # Tuesday
    entries = [
        _entry("headline 6 (oldest)", 6, now),
        _entry("headline 1 (newest)", 1, now),
        _entry("headline 4", 4, now),
        _entry("headline 2", 2, now),
        _entry("headline 5", 5, now),
        _entry("headline 3", 3, now),
    ]
    monkeypatch.setattr(feedparser, "parse", lambda url: _FakeFeed(entries))

    result = news.get_symbol_news("AAPL", now)

    assert result["headline_count"] == 6  # uncapped count
    assert len(result["headlines"]) == 5  # capped list
    assert result["headlines"] == [
        "headline 1 (newest)",
        "headline 2",
        "headline 3",
        "headline 4",
        "headline 5",
    ]


def test_get_symbol_news_excludes_headlines_outside_window(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)  # Tuesday, 24h window
    entries = [
        _entry("inside window", 1, now),
        _entry("outside window", 30, now),
    ]
    monkeypatch.setattr(feedparser, "parse", lambda url: _FakeFeed(entries))

    result = news.get_symbol_news("AAPL", now)

    assert result["headline_count"] == 1
    assert result["headlines"] == ["inside window"]


def test_get_symbol_news_empty_bozo_feed(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(feedparser, "parse", lambda url: _FakeFeed([], bozo=True))

    result = news.get_symbol_news("AAPL", now)

    assert result == {"net_sentiment": None, "headline_count": 0, "headlines": []}


def test_get_symbol_news_feed_raises_is_handled(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)

    def _raise(url):
        raise RuntimeError("connection boom")

    monkeypatch.setattr(feedparser, "parse", _raise)

    result = news.get_symbol_news("AAPL", now)

    assert result == {"net_sentiment": None, "headline_count": 0, "headlines": []}


def test_lookback_hours_monday_vs_other():
    monday = datetime.datetime(2026, 7, 20, 12, 0, tzinfo=datetime.timezone.utc)
    tuesday = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)
    friday = datetime.datetime(2026, 7, 24, 12, 0, tzinfo=datetime.timezone.utc)

    assert news._lookback_hours(monday) == config.NEWS_WINDOW_HOURS_MONDAY
    assert news._lookback_hours(tuesday) == config.NEWS_WINDOW_HOURS
    assert news._lookback_hours(friday) == config.NEWS_WINDOW_HOURS


def test_get_market_headlines_capped(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)
    entries = [_entry(f"market headline {i}", i, now) for i in range(1, 8)]
    monkeypatch.setattr(feedparser, "parse", lambda url: _FakeFeed(entries))

    result = news.get_market_headlines(now)

    assert len(result) == config.NEWS_HEADLINE_CAP
    assert result[0] == "market headline 1"


def test_get_market_headlines_failure_returns_empty(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)

    def _raise(url):
        raise RuntimeError("boom")

    monkeypatch.setattr(feedparser, "parse", _raise)

    assert news.get_market_headlines(now) == []


def test_attach_news_one_symbol_fails_others_unaffected(monkeypatch):
    now = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)

    def _fake_parse(url):
        if "FAIL" in url:
            raise RuntimeError("feed down")
        return _FakeFeed([_entry("good news", 1, now)])

    monkeypatch.setattr(feedparser, "parse", _fake_parse)

    survivors = [
        {"symbol": "FAIL", "score": 1.0},
        {"symbol": "OK", "score": 2.0},
    ]

    result = news.attach_news(survivors, now)

    assert len(result) == 2
    assert result[0]["symbol"] == "FAIL"
    assert result[0]["news"] == {"net_sentiment": None, "headline_count": 0, "headlines": []}
    assert result[1]["symbol"] == "OK"
    assert result[1]["news"]["headline_count"] == 1
    assert result[1]["news"]["headlines"] == ["good news"]
    # unrelated fields preserved
    assert result[0]["score"] == 1.0
    assert result[1]["score"] == 2.0
