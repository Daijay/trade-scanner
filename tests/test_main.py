import datetime
import pytz
import config
from main import is_weekday, market_open_today


def test_is_weekday_true_for_wednesday():
    tz = pytz.timezone("America/Vancouver")
    wed = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))   # a Wednesday
    assert is_weekday(wed) is True


def test_is_weekday_false_for_saturday():
    tz = pytz.timezone("America/Vancouver")
    sat = tz.localize(datetime.datetime(2026, 7, 25, 6, 0))   # a Saturday
    assert is_weekday(sat) is False


def test_market_open_today_false_on_holiday(monkeypatch):
    monkeypatch.setattr(config, "MARKET_HOLIDAYS", ["2026-07-22"])
    tz = pytz.timezone("America/Vancouver")
    dt = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))
    assert market_open_today(dt) is False


def test_market_open_today_true_premarket(monkeypatch):
    monkeypatch.setattr(config, "MARKET_HOLIDAYS", ["2026-01-01"])
    tz = pytz.timezone("America/Vancouver")
    dt = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))  # pre-market hour, regression test
    assert market_open_today(dt) is True


def test_market_open_today_true_midsession(monkeypatch):
    monkeypatch.setattr(config, "MARKET_HOLIDAYS", ["2026-01-01"])
    tz = pytz.timezone("America/Vancouver")
    dt = tz.localize(datetime.datetime(2026, 7, 22, 10, 30))
    assert market_open_today(dt) is True
