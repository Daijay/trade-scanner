# analyst.py
"""Batched Claude call turning survivors + news into structured trade setups.
PLAN.md §8. Horizon is computed in code from alignment, never asked of Claude.
bars_since_flip/min_bars_since_flip are forwarded as informational context
only -- never scored or filtered on here.
"""

import datetime
import json
import logging

from dotenv import load_dotenv

import config

load_dotenv()

import anthropic  # noqa: E402  (import after load_dotenv so ANTHROPIC_API_KEY is set)

logger = logging.getLogger(__name__)

_FENCE_PREFIXES = ("```json", "```")

_FORBIDDEN_WORDS = [
    "guaranteed",
    "certain",
    "will definitely",
    "can't lose",
    "sure thing",
    "easy money",
]

_REQUIRED_KEYS = {
    "ticker", "bias", "conviction", "entry", "stop", "target", "rr",
    "horizon", "timeframes", "news_read", "reasoning",
}


def compute_horizon(alignment: int) -> str | None:
    """Alignment 3 -> swing, 2 -> intraday. Anything else (should already be
    filtered out by filter.py) returns None rather than raising."""
    return config.HORIZON_BY_ALIGNMENT.get(alignment)


def _range_position(snap: dict) -> float | None:
    """0-1 position of close within the 20d range (0 = at low, 1 = at high)."""
    high = snap.get("range_high_20d")
    low = snap.get("range_low_20d")
    close = snap.get("close")
    if high is None or low is None or close is None:
        return None
    span = high - low
    if span <= 0:
        return None
    return (close - low) / span


def _build_payload(survivor: dict) -> dict:
    """Compact per-symbol payload sent to Claude. Keeps everything the
    prompt constraints depend on (ATR for stop sizing, indicator stack per
    timeframe, alignment, bars_since_flip as informational context, news)."""
    analysis = survivor["analysis"]
    daily = analysis["snapshots"]["daily"]

    snapshots = {}
    for tf, snap in analysis["snapshots"].items():
        snapshots[tf] = {
            "ema9": snap["ema9"],
            "ema21": snap["ema21"],
            "ema50": snap["ema50"],
            "ema200": snap["ema200"],
            "rsi14": snap["rsi14"],
            "macd_hist": snap["macd_hist"],
            "bb_upper": snap["bb_upper"],
            "bb_lower": snap["bb_lower"],
            "atr14": snap["atr14"],
            "atr_pct": snap["atr_pct"],
            "adx14": snap["adx14"],
            "vol_ratio": snap["vol_ratio"],
            "trend": analysis["trends"].get(tf),
        }

    alignment = analysis["alignment"]
    news = survivor.get("news", {"net_sentiment": None, "headline_count": 0, "headlines": []})

    return {
        "ticker": survivor["symbol"],
        "price": daily["close"],
        "alignment": alignment,
        "horizon": compute_horizon(alignment),
        "timeframes": snapshots,
        "range_position_20d": _range_position(daily),
        "vol_ratio": daily["vol_ratio"],
        "bars_since_flip": survivor.get("bars_since_flip"),
        "min_bars_since_flip": survivor.get("min_bars_since_flip"),
        "news": {
            "net_sentiment": news.get("net_sentiment"),
            "headlines": news.get("headlines", []),
        },
    }


