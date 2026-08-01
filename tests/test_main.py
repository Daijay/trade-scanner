import datetime
import pytz
import config
import main
import journal
import notify
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


def test_run_scan_force_run_bypasses_tolerance_but_not_weekday(monkeypatch):
    """force_run must skip only within_scan_tolerance; weekday/holiday guards
    still apply so it can never be used to run outside real market days."""
    tz = pytz.timezone("America/Vancouver")
    weekend = tz.localize(datetime.datetime(2026, 7, 25, 9, 0))  # a Saturday, off-hours

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return weekend

    monkeypatch.setattr(main.datetime, "datetime", _FakeDatetime)

    def _fail(*a, **k):
        raise AssertionError("should not reach universe build on a non-weekday even with force_run")

    monkeypatch.setattr(main.data, "build_universe", _fail)

    result = main.run_scan(dry_run=False, force_run=True)
    assert result == {"setups": [], "digest": None}


def test_run_scan_force_run_reaches_pipeline_outside_tolerance(monkeypatch):
    import journal as journal_mod
    import filter as filter_mod
    import news
    import analyst
    import digest

    tz = pytz.timezone("America/Vancouver")
    off_hours = tz.localize(datetime.datetime(2026, 7, 22, 9, 0))  # a Wednesday, off-hours

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return off_hours

    monkeypatch.setattr(main.datetime, "datetime", _FakeDatetime)

    monkeypatch.setattr(journal_mod, "load_journal", lambda path="journal.json": [])
    monkeypatch.setattr(journal_mod, "save_journal", lambda alerts, path="journal.json": None)
    monkeypatch.setattr(journal_mod, "log_alerts", lambda setups, scan, now: None)
    monkeypatch.setattr(journal_mod, "compute_stats", lambda alerts: {})
    monkeypatch.setattr(journal_mod, "should_compute_stats", lambda alerts: False)

    monkeypatch.setattr(main.data, "build_universe", lambda: ["NVDA"])
    monkeypatch.setattr(main.data, "fetch_all_timeframes", lambda symbols: {"NVDA": {}})
    monkeypatch.setattr(
        filter_mod, "run_filter",
        lambda frames: ([{"symbol": "NVDA", "analysis": {"alignment": "long"}}], []),
    )
    monkeypatch.setattr(news, "attach_news", lambda survivors, now: survivors)
    monkeypatch.setattr(
        analyst, "analyze_survivors",
        lambda survivors, now: [{"symbol": "NVDA", "conviction": 8}],
    )
    monkeypatch.setattr(main.display, "flash_ticker", lambda *a, **k: None)
    monkeypatch.setattr(main.display, "print_survivor_table", lambda *a, **k: None)
    monkeypatch.setattr(main.data, "fetch_market_context", lambda: "")
    monkeypatch.setattr(digest, "build_digest", lambda *a, **k: "DIGEST")

    result = main.run_scan(dry_run=False, force_run=True)
    assert result["digest"] == "DIGEST"


def test_main_force_run_flag_parsed(monkeypatch):
    import sys

    captured = {}

    def _fake_run_scan(dry_run, limit, force_run):
        captured["force_run"] = force_run
        return {"setups": [], "digest": None}

    monkeypatch.setattr(main, "run_scan", _fake_run_scan)
    monkeypatch.setattr(sys, "argv", ["main.py", "--force-run"])
    main.main()

    assert captured["force_run"] is True


def test_run_scan_wires_market_context_into_build_digest(monkeypatch):
    import journal as journal_mod
    import filter as filter_mod
    import news
    import analyst
    import digest

    monkeypatch.setattr(journal_mod, "load_journal", lambda path="journal.json": [])
    monkeypatch.setattr(journal_mod, "save_journal", lambda alerts, path="journal.json": None)
    monkeypatch.setattr(journal_mod, "log_alerts", lambda setups, scan, now: None)
    monkeypatch.setattr(journal_mod, "compute_stats", lambda alerts: {})
    monkeypatch.setattr(journal_mod, "should_compute_stats", lambda alerts: False)

    monkeypatch.setattr(main.data, "build_universe", lambda: ["NVDA"])
    monkeypatch.setattr(main.data, "fetch_all_timeframes", lambda symbols: {"NVDA": {}})
    monkeypatch.setattr(
        filter_mod, "run_filter",
        lambda frames: ([{"symbol": "NVDA", "analysis": {"alignment": "long"}}], []),
    )
    monkeypatch.setattr(news, "attach_news", lambda survivors, now: survivors)
    monkeypatch.setattr(
        analyst, "analyze_survivors",
        lambda survivors, now: [{"symbol": "NVDA", "conviction": 8}],
    )
    monkeypatch.setattr(main.display, "flash_ticker", lambda *a, **k: None)
    monkeypatch.setattr(main.display, "print_survivor_table", lambda *a, **k: None)

    monkeypatch.setattr(main.data, "fetch_market_context", lambda: "SPY +0.3% | QQQ +0.5% | VIX 14.2")

    captured = {}

    def _fake_build_digest(scan, now, alertable, scan_counts, stats, market_context=""):
        captured["market_context"] = market_context
        return "DIGEST"

    monkeypatch.setattr(digest, "build_digest", _fake_build_digest)

    result = main.run_scan(dry_run=True)
    assert captured["market_context"] == "SPY +0.3% | QQQ +0.5% | VIX 14.2"
    assert result["digest"] == "DIGEST"


def test_main_passes_run_scan_digest_to_notify(monkeypatch):
    """Regression test: main() must forward run_scan()'s real digest output
    to notify.send_digest, not a stub or hardcoded placeholder."""
    import sys
    sentinel_digest = "REAL DIGEST CONTENT - not a placeholder"
    monkeypatch.setattr(
        main, "run_scan",
        lambda dry_run, limit, force_run: {"setups": [], "digest": sentinel_digest},
    )

    captured = {}
    monkeypatch.setattr(notify, "send_digest", lambda msg: captured.update(sent=msg))

    monkeypatch.setattr(sys, "argv", ["main.py"])
    main.main()

    assert captured["sent"] == sentinel_digest
