import datetime
import pytz
import config
import main
import journal
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


def test_previous_scan_time_preclose_same_day():
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 12, 30))  # Wednesday preclose
    prev = main.previous_scan_time("preclose", now)
    expected = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))
    assert prev == expected


def test_previous_scan_time_premarket_rolls_back_to_prior_weekday():
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))  # Wednesday premarket
    prev = main.previous_scan_time("premarket", now)
    expected = tz.localize(datetime.datetime(2026, 7, 21, 12, 30))  # Tuesday preclose
    assert prev == expected


def test_previous_scan_time_monday_premarket_rolls_back_to_friday():
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 20, 6, 0))  # Monday premarket
    prev = main.previous_scan_time("premarket", now)
    expected = tz.localize(datetime.datetime(2026, 7, 17, 12, 30))  # Friday preclose
    assert prev == expected


def test_resolve_previous_alerts_no_open_alerts_no_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": [])
    saved = {}
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": saved.update(alerts=alerts))

    def _fail_fetch(*a, **k):
        raise AssertionError("should not fetch when there are no open alerts")

    monkeypatch.setattr(main.data, "fetch_ohlcv", _fail_fetch)

    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))
    result = main.resolve_previous_alerts(now, scan="premarket")
    assert result == []


def test_resolve_previous_alerts_fetches_only_open_tickers(monkeypatch):
    alerts = [
        {"id": "a1", "ticker": "NVDA", "status": "open", "bias": "long",
         "entry": 100.0, "stop": 95.0, "target": 110.0, "bars_open": 0, "position": None},
        {"id": "a2", "ticker": "TSLA", "status": "closed", "outcome": "win"},
    ]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": alerts)
    monkeypatch.setattr(journal, "save_journal", lambda a, path="journal.json": None)

    fetched = {}

    def _fake_fetch(symbols, timeframe):
        fetched["symbols"] = symbols
        fetched["timeframe"] = timeframe
        return {}

    monkeypatch.setattr(main.data, "fetch_ohlcv", _fake_fetch)

    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))
    main.resolve_previous_alerts(now, scan="premarket")
    assert fetched["symbols"] == ["NVDA"]
    assert fetched["timeframe"] == "30m"


def test_within_scan_tolerance_true_at_exact_scan_time():
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))
    assert main.within_scan_tolerance(now) is True


def test_within_scan_tolerance_true_within_window():
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 6, 15))  # 15 min after premarket
    assert main.within_scan_tolerance(now) is True


def test_within_scan_tolerance_false_outside_window():
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 9, 0))  # between the two scan times
    assert main.within_scan_tolerance(now) is False


def test_within_scan_tolerance_true_for_preclose():
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 12, 25))  # 5 min before preclose
    assert main.within_scan_tolerance(now) is True


def test_run_scan_exits_when_outside_tolerance(monkeypatch):
    tz = pytz.timezone("America/Vancouver")
    off_hours = tz.localize(datetime.datetime(2026, 7, 22, 9, 0))  # a Wednesday, off-hours

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return off_hours

    monkeypatch.setattr(main.datetime, "datetime", _FakeDatetime)

    def _fail(*a, **k):
        raise AssertionError("should not reach universe build when outside scan tolerance")

    monkeypatch.setattr(main.data, "build_universe", _fail)

    result = main.run_scan(dry_run=False)
    assert result == {"setups": [], "digest": None}
