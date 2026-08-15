# tests/test_journal.py
import datetime

import pandas as pd
import pytest

import config
import journal


def _bars(rows):
    """rows: list of (timestamp, High, Low, Close) -> DataFrame indexed by timestamp."""
    idx = [r[0] for r in rows]
    data = {
        "High": [r[1] for r in rows],
        "Low": [r[2] for r in rows],
        "Close": [r[3] for r in rows],
    }
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx))


def _alert(**overrides):
    base = {
        "id": "2026-07-22T06:00-NVDA",
        "timestamp": "2026-07-22T06:00:00",
        "scan": "premarket",
        "ticker": "NVDA",
        "bias": "long",
        "conviction": 8,
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "rr": 2.0,
        "horizon": "swing",
        "alerted": True,
        "status": "open",
        "position": None,
        "resolved_at": None,
        "outcome": None,
        "bars_open": 0,
    }
    base.update(overrides)
    return base


# -- log_alerts ---------------------------------------------------------

def test_log_alerts_builds_records(monkeypatch):
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": [])
    saved = {}
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": saved.setdefault("alerts", alerts))

    setups = [
        {"ticker": "NVDA", "bias": "long", "conviction": 8, "entry": 100.0,
         "stop": 95.0, "target": 110.0, "rr": 2.0, "horizon": "swing"},
        {"ticker": "AMD", "bias": "short", "conviction": 7, "entry": 50.0,
         "stop": 53.0, "target": 44.0, "rr": 2.0, "horizon": "intraday"},
    ]
    now = datetime.datetime(2026, 7, 22, 6, 0)

    result = journal.log_alerts(setups, "premarket", now)

    assert len(result) == 2
    nvda = result[0]
    assert nvda["id"] == "2026-07-22T06:00-NVDA"
    assert nvda["scan"] == "premarket"
    assert nvda["status"] == "open"
    assert nvda["alerted"] is True
    assert nvda["position"] is None
    assert nvda["resolved_at"] is None
    assert nvda["outcome"] is None
    assert nvda["bars_open"] == 0
    assert nvda["ticker"] == "NVDA"
    assert nvda["bias"] == "long"
    assert nvda["conviction"] == 8
    assert nvda["entry"] == 100.0
    assert nvda["stop"] == 95.0
    assert nvda["target"] == 110.0
    assert nvda["rr"] == 2.0
    assert nvda["horizon"] == "swing"

    amd = result[1]
    assert amd["id"] == "2026-07-22T06:00-AMD"

    assert saved["alerts"] == result


def test_log_alerts_skips_ticker_already_logged_same_scan_same_day(monkeypatch):
    existing = [_alert(
        id="2026-07-22T06:00-NVDA", timestamp="2026-07-22T06:00:00",
        scan="premarket", ticker="NVDA",
    )]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": list(existing))
    saved = {}
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": saved.setdefault("alerts", alerts))

    setups = [
        {"ticker": "NVDA", "bias": "short", "conviction": 6, "entry": 90.0,
         "stop": 95.0, "target": 80.0, "rr": 2.0, "horizon": "intraday"},
        {"ticker": "AMD", "bias": "long", "conviction": 7, "entry": 50.0,
         "stop": 47.0, "target": 56.0, "rr": 2.0, "horizon": "swing"},
    ]
    now = datetime.datetime(2026, 7, 22, 6, 38)  # later, same day, same scan slot

    result = journal.log_alerts(setups, "premarket", now)

    assert [r["ticker"] for r in result] == ["AMD"]
    assert len(saved["alerts"]) == 2  # original NVDA record + new AMD, no duplicate NVDA


def test_log_alerts_allows_same_ticker_different_scan_same_day(monkeypatch):
    existing = [_alert(
        id="2026-07-22T06:00-NVDA", timestamp="2026-07-22T06:00:00",
        scan="premarket", ticker="NVDA",
    )]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": list(existing))
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": None)

    setups = [{"ticker": "NVDA", "bias": "long", "conviction": 8, "entry": 100.0,
               "stop": 95.0, "target": 110.0, "rr": 2.0, "horizon": "swing"}]
    now = datetime.datetime(2026, 7, 22, 12, 30)  # same day, preclose slot

    result = journal.log_alerts(setups, "preclose", now)

    assert [r["ticker"] for r in result] == ["NVDA"]


