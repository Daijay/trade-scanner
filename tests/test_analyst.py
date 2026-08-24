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
    """A hallucinated ticker outside this batch's survivors must not become
    an alert (regression: real Claude output returned 31 setups for 30
    survivors -- one extra, unverified-against-filters ticker)."""
    survivors = [_survivor("AMD"), _survivor("INTC")]
    # response has an extra unknown ticker and is missing INTC
    response_text = json.dumps([_setup_json("AMD"), _setup_json("UNKNOWN")])
    fake_client = _FakeClient([response_text])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    tickers = {r["ticker"] for r in results}
    assert tickers == {"AMD"}
    assert fake_client.messages.call_count == 1


def test_analyze_survivors_duplicate_ticker_deduped(monkeypatch):
    survivors = [_survivor("AMD")]
    response_text = json.dumps([_setup_json("AMD"), _setup_json("AMD")])
    fake_client = _FakeClient([response_text])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert len(results) == 1


def test_analyze_survivors_normalizes_bare_string_timeframes(monkeypatch):
    """Regression: real Claude output returned each timeframe as a bare
    trend string (e.g. "up") instead of {"trend": "up"}, which crashed
    digest.py's _timeframe_checks with AttributeError. analyst.py must
    normalize this shape before it leaves analyze_survivors."""
    survivors = [_survivor("AAPL")]
    setup = _setup_json("AAPL")
    setup["timeframes"] = {"30m": "bullish", "4h": "down", "daily": "flat"}
    response_text = json.dumps([setup])
    fake_client = _FakeClient([response_text])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    results = analyst.analyze_survivors(survivors, now)

    assert len(results) == 1
    tfs = results[0]["timeframes"]
    assert tfs["30m"] == {"trend": "up"}
    assert tfs["4h"] == {"trend": "down"}
    assert tfs["daily"] == {"trend": "flat"}


def test_call_claude_sends_cached_system_prompt_matching_prior_content(monkeypatch):
    """Verifies the API call structure: static instructions go in a cached
    system block, the per-call payload goes in the user message, and
    reassembling system + user reproduces byte-for-byte the single-prompt
    text this codebase sent before caching was introduced (i.e. prompt
    content is unchanged, only how it's split across the request)."""
    survivors = [_survivor("AAPL")]
    response_text = json.dumps([_setup_json("AAPL")])
    fake_client = _FakeClient([response_text])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    analyst.analyze_survivors(survivors, now)

    kwargs = fake_client.messages.calls[0]
    system_blocks = kwargs["system"]
    assert len(system_blocks) == 1
    assert system_blocks[0]["type"] == "text"
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

    messages = kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"

    payloads = [analyst._build_payload(s) for s in survivors]
    reassembled = system_blocks[0]["text"] + "\n\n" + messages[0]["content"]
    forbidden = ", ".join(analyst._FORBIDDEN_WORDS)
    expected = f"""You are a trading analyst. You will be given a JSON array of compact
per-symbol technical + news payloads for symbols that already passed a hard
technical filter. For EACH symbol, produce a trade setup.

Respond with ONLY a JSON array, no prose, no markdown code fences, no
commentary before or after. The array must contain one object per input
symbol, matching this exact schema:

{{"ticker": str, "bias": "long"|"short", "conviction": int (0-10),
 "entry": float, "stop": float, "target": float, "rr": float,
 "horizon": "swing"|"intraday",
 "timeframes": {{"30m": {{"trend": "up"|"down"|"flat"}},
                 "4h": {{"trend": "up"|"down"|"flat"}},
                 "daily": {{"trend": "up"|"down"|"flat"}}}},
 "news_read": str, "reasoning": str}}

Each of "30m"/"4h"/"daily" MUST be an object with a "trend" key as shown --
never a bare string.

Rules:
- Stops MUST be derived from ATR (atr14 in the payload), not arbitrary round numbers.
- Each symbol's "horizon" is already computed for you in the input payload
  (from its alignment score) -- use that exact value, do not decide it yourself.
- If a setup's reward:risk (rr = (target-entry)/(entry-stop) in absolute
  terms) is below {config.MIN_RR}, you must still return the object but with
  conviction: 0.
- Conviction is a RELATIVE RANKING WITHIN THIS BATCH ONLY -- it is not a
  probability of profit, not a guarantee, not a forecast.
- If a symbol has no clean setup, return it with conviction: 0 rather than
  inventing one.
- In "reasoning", never use any of these words or phrases: {forbidden}.
- bars_since_flip / min_bars_since_flip fields are informational context only.

Input symbols:
{json.dumps(payloads)}
"""
    assert reassembled == expected


def test_system_prompt_stable_across_retry_and_batches(monkeypatch):
    """The cached system block must be identical across the retry call
    within a batch (only the user message should carry the strict-mode
    nudge) and across batches with different survivor payloads -- that
    stability is what makes prompt caching actually hit."""
    survivors = [_survivor("AAPL"), _survivor("MSFT")]
    good = json.dumps([_setup_json("AAPL"), _setup_json("MSFT")])
    fake_client = _FakeClient(["not json", good])
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda: fake_client)

    now = datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc)
    analyst.analyze_survivors(survivors, now)

    assert fake_client.messages.call_count == 2
    first_call, second_call = fake_client.messages.calls
    assert first_call["system"] == second_call["system"]
    assert first_call["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Only the user message differs (retry nudge appended).
    assert first_call["messages"][0]["content"] != second_call["messages"][0]["content"]
    assert "Your last response was not valid JSON" in second_call["messages"][0]["content"]
    assert "Your last response was not valid JSON" not in first_call["messages"][0]["content"]


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
