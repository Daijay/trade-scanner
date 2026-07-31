# tests/test_analyst_digest_integration.py
"""Regression coverage for a real production crash: digest.py's
_timeframe_checks assumed every setup's per-timeframe value was a dict, but
real (non-mocked) Claude output returned at least one timeframe as a bare
trend string. analyst.py's own unit tests hand-constructed perfect dicts
and never caught this, since the fixture was never fed through digest.py.
This module wires analyst.py's real parsing path straight into
digest.build_digest to catch this whole class of shape-mismatch bug."""
import datetime
import json

import analyst
import digest


def _survivor(symbol):
    snap = {
        "ema9": 100.0, "ema21": 99.0, "ema50": 98.0, "ema200": 95.0,
        "rsi14": 55.0, "macd_hist": 0.5, "bb_upper": 105.0, "bb_lower": 95.0,
        "atr14": 2.0, "atr_pct": 2.0, "adx14": 25.0, "vol_ratio": 1.5,
        "range_high_20d": 110.0, "range_low_20d": 90.0, "close": 100.0,
    }
    return {
        "symbol": symbol,
        "analysis": {
            "snapshots": {"30m": snap, "4h": snap, "daily": snap},
            "trends": {"30m": "bullish", "4h": "bullish", "daily": "bullish"},
            "alignment": 3,
        },
        "score": 10.0,
        "reason": "",
        "bars_since_flip": {"30m": 1, "4h": 2, "daily": 3},
        "min_bars_since_flip": 1,
        "news": {"net_sentiment": 0.2, "headline_count": 2, "headlines": ["a"]},
    }


class _FakeMessage:
    def __init__(self, text):
        self.content = [type("TextBlock", (), {"type": "text", "text": text})()]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        return _FakeMessage(self._text)


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def test_real_shaped_claude_response_survives_into_digest(monkeypatch):
    """Real captured response shape: per-timeframe values are bare trend
    strings (not {"trend": ...} objects), and the batch includes one extra
    hallucinated ticker not present in the survivors list -- matching both
    anomalies observed in the actual production crash log ("31 setups from
    30 survivors" + AttributeError on 'trend')."""
    real_shaped_response = json.dumps([
        {
            "ticker": "NVDA", "bias": "long", "conviction": 8,
            "entry": 101.5, "stop": 97.0, "target": 112.0, "rr": 2.4,
            "horizon": "swing",
            "timeframes": {"30m": "bullish", "4h": "up", "daily": "flat"},
            "news_read": "mixed sentiment ahead of earnings",
            "reasoning": "Breakout above 21ema with volume confirmation.",
        },
        {
            "ticker": "GHOST", "bias": "short", "conviction": 5,
            "entry": 50.0, "stop": 52.0, "target": 45.0, "rr": 2.5,
            "horizon": "intraday",
            "timeframes": {"30m": "down", "4h": "down", "daily": "flat"},
            "news_read": "no data", "reasoning": "Not requested by survivors.",
        },
    ])
    fake_client = _FakeClient(real_shaped_response)
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    setups = analyst.analyze_survivors([_survivor("NVDA")], now)

    # The hallucinated extra ticker must never reach digest as an alert.
    assert [s["ticker"] for s in setups] == ["NVDA"]

    scan_counts = {"scanned": 100, "filtered": 5, "alerts": len(setups)}
    msg = digest.build_digest("premarket", now, setups, scan_counts, None)

    assert "NVDA" in msg
    assert "GHOST" not in msg