def test_log_alerts_allows_same_ticker_scan_different_day(monkeypatch):
    existing = [_alert(
        id="2026-07-21T06:00-NVDA", timestamp="2026-07-21T06:00:00",
        scan="premarket", ticker="NVDA",
    )]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": list(existing))
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": None)

    setups = [{"ticker": "NVDA", "bias": "long", "conviction": 8, "entry": 100.0,
               "stop": 95.0, "target": 110.0, "rr": 2.0, "horizon": "swing"}]
    now = datetime.datetime(2026, 7, 22, 6, 0)  # next day, same scan slot

    result = journal.log_alerts(setups, "premarket", now)

    assert [r["ticker"] for r in result] == ["NVDA"]


# -- resolve_alert --------------------------------------------------------

def test_resolve_alert_win_long():
    alert = _alert(bias="long", entry=100.0, stop=95.0, target=110.0)
    bars = _bars([
        (datetime.datetime(2026, 7, 22, 7, 0), 105.0, 99.0, 104.0),
        (datetime.datetime(2026, 7, 22, 8, 0), 111.0, 104.0, 110.5),
    ])

    result = journal.resolve_alert(alert, bars)

    assert result["outcome"] == "win"
    assert result["status"] == "closed"
    assert result["resolved_at"] is not None


def test_resolve_alert_loss_long():
    alert = _alert(bias="long", entry=100.0, stop=95.0, target=110.0)
    bars = _bars([
        (datetime.datetime(2026, 7, 22, 7, 0), 102.0, 99.0, 101.0),
        (datetime.datetime(2026, 7, 22, 8, 0), 101.0, 94.0, 95.5),
    ])

    result = journal.resolve_alert(alert, bars)

    assert result["outcome"] == "loss"
    assert result["status"] == "closed"


def test_resolve_alert_ambiguous_same_bar():
    alert = _alert(bias="long", entry=100.0, stop=95.0, target=110.0)
    bars = _bars([
        (datetime.datetime(2026, 7, 22, 7, 0), 112.0, 93.0, 100.0),
    ])

    result = journal.resolve_alert(alert, bars)

    assert result["outcome"] == "ambiguous"
    assert result["status"] == "closed"


def test_resolve_alert_scratch_after_max_bars():
    alert = _alert(bias="long", entry=100.0, stop=95.0, target=110.0)
    n = config.SCRATCH_AFTER_BARS + 1
    rows = [
        (datetime.datetime(2026, 7, 22, 7, 0) + datetime.timedelta(hours=i), 101.0, 99.0, 100.5)
        for i in range(n)
    ]
    bars = _bars(rows)

    result = journal.resolve_alert(alert, bars)

    assert result["outcome"] == "scratch"
    assert result["status"] == "closed"


def test_resolve_alert_still_open_price_above_entry():
    alert = _alert(bias="long", entry=100.0, stop=95.0, target=110.0)
    bars = _bars([
        (datetime.datetime(2026, 7, 22, 7, 0), 103.0, 99.0, 102.0),
    ])

    result = journal.resolve_alert(alert, bars)

    assert result["status"] == "open"
    assert result["outcome"] is None
    assert result["position"] == "upper"
    assert result["bars_open"] == 1


def test_resolve_alert_still_open_price_below_entry():
    alert = _alert(bias="long", entry=100.0, stop=95.0, target=110.0)
    bars = _bars([
        (datetime.datetime(2026, 7, 22, 7, 0), 100.5, 97.0, 98.0),
    ])

    result = journal.resolve_alert(alert, bars)

    assert result["status"] == "open"
    assert result["position"] == "lower"


def test_resolve_alert_short_win():
    alert = _alert(bias="short", entry=50.0, stop=53.0, target=44.0)
    bars = _bars([
        (datetime.datetime(2026, 7, 22, 7, 0), 50.5, 43.5, 44.5),
    ])

    result = journal.resolve_alert(alert, bars)

    assert result["outcome"] == "win"
    assert result["status"] == "closed"


def test_resolve_alert_short_loss():
    alert = _alert(bias="short", entry=50.0, stop=53.0, target=44.0)
    bars = _bars([
        (datetime.datetime(2026, 7, 22, 7, 0), 53.5, 49.0, 53.2),
    ])

    result = journal.resolve_alert(alert, bars)

    assert result["outcome"] == "loss"
    assert result["status"] == "closed"