def _build_system_prompt() -> str:
    """Static instructions, identical across every batch call (and across
    scans). Split out from the per-call payload so it can be sent as a
    cached system block -- see _call_claude.

    Length is load-bearing: Anthropic's prompt cache has a ~1024-token
    minimum cacheable prefix and SILENTLY declines to cache anything
    shorter. Do not trim this block for brevity -- if it drops back under
    that floor the cache stops engaging with no error, and the only
    symptom is cache_read_input_tokens sitting at 0 in the scan logs."""
    forbidden = ", ".join(_FORBIDDEN_WORDS)
    return f"""You are a trading analyst. You will be given a JSON array of compact
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

INPUT PAYLOAD FIELDS
Each object in the input array carries:
- "ticker": the symbol.
- "price": last daily close. Anchor entry, stop and target to this.
- "alignment": how many of the three timeframes agree on direction (2 or 3).
- "horizon": already computed from alignment -- copy it through verbatim.
- "timeframes": per-timeframe indicator snapshot, each holding ema9, ema21,
  ema50, ema200, rsi14, macd_hist, bb_upper, bb_lower, atr14, atr_pct,
  adx14, vol_ratio, and a precomputed "trend" label.
- "range_position_20d": where the close sits in the 20-day range, 0.0 at the
  low and 1.0 at the high. Near 1.0 is a breakout-or-resistance context;
  near 0.0 is a support-or-reversal context.
- "vol_ratio": current volume over its 20-period average. Meaningfully
  above 1.0 is real participation; around 1.0 is unremarkable.
- "bars_since_flip" / "min_bars_since_flip": bars since each timeframe last
  changed direction. Informational context only -- never score or filter on
  them, and never cite them as the reason for a conviction.
- "news": net_sentiment (-1.0 to 1.0, or null when there is no coverage)
  and the recent headlines behind it.

OUTPUT FIELD RULES
- "entry", "stop" and "target" are absolute prices in the same units as
  "price" -- not offsets, not percentages.
- "bias" must agree with the geometry of your own numbers: for a long,
  stop < entry < target; for a short, target < entry < stop.
- "rr" must be consistent with the prices you return. Compute it from them
  rather than asserting a figure they contradict.
- "news_read" is one short clause on what the coverage implies for the
  setup. When net_sentiment is null or there are no headlines, say there is
  no material coverage -- never invent a narrative to fill the field.
- "reasoning" is one to three sentences citing the specific payload values
  that drove the call.

Rules:
- Stops MUST be derived from ATR (atr14 in the payload), not arbitrary round numbers.
  Use the atr14 belonging to the timeframe that matches the horizon: daily
  for a swing, 30m for an intraday. Roughly 1.5x to 2.5x that atr14 away
  from entry is the workable band -- tighter than that and ordinary noise
  takes the trade out, wider and the reward:risk stops clearing the floor.
  Worked example: entry 100.00 against a daily atr14 of 2.00 puts a long
  stop near 96.00, which is 2x the ATR below entry.
- Each symbol's "horizon" is already computed for you in the input payload
  (from its alignment score) -- use that exact value, do not decide it yourself.
- If a setup's reward:risk (rr = (target-entry)/(entry-stop) in absolute
  terms) is below {config.MIN_RR}, you must still return the object but with
  conviction: 0.
- Conviction is a RELATIVE RANKING WITHIN THIS BATCH ONLY -- it is not a
  probability of profit, not a guarantee, not a forecast. Rank the batch
  against itself: the cleanest one or two setups take the top scores, the
  merely acceptable sit mid-scale, and anything you would not take yourself
  is a 0. Do not return a batch of uniformly high scores -- if every symbol
  looks like an 8, you have not ranked them, you have flattered them.
- What separates a high conviction from a low one, in descending weight:
  all three timeframes agreeing rather than two; adx14 confirming the trend
  actually has strength rather than drifting sideways; vol_ratio showing
  real participation behind the move; a range_position_20d that leaves the
  target room to run rather than parking entry directly beneath resistance;
  and news that does not cut against the technical read.
- If a symbol has no clean setup, return it with conviction: 0 rather than
  inventing one.

WORKED EXAMPLES OF "reasoning"
Good, high conviction: "All three timeframes bullish, daily adx14 at 31 and
vol_ratio 1.9 confirm participation behind the move, and
range_position_20d of 0.55 leaves room to the 20-day high before the
target." Specific values, an explanation of why each matters, no claim
about what the market will do next.
Good, low conviction: "Two-timeframe alignment only, and daily adx14 of 14
says this is drifting rather than trending; entry sits at
range_position_20d 0.94 with resistance immediately overhead, so rr comes
in under the floor." States plainly why the setup is weak instead of
talking itself into it.
Bad: "Strong setup, great chart, this one is a sure thing." Cites no
values and promises an outcome.
Bad: "RSI is 58." A number with no interpretation and no link to the call.

- In "reasoning", never use any of these words or phrases: {forbidden}.
  They are banned because each asserts a certainty no technical setup
  possesses -- every trade this system produces can lose. Describe what the
  indicators show and what would invalidate the setup, not what the market
  will do. "The setup is void below the stop" is the register to write in.
- bars_since_flip / min_bars_since_flip fields are informational context only."""


def _build_user_content(payloads: list[dict], strict: bool = False) -> str:
    """Per-call content: just the batch's payload data (and, on retry, the
    strict-mode nudge). Everything else lives in the cached system prompt."""
    content = f"""Input symbols:
{json.dumps(payloads)}
"""
    if strict:
        content += (
            "\n\nYour last response was not valid JSON / not a JSON array. "
            "Return ONLY a JSON array, no other text."
        )
    return content


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


_TF_KEYS = ("30m", "4h", "daily")
_TREND_SYNONYMS = {"bullish": "up", "bearish": "down", "neutral": "flat"}


