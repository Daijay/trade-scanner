import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dedupe_journal


def _alert(**overrides):
    base = {
        "id": "x", "timestamp": "2026-08-07T06:38:18-07:00", "scan": "premarket",
        "ticker": "VEEV", "bias": "long", "conviction": 7, "entry": 100.0,
        "stop": 95.0, "target": 110.0, "rr": 2.0, "horizon": "swing",
        "alerted": True, "status": "open", "position": None,
        "resolved_at": None, "outcome": None, "bars_open": 0,
    }
    base.update(overrides)
    return base


def test_dedupe_keeps_single_record_untouched():
    alerts = [_alert(id="a1")]
    kept, dropped = dedupe_journal.dedupe_alerts(alerts)
    assert kept == alerts
    assert dropped == []


def test_dedupe_drops_duplicate_open_records_keeps_first():
    first = _alert(id="a1", timestamp="2026-08-07T06:38:18-07:00", conviction=6)
    second = _alert(id="a2", timestamp="2026-08-07T13:23:25-07:00", conviction=8)
    kept, dropped = dedupe_journal.dedupe_alerts([first, second])
    assert kept == [first]
    assert dropped == [second]


def test_dedupe_prefers_closed_record_over_open_duplicate():
    open_record = _alert(id="a1", timestamp="2026-08-07T06:38:18-07:00", status="open")
    closed_record = _alert(id="a2", timestamp="2026-08-07T13:23:25-07:00",
                            status="closed", outcome="win", resolved_at="2026-08-07T14:00:00-07:00")
    kept, dropped = dedupe_journal.dedupe_alerts([open_record, closed_record])
    assert kept == [closed_record]
    assert dropped == [open_record]


def test_dedupe_does_not_touch_different_ticker_scan_or_day():
    a = _alert(id="a1", ticker="VEEV", scan="premarket", timestamp="2026-08-07T06:38:18-07:00")
    b = _alert(id="a2", ticker="CRWD", scan="premarket", timestamp="2026-08-07T06:38:18-07:00")
    c = _alert(id="a3", ticker="VEEV", scan="preclose", timestamp="2026-08-07T12:39:59-07:00")
    d = _alert(id="a4", ticker="VEEV", scan="premarket", timestamp="2026-08-10T06:43:20-07:00")
    kept, dropped = dedupe_journal.dedupe_alerts([a, b, c, d])
    assert kept == [a, b, c, d]
    assert dropped == []


def test_dedupe_prefers_earliest_resolved_among_multiple_closed_duplicates():
    earlier_logged_later_resolved = _alert(
        id="a1", timestamp="2026-08-07T06:38:18-07:00",
        status="closed", outcome="win", resolved_at="2026-08-07T14:00:00-07:00",
    )
    later_logged_earlier_resolved = _alert(
        id="a2", timestamp="2026-08-07T13:23:25-07:00",
        status="closed", outcome="win", resolved_at="2026-08-07T10:00:00-07:00",
    )
    kept, dropped = dedupe_journal.dedupe_alerts([earlier_logged_later_resolved, later_logged_earlier_resolved])
    assert kept == [later_logged_earlier_resolved]
    assert dropped == [earlier_logged_later_resolved]


def test_dedupe_prefers_resolved_closed_record_over_scratch_duplicate():
    scratch = _alert(
        id="a1", timestamp="2026-08-07T06:38:18-07:00",
        status="closed", outcome="scratch", resolved_at=None,
    )
    resolved_win = _alert(
        id="a2", timestamp="2026-08-07T13:23:25-07:00",
        status="closed", outcome="win", resolved_at="2026-08-07T14:00:00-07:00",
    )
    kept, dropped = dedupe_journal.dedupe_alerts([scratch, resolved_win])
    assert kept == [resolved_win]
    assert dropped == [scratch]