# -- resolve_open_alerts --------------------------------------------------

def test_resolve_open_alerts_mixed():
    open_with_bars = _alert(ticker="NVDA", bias="long", entry=100.0, stop=95.0, target=110.0)
    open_no_bars = _alert(ticker="AMD", bias="long", entry=50.0, stop=45.0, target=60.0)
    already_closed = _alert(ticker="TSLA", status="closed", outcome="win")

    bars_by_ticker = {
        "NVDA": _bars([(datetime.datetime(2026, 7, 22, 7, 0), 111.0, 99.0, 110.5)]),
    }

    alerts = [open_with_bars, open_no_bars, already_closed]
    result = journal.resolve_open_alerts(alerts, bars_by_ticker)

    assert result[0]["outcome"] == "win"
    assert result[0]["status"] == "closed"
    # untouched -- no bars supplied
    assert result[1]["status"] == "open"
    assert result[1]["outcome"] is None
    assert result[1]["bars_open"] == 0
    # untouched -- was already closed
    assert result[2]["outcome"] == "win"


# -- compute_stats ---------------------------------------------------------

def _closed_alert(outcome, rr=2.0, conviction=8, scan="premarket", bias="long", horizon="swing"):
    return _alert(status="closed", outcome=outcome, rr=rr, conviction=conviction,
                  scan=scan, bias=bias, horizon=horizon)


def test_compute_stats_hand_computable():
    alerts = (
        [_closed_alert("win", rr=2.0) for _ in range(3)]
        + [_closed_alert("loss") for _ in range(2)]
        + [_closed_alert("scratch")]
        + [_closed_alert("ambiguous")]
    )

    stats = journal.compute_stats(alerts)

    # wins=3, losses=2+1(ambiguous)=3, scratches=1, total_resolved=7
    assert stats["wins"] == 3
    assert stats["losses"] == 3
    assert stats["scratches"] == 1
    assert stats["total_resolved"] == 7
    assert stats["hit_rate"] == pytest.approx(3 / 6)
    assert stats["adj_hit_rate"] == pytest.approx((3 + 0.5 * 1) / 7)
    assert stats["scratch_rate"] == pytest.approx(1 / 7)
    # avg_rr: 3 wins @ +2.0, 2 losses @ -1.0, 1 scratch @ 0.0, 1 ambiguous @ -1.0
    expected_avg_rr = (3 * 2.0 + 2 * -1.0 + 1 * 0.0 + 1 * -1.0) / 7
    assert stats["avg_rr"] == pytest.approx(expected_avg_rr)


def test_compute_stats_ambiguous_counts_as_loss():
    # 1 win, 1 ambiguous: if ambiguous were a win, hit_rate would be 1.0;
    # treated as a loss, hit_rate is 0.5.
    alerts = [_closed_alert("win"), _closed_alert("ambiguous")]

    stats = journal.compute_stats(alerts)

    assert stats["hit_rate"] == pytest.approx(0.5)
    assert stats["losses"] == 1
    assert stats["wins"] == 1


def test_compute_stats_empty():
    stats = journal.compute_stats([])

    assert stats["hit_rate"] is None
    assert stats["adj_hit_rate"] is None
    assert stats["scratch_rate"] is None
    assert stats["avg_rr"] is None
    assert stats["wins"] == 0
    assert stats["losses"] == 0
    assert stats["scratches"] == 0
    assert stats["total_resolved"] == 0


def test_compute_stats_breakdowns():
    alerts = (
        [_closed_alert("win", conviction=9, scan="premarket", bias="long", horizon="swing") for _ in range(2)]
        + [_closed_alert("loss", conviction=6, scan="preclose", bias="short", horizon="intraday") for _ in range(2)]
    )

    stats = journal.compute_stats(alerts)
    b = stats["breakdowns"]

    assert b["by_conviction"][9]["hit_rate"] == pytest.approx(1.0)
    assert b["by_conviction"][6]["hit_rate"] == pytest.approx(0.0)
    assert b["by_conviction"][7]["total_resolved"] == 0
    assert b["by_conviction"][7]["hit_rate"] is None

    assert b["by_scan"]["premarket"]["hit_rate"] == pytest.approx(1.0)
    assert b["by_scan"]["preclose"]["hit_rate"] == pytest.approx(0.0)

    assert b["by_direction"]["long"]["hit_rate"] == pytest.approx(1.0)
    assert b["by_direction"]["short"]["hit_rate"] == pytest.approx(0.0)

    assert b["by_horizon"]["swing"]["hit_rate"] == pytest.approx(1.0)
    assert b["by_horizon"]["intraday"]["hit_rate"] == pytest.approx(0.0)