def _normalize_timeframes(item: dict) -> None:
    """Real Claude output has been observed collapsing a per-timeframe
    snapshot to a bare trend string (e.g. "up") instead of the requested
    {"trend": "up"} object -- digest.py's _timeframe_checks assumes a dict
    and crashes (AttributeError: 'str' object has no attribute 'get') on
    the bare-string shape. Normalize both shapes here, at the point where
    analyst.py's output is constructed, rather than pushing the defensive
    handling onto every consumer. Also maps the classify_trend() vocabulary
    (bullish/bearish/neutral) onto digest.py's up/down/flat in case Claude
    echoes the input payload's trend wording instead of the requested one."""
    timeframes = item.get("timeframes")
    if not isinstance(timeframes, dict):
        item["timeframes"] = {}
        return
    normalized = {}
    for tf in _TF_KEYS:
        snap = timeframes.get(tf)
        if isinstance(snap, dict):
            trend = snap.get("trend")
        elif isinstance(snap, str):
            trend = snap
        else:
            trend = None
        if isinstance(trend, str):
            trend = _TREND_SYNONYMS.get(trend.lower(), trend.lower())
        normalized[tf] = {"trend": trend}
    item["timeframes"] = normalized


def _parse_response(text: str) -> list[dict] | None:
    """Returns a validated list of dicts, or None on any failure."""
    try:
        data = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict) or not _REQUIRED_KEYS.issubset(item.keys()):
            return None
        _normalize_timeframes(item)
    return data


def _extract_text(response) -> str | None:
    """Concatenate text blocks from the response, skipping non-text blocks
    (e.g. ThinkingBlock, which precedes the text block when extended
    thinking is enabled)."""
    parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    return "".join(parts) if parts else None


def _log_cache_usage(response) -> None:
    """Record prompt-cache effectiveness so production logs can answer
    whether the cached system block is actually being served from cache.

    cache_read of 0 on every call means the cache never engaged. The usual
    cause is the system prefix falling back under Anthropic's ~1024-token
    minimum, which fails silently -- no error, no warning, just full price
    on every batch. Never raises: this is observability, and it must not be
    able to take down a scan."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    logger.info(
        "Claude usage: cache_read=%s cache_write=%s uncached_input=%s output=%s",
        getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
    )


def _call_claude(client, payloads: list[dict], strict: bool = False) -> str | None:
    user_content = _build_user_content(payloads, strict=strict)
    try:
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            thinking={"type": "disabled"},
            system=[
                {
                    "type": "text",
                    "text": _build_system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
        _log_cache_usage(response)
        return _extract_text(response)
    except Exception as e:
        logger.warning("Claude API call failed: %r", e)
        return None


def _analyze_batch(client, batch: list[dict]) -> list[dict]:
    payloads = [_build_payload(s) for s in batch]
    symbols = [s["symbol"] for s in batch]

    text = _call_claude(client, payloads, strict=False)
    parsed = _parse_response(text) if text is not None else None

    attempts = 0
    while parsed is None and attempts < config.ANALYST_MAX_RETRIES:
        attempts += 1
        logger.info("Batch response invalid, retrying (%d/%d): %s", attempts, config.ANALYST_MAX_RETRIES, symbols)
        text = _call_claude(client, payloads, strict=True)
        parsed = _parse_response(text) if text is not None else None

    if parsed is None:
        logger.warning("batch skipped: %s", ", ".join(symbols))
        return []

    known = set(symbols)
    seen: set[str] = set()
    filtered = []
    for item in parsed:
        ticker = item["ticker"]
        if ticker not in known:
            logger.info("Dropping setup for ticker not in this batch's survivors: %s", ticker)
            continue
        if ticker in seen:
            logger.info("Dropping duplicate setup for ticker: %s", ticker)
            continue
        seen.add(ticker)
        filtered.append(item)

    missing = known - seen
    if missing:
        logger.info("Symbols skipped for this batch (no matching ticker in response): %s", ", ".join(sorted(missing)))

    return filtered


def analyze_survivors(survivors: list[dict], now: datetime.datetime) -> list[dict]:
    """Batched Claude call: survivors (already carrying a "news" key from
    news.attach_news) -> list of parsed setup dicts. Skipped batches simply
    contribute nothing; the rest of the scan continues."""
    if not survivors:
        return []

    client = anthropic.Anthropic()

    results: list[dict] = []
    for i in range(0, len(survivors), config.BATCH_SIZE):
        batch = survivors[i : i + config.BATCH_SIZE]
        results.extend(_analyze_batch(client, batch))

    return results
