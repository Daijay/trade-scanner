import datetime
import json
import math

import config
import analyst


def _snap(close=100.0):
    return {
        "ema9": 100.0, "ema21": 99.0, "ema50": 98.0, "ema200": 95.0,
        "rsi14": 55.0, "macd_hist": 0.5, "bb_upper": 105.0, "bb_lower": 95.0,
        "atr14": 2.0, "atr_pct": 2.0, "adx14": 25.0, "vol_ratio": 1.5,
        "range_high_20d": 110.0, "range_low_20d": 90.0, "close": close,
    }


def _survivor(symbol, alignment=3):
    return {
        "symbol": symbol,
        "analysis": {
            "snapshots": {"30m": _snap(), "4h": _snap(), "daily": _snap()},
            "trends": {"30m": "bullish", "4h": "bullish", "daily": "bullish"},
            "alignment": alignment,
        },
        "score": 10.0,
        "reason": "",
        "bars_since_flip": {"30m": 1, "4h": 2, "daily": 3},
        "min_bars_since_flip": 1,
        "news": {"net_sentiment": 0.2, "headline_count": 2, "headlines": ["a", "b"]},
    }


def _setup_json(ticker):
    return {
        "ticker": ticker, "bias": "long", "conviction": 7,
        "entry": 100.0, "stop": 96.0, "target": 110.0, "rr": 2.5,
        "horizon": "swing", "timeframes": {"30m": "ok", "4h": "ok", "daily": "ok"},
        "news_read": "mixed", "reasoning": "clean breakout setup",
    }


class _FakeMessage:
    def __init__(self, text, thinking_block=False):
        blocks = []
        if thinking_block:
            blocks.append(type("ThinkingBlock", (), {"type": "thinking", "thinking": "reasoning..."})())
        blocks.append(type("TextBlock", (), {"type": "text", "text": text})())
        self.content = blocks


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0
        self.calls = []

    def create(self, **kwargs):
        self.call_count += 1
        self.calls.append(kwargs)
        resp = self._responses[min(self.call_count - 1, len(self._responses) - 1)]
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, tuple):
            text, thinking_block = resp
            return _FakeMessage(text, thinking_block=thinking_block)
        return _FakeMessage(resp)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def test_compute_horizon():
    assert analyst.compute_horizon(3) == "swing"
    assert analyst.compute_horizon(2) == "intraday"
    assert analyst.compute_horizon(1) is None
    assert analyst.compute_horizon(0) is None


def test_analyze_survivors_success_no_fences(monkeypatch):
    survivors = [_survivor("AAPL")]
    response_text = json.dumps([_setup_json("AAPL")])
    fake_client = _FakeClient([response_text])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert len(results) == 1
    assert results[0]["ticker"] == "AAPL"
    assert results[0]["bias"] == "long"
    assert fake_client.messages.call_count == 1


def test_analyze_survivors_skips_thinking_block(monkeypatch):
    """Extended-thinking responses put a non-text ThinkingBlock before the
    text block in response.content; the parser must find the text block
    rather than assuming content[0] is text (regression: AttributeError
    'ThinkingBlock' object has no attribute 'text')."""
    survivors = [_survivor("AAPL")]
    response_text = json.dumps([_setup_json("AAPL")])
    fake_client = _FakeClient([(response_text, True)])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert len(results) == 1
    assert results[0]["ticker"] == "AAPL"
    assert fake_client.messages.call_count == 1


def test_analyze_survivors_strips_fences(monkeypatch):
    survivors = [_survivor("MSFT")]
    response_text = "```json\n" + json.dumps([_setup_json("MSFT")]) + "\n```"
    fake_client = _FakeClient([response_text])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert len(results) == 1
    assert results[0]["ticker"] == "MSFT"


def test_analyze_survivors_retry_then_success(monkeypatch):
    survivors = [_survivor("TSLA")]
    good = json.dumps([_setup_json("TSLA")])
    fake_client = _FakeClient(["not json at all", good])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert fake_client.messages.call_count == 2
    assert len(results) == 1
    assert results[0]["ticker"] == "TSLA"


def test_analyze_survivors_both_calls_invalid_skips_batch(monkeypatch):
    survivors = [_survivor("NVDA")]
    fake_client = _FakeClient(["not json", "still not json"])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    logged = []

    class _FakeLogger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            logged.append((a, k))

    monkeypatch.setattr(analyst, "logger", _FakeLogger())

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert results == []
    assert fake_client.messages.call_count == 2
    assert any("batch skipped" in str(a) for a, k in logged)


def test_analyze_survivors_batching_calls_once_per_batch(monkeypatch):
    monkeypatch.setattr(config, "BATCH_SIZE", 2)
    survivors = [_survivor(f"SYM{i}") for i in range(5)]

    def _resp(**kwargs):
        pass

    fake_client = _FakeClient([
        json.dumps([_setup_json("SYM0"), _setup_json("SYM1")]),
        json.dumps([_setup_json("SYM2"), _setup_json("SYM3")]),
        json.dumps([_setup_json("SYM4")]),
    ])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert fake_client.messages.call_count == math.ceil(5 / 2)
    assert len(results) == 5


def test_analyze_survivors_mismatched_tickers_no_crash(monkeypatch):
    survivors = [_survivor("AMD"), _survivor("INTC")]
    # response has an extra unknown ticker and is missing INTC
    response_text = json.dumps([_setup_json("AMD"), _setup_json("UNKNOWN")])
    fake_client = _FakeClient([response_text])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    tickers = {r["ticker"] for r in results}
    assert tickers == {"AMD", "UNKNOWN"}
    assert fake_client.messages.call_count == 1


def test_analyze_survivors_empty_list_no_api_call(monkeypatch):
    called = {"n": 0}

    def _make_client():
        called["n"] += 1
        return _FakeClient([json.dumps([])])

    monkeypatch.setattr(analyst.anthropic, "Anthropic", _make_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors([], now)

    assert results == []
    assert called["n"] == 0