# -- should_compute_stats ---------------------------------------------------

def test_should_compute_stats_exactly_stats_every():
    alerts = [_closed_alert("win") for _ in range(config.STATS_EVERY)]
    assert journal.should_compute_stats(alerts) is True


def test_should_compute_stats_multiple():
    alerts = [_closed_alert("win") for _ in range(config.STATS_EVERY * 2)]
    assert journal.should_compute_stats(alerts) is True


def test_should_compute_stats_non_multiple():
    alerts = [_closed_alert("win") for _ in range(config.STATS_EVERY - 1)]
    assert journal.should_compute_stats(alerts) is False


def test_should_compute_stats_zero_closed():
    alerts = [_alert(status="open")]
    assert journal.should_compute_stats(alerts) is False


# -- load_journal / save_journal --------------------------------------------

def test_load_journal_missing_file_returns_empty(tmp_path):
    path = str(tmp_path / "nope.json")
    assert journal.load_journal(path) == []


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "journal.json")
    alerts = [_alert(), _alert(ticker="AMD")]

    journal.save_journal(alerts, path)
    result = journal.load_journal(path)

    assert result == alerts


class _FakeMessage:
    def __init__(self, text):
        self.content = [type("TextBlock", (), {"type": "text", "text": text})()]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self._text)


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def _sample_stats():
    return {
        "hit_rate": 0.6, "adj_hit_rate": 0.55, "scratch_rate": 0.2, "avg_rr": 0.8,
        "wins": 12, "losses": 8, "scratches": 5, "total_resolved": 25,
        "breakdowns": {
            "by_conviction": {6: journal._empty_rate_block(), 7: journal._empty_rate_block(),
                              8: journal._empty_rate_block(), 9: journal._empty_rate_block(),
                              10: journal._empty_rate_block()},
            "by_scan": {"premarket": journal._empty_rate_block(), "preclose": journal._empty_rate_block()},
            "by_direction": {"long": journal._empty_rate_block(), "short": journal._empty_rate_block()},
            "by_horizon": {"intraday": journal._empty_rate_block(), "swing": journal._empty_rate_block()},
        },
    }


def test_summarize_stats_returns_claude_text(monkeypatch):
    fake_client = _FakeClient("Hit rate is trending up on swing setups; scratch rate is elevated on intraday longs.")
    monkeypatch.setattr(journal.anthropic, "Anthropic", lambda: fake_client)
    result = journal.summarize_stats(_sample_stats())
    assert result == "Hit rate is trending up on swing setups; scratch rate is elevated on intraday longs."


def test_summarize_stats_disables_thinking(monkeypatch):
    fake_client = _FakeClient("summary text")
    monkeypatch.setattr(journal.anthropic, "Anthropic", lambda: fake_client)
    journal.summarize_stats(_sample_stats())
    kwargs = fake_client.messages.calls[0]
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["model"] == config.MODEL


def test_summarize_stats_prompt_includes_stats_json(monkeypatch):
    fake_client = _FakeClient("summary text")
    monkeypatch.setattr(journal.anthropic, "Anthropic", lambda: fake_client)
    stats = _sample_stats()
    journal.summarize_stats(stats)
    kwargs = fake_client.messages.calls[0]
    prompt = kwargs["messages"][0]["content"]
    assert '"hit_rate": 0.6' in prompt
    assert "probability" in prompt.lower() or "certain" in prompt.lower()


def test_summarize_stats_api_failure_returns_none(monkeypatch):
    class _RaisingMessages:
        def create(self, **kwargs):
            raise RuntimeError("network down")

    class _RaisingClient:
        def __init__(self):
            self.messages = _RaisingMessages()

    monkeypatch.setattr(journal.anthropic, "Anthropic", lambda: _RaisingClient())
    assert journal.summarize_stats(_sample_stats()) is None
