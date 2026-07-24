# Trade Scanner — Build Plan

An automated multi-scan equity/futures scanner. Filters a 300-500 symbol universe down
to a handful of high-conviction setups, has Claude analyze the survivors, and delivers
one consolidated digest per scan to Telegram and email. Every alert is logged and its
outcome tracked so hit rate can be measured before real capital is risked.

---

## 0. Ground rules (read before building)

1. **This is not a self-training system.** No model weights update. No strategy rewrites
   itself. The "self-improvement" loop is: log outcomes → Claude summarizes patterns in
   plain English → a human decides whether to edit `config.py`. Do not build anything
   that mutates trading parameters automatically.
2. **No output may claim certainty.** Conviction scores are relative rankings, not
   probabilities of profit. Prompts must forbid language like "guaranteed," "will
   definitely," "can't lose."
3. **Paper gate is mandatory.** 30 logged sessions reviewed against real stats before
   any real money. This is written into the code as a flag, not just a good intention.
4. **Secrets never enter the repo.** `.env` is gitignored from commit #1. GitHub Actions
   uses repository Secrets.

---

## 1. Environment

| Item | Value |
|---|---|
| Python | 3.11.9 — invoke as `py -3.11` on Windows (system default is 3.14 and breaks numba/pandas-ta) |
| OS (dev) | Windows 11, PowerShell |
| Runtime (prod) | GitHub Actions, `ubuntu-latest`, Python 3.11 |
| Repo | `github.com/Daijay/trade-scanner` (private) |

### Installed and available

`yfinance`, `pandas`, `numpy`, `scipy`, `scikit-learn`, `anthropic`, `requests`,
`beautifulsoup4`, `feedparser`, `httpx`, `python-telegram-bot`, `schedule`, `pytz`,
`colorama`, `rich`, `plotly`, `matplotlib`, `seaborn`, `backtrader`, `yahooquery`,
`finnhub-python`, `textblob`, `vaderSentiment`, `python-dotenv`, `tqdm`, `loguru`,
`aiohttp`, `ta`, `stockstats`, `finta`, `curl_cffi`, `quantstats`, `technical-analysis`

### Explicitly NOT available — do not import

- `pandas-ta` — requires Python ≥3.12. **Use `ta` and `stockstats` instead.**
- `pyfolio` — broken build (uses removed `configparser.SafeConfigParser`). Use
  `quantstats` if portfolio analytics are ever needed.
- `TA-Lib` — needs a C toolchain, not installed.

### Known compatibility risk

`pandas 3.0.x` is installed. Several older TA libraries were written against pandas 1.x/2.x.
**Phase 1 task:** smoke-test `ta` and `stockstats` against a single ticker before building
anything on top of them. If either breaks, `finta` is the fallback; if all three break,
hand-roll the ~8 indicators needed (they are all short pandas expressions).

---

## 2. Architecture

```
main.py
  │
  ├─ config.py ........ universe, thresholds, feature flags
  ├─ data.py .......... batch OHLCV fetch, multi-timeframe
  ├─ indicators.py .... RSI, MACD, EMA stack, BBands, ATR, volume, ADX
  ├─ filter.py ........ 300-500 → ~30 (pure math, zero API cost)
  ├─ news.py .......... RSS + sentiment for survivors only
  ├─ analyst.py ....... Claude call → structured JSON
  ├─ digest.py ........ builds the one consolidated message
  ├─ notify.py ........ Telegram + email delivery
  ├─ journal.py ....... alert logging, outcome resolution, stats
  └─ display.py ....... terminal scan animation (local only)
```

### Execution flow

```
1.  guard: is it a weekday? is the market open today? → else exit
2.  fetch OHLCV for full universe, batched          [~15s, free]
3.  compute indicators on all symbols               [~5s, free]
4.  HARD FILTER → ~30 survivors                     [free]
5.  fetch news + sentiment for the ~30              [~10s, free]
6.  Claude analyzes the ~30 in batches               [~20s, ~$0.01-0.03]
7.  rank by conviction, take top 8 above threshold
8.  resolve outcomes of previously-open alerts      [free]
9.  build digest (setups + journal summary)
10. send to Telegram + email
11. log all ~30 to journal.json (not just the 8)
```

**Step 4 is the cost control.** Claude never sees the full universe. Adding symbols to
the universe does not increase API spend; only the survivor count does. Keep survivors
capped at 30 hard.

---

## 3. Schedule

Two scans, weekdays only (cost-driven — see §15). Market hours in Vancouver (PT):
open 6:30 AM, close 1:00 PM.

| Scan | PT | Purpose |
|---|---|---|
| Pre-market | 6:00 AM | Overnight gaps, gap-and-go setups, sets the day's bias |
| Pre-close | 12:30 PM | Late momentum, positions to carry overnight |

**Dropped coverage:** the former 10:30 AM mid-session scan is gone. Intraday setups
that form and resolve entirely between 6:00 AM and 12:30 PM will not be caught unless
they are still valid at the 12:30 PM scan.

### DST problem — handle explicitly

GitHub Actions cron is **UTC only** and does not observe DST. PT is UTC-7 in summer
(PDT) and UTC-8 in winter (PST), so a fixed cron drifts by an hour in November.

**Solution:** schedule cron for *both* offsets, then have `main.py` verify the actual
PT time and exit early if it isn't within ±20 minutes of a scheduled slot.

```yaml
# .github/workflows/scan.yml
on:
  schedule:
    # 6:00 AM PT  → 13:00 UTC (PDT) / 14:00 UTC (PST)
    - cron: '0 13,14 * * 1-5'
    # 12:30 PM PT → 19:30 UTC (PDT) / 20:30 UTC (PST)
    - cron: '30 19,20 * * 1-5'
  workflow_dispatch:        # manual trigger for testing
```

`1-5` = Mon-Fri. Weekends never run.

### Market holiday guard

Cron will still fire on Thanksgiving and July 4th. Before scanning, check whether SPY's
most recent daily bar is today. If not, log "market closed" and exit without any API
calls. Cheap, no extra dependency, self-maintaining.

---

## 4. `config.py`

```python
# ── Universe ────────────────────────────────────────────────
SP500_ENABLED      = True
NASDAQ100_ENABLED  = True
FUTURES            = ["NQ=F", "ES=F", "YM=F", "RTY=F"]
EXTRA              = []            # manual adds
UNIVERSE_CAP       = 500           # hard ceiling after dedupe

# ── Filter thresholds (tune these, not the code) ────────────
MIN_AVG_VOLUME     = 1_000_000     # liquidity floor
MIN_PRICE          = 5.00          # no penny stocks
MAX_SURVIVORS      = 30            # hard cap into Claude
MIN_ATR_PCT        = 0.5           # skip dead, non-moving names

# ── Alerting ────────────────────────────────────────────────
MAX_ALERTS         = 8
MIN_CONVICTION     = 6             # out of 10
MIN_RR             = 1.5

# ── Claude ──────────────────────────────────────────────────
MODEL              = "claude-sonnet-5"   # verify current string in console
MODEL_CHEAP        = "claude-haiku-4-5-20251001"   # fallback if cost climbs
BATCH_SIZE         = 10            # symbols per API call
MAX_TOKENS         = 4000

# ── Journal ─────────────────────────────────────────────────
SCRATCH_AFTER_BARS = 12            # bars open with no hit → scratch
STATS_EVERY        = 30            # resolved alerts per review
PAPER_MODE         = True          # ← flip only after 30 reviewed sessions
MIN_PAPER_SESSIONS = 30

# ── Display ─────────────────────────────────────────────────
SCAN_ANIMATION     = True          # auto-disabled when CI env var present
TICKER_DELAY       = 0.2           # seconds per ticker
```

Everything tunable lives here. No magic numbers anywhere else in the codebase.

---

## 5. Indicators (`indicators.py`)

Computed for every symbol on **30M, 4H, and Daily**:

| Indicator | Use |
|---|---|
| EMA 9 / 21 / 50 / 200 | Trend direction + stack order |
| RSI(14) | Momentum, overbought/oversold |
| MACD (12,26,9) | Momentum shift, crossovers |
| Bollinger Bands (20,2) | Volatility expansion/contraction, squeeze |
| ATR(14) | Stop distance sizing, volatility floor |
| Volume vs 20-day avg | Participation confirmation |
| ADX(14) | Trend strength vs chop |
| 20-day high/low range | Breakout detection |

### Timeframe alignment score

The core signal, mirroring the reference format:

```
BULLISH on a timeframe  = price > EMA21 AND EMA9 > EMA21 AND MACD histogram > 0
BEARISH on a timeframe  = the inverse
NEUTRAL                 = anything else

alignment = count of timeframes agreeing (0-3)
```

3/3 = strongest. This directly drives hold horizon (§8).

---

## 6. Hard filter (`filter.py`)

Runs on every symbol, costs nothing, must cut 300-500 → ≤30.

**Reject immediately if:**
- avg volume < `MIN_AVG_VOLUME`
- price < `MIN_PRICE`
- ATR% < `MIN_ATR_PCT` (dead name, nothing to trade)
- alignment score ≤ 1 (no coherent direction across timeframes)
- missing or malformed data

**Then score survivors** on: alignment strength, volume surge vs average, distance from
20-day range edge, ADX, and Bollinger squeeze/expansion state. Sort descending, take
top `MAX_SURVIVORS`.

If more than 30 pass, the excess is dropped — but **still logged with a `filtered_out`
flag** so the journal can later answer "were we discarding the winners?"

---

## 7. News (`news.py`)

For survivors only.

- Sources: Yahoo Finance RSS per ticker, plus a general market feed. `feedparser`.
- Window: headlines from the last 24h (48h for the 6:00 AM scan, to cover the weekend
  on Mondays).
- Scoring: `vaderSentiment` on each headline → compound score. Aggregate to
  `net_sentiment` (-1 to +1) and `headline_count`.
- Cap at 5 headlines per ticker fed to Claude. Titles only, not full articles.
- Failures are non-fatal: no news → `net_sentiment: null`, scan continues.

---

## 8. Claude analysis (`analyst.py`)

### Input per symbol

Compact JSON: ticker, current price, indicator values per timeframe, alignment score,
ATR, 20-day range position, volume ratio, headline titles + sentiment.

### Required output schema

Claude must return **only** a JSON array, no prose, no markdown fences:

```json
[{
  "ticker": "NVDA",
  "bias": "long",
  "conviction": 8,
  "entry": 950.20,
  "stop": 948.10,
  "target": 954.40,
  "rr": 2.0,
  "horizon": "swing",
  "timeframes": {"30m": "bullish", "4h": "bullish", "daily": "bullish"},
  "news_read": "net bullish, 3 headlines",
  "reasoning": "EMA stack aligned across all timeframes, volume expanding, breaking 20-day range high"
}]
```

### Horizon rule (deterministic, not Claude's discretion)

Computed in code from alignment, then passed to Claude as context:

| Alignment | Horizon | Typical hold |
|---|---|---|
| 3/3 timeframes | `swing` | 2-4 days |
| 2/3 (Daily disagrees) | `intraday` | hours, close same session |
| ≤1/3 | filtered out before this stage | — |

### Prompt constraints

- Stops must be ATR-derived, not arbitrary round numbers.
- Any setup with R:R below `MIN_RR` must be returned with `conviction: 0`.
- Conviction is a **relative ranking within this batch**, not a probability of profit.
  State this in the prompt explicitly.
- Forbidden words in `reasoning`: guaranteed, certain, will definitely, can't lose,
  sure thing, easy money.
- If a symbol has no clean setup, return it with `conviction: 0` rather than inventing one.

### Robustness

Wrap every call in try/except. Strip ` ```json ` fences before parsing. On a parse
failure, retry once with a stricter reminder, then skip that batch and note it in the
digest. A malformed response must never crash the scan.

---

## 9. Journal (`journal.py`)

The most important file in the project. Everything else is plumbing.

### Alert record

```json
{
  "id": "2026-07-22T06:00-NVDA",
  "timestamp": "2026-07-22T06:00:00-07:00",
  "scan": "premarket",
  "ticker": "NVDA",
  "bias": "long",
  "conviction": 8,
  "entry": 950.20,
  "stop": 948.10,
  "target": 954.40,
  "rr": 2.0,
  "horizon": "swing",
  "alerted": true,
  "status": "open",
  "position": null,
  "resolved_at": null,
  "outcome": null,
  "bars_open": 0
}
```

### Outcome resolution — runs at the start of every scan

For each `status: "open"` alert, pull price action since the alert timestamp:

| Condition | Result |
|---|---|
| Hit target first | `outcome: "win"`, status closed |
| Hit stop first | `outcome: "loss"`, status closed |
| Neither, `bars_open` > `SCRATCH_AFTER_BARS` | `outcome: "scratch"`, status closed |
| Neither, still within window | stays open, `position` updated |

`position` for still-open trades, per your rule:
- price **above entry** → `"upper"`
- price **below entry** → `"lower"`

Note for implementation: for a short, "upper" is unfavorable. Store the raw
above/below-entry fact and interpret direction-aware in the stats layer.

If both target and stop fall inside the same bar's high/low range, the sequence is
unknowable from OHLC data. Record `outcome: "ambiguous"` and count it as a **loss** —
the conservative assumption. Do not silently pick the favorable one; that is how
backtests lie.

### Stats — computed every `STATS_EVERY` resolved alerts

```
hit_rate      = wins / (wins + losses)                    # scratches excluded
adj_hit_rate  = (wins + 0.5 * scratches) / total_resolved # scratches as half
scratch_rate  = scratches / total_resolved
avg_rr        = mean realized R multiple
```

Reported for the whole set and broken down by:
- conviction bucket (6, 7, 8, 9, 10)
- scan time (premarket / mid / preclose)
- direction (long / short)
- horizon (intraday / swing)

**A high scratch rate is a signal, not noise.** If 40% of setups go nowhere, the filter
is selecting choppy names or targets are too far out. That belongs in the review.

### The review

Claude reads the stats block and writes a short plain-English summary: what's working,
what isn't, what a human might consider changing in `config.py`. **It does not edit
anything.** You read it and decide.

---

## 10. Digest (`digest.py`)

One message per scan. Never one message per setup.

```
📊 PRE-MARKET SCAN — Wed Jul 22, 6:00 AM PT
SPY +0.3% | QQQ +0.5% | VIX 14.2
Scanned 412 → filtered 28 → alerts 6

━━━━━━━━━━ SETUPS ━━━━━━━━━━

🟢 NVDA · LONG · conviction 8/10
   30M ✅  4H ✅  Daily ✅        horizon: swing
   entry 950.20 │ stop 948.10 │ target 954.40 │ R:R 2.0
   📰 net bullish, 3 headlines
   EMA stack aligned, volume expanding, breaking 20-day high

🟡 TSLA · LONG · conviction 6/10
   30M ✅  4H ✅  Daily ❌        horizon: intraday
   entry 245.10 │ stop 244.20 │ target 246.50 │ R:R 1.6
   📰 mixed, 2 headlines
   Momentum building but daily trend still choppy

━━━━━━━━━━ JOURNAL ━━━━━━━━━━
Resolved yesterday: 2 win · 1 loss · 1 scratch
Session 7 of 30 (paper)
Hit rate 64% │ Adjusted 58% │ Scratch 21%
```

### No-setup message

```
📊 PRE-MARKET SCAN — Wed Jul 22, 6:00 AM PT
Scanned 412 → filtered 3 → alerts 0

No setups. Only 3 names cleared the filter and none reached
conviction 6. Broad market flat (SPY -0.1%), VIX 12.4, most
of the universe inside its 20-day range with contracting volume.

Session 7 of 30 (paper) · Hit rate 64%
```

Always sends. Silence is indistinguishable from a crashed scanner.

---

## 11. Delivery (`notify.py`)

**Telegram:** `python-telegram-bot`. Create via @BotFather, get token, message the bot
once, then read `chat_id` from `getUpdates`. Telegram caps messages at 4096 chars —
split on setup boundaries if exceeded, never mid-setup.

**Email:** Gmail SMTP with an app password (not the account password). Same content,
plain text.

Both wrapped in try/except independently. Telegram failing must not block email.

---

## 12. Terminal display (`display.py`)

Uses `rich`. During the scan, each ticker flashes for `TICKER_DELAY` seconds with its
live status:

```
  scanning ▸ NVDA    ✅ passed   align 3/3
  scanning ▸ AAPL    ✗ filtered  low ATR
  scanning ▸ TSLA    ✅ passed   align 2/3
```

Then a summary table of survivors before the Claude call.

**Auto-disable in CI.** Check `os.getenv("CI")` — GitHub Actions sets it. 500 × 0.2s is
100 seconds of dead time in a cloud runner and produces unreadable logs. Local only.

---

## 13. Build phases

### Phase 1 — Scan pipeline, no API
`config.py`, `data.py`, `indicators.py`, `filter.py`, `display.py`

Goal: run `py -3.11 main.py --dry-run`, watch tickers flash, get a survivor table.
**Checkpoint:** are the ~30 survivors actually interesting charts? Pull three up on
TradingView and look. If the filter surfaces junk, no amount of Claude fixes it.

### Phase 2 — News + Claude
`news.py`, `analyst.py`

Start with `MAX_SURVIVORS = 5` to keep test costs near zero. Verify JSON parses,
stops are ATR-derived, R:R math is right.
**Checkpoint:** would you actually take these trades?

### Phase 3 — Journal + digest
`journal.py`, `digest.py`

Run locally 2-3 days. Confirm outcomes resolve correctly — deliberately backdate a test
alert to verify win/loss/scratch classification.

### Phase 4 — Delivery + automation
`notify.py`, `.github/workflows/scan.yml`

Secrets into GitHub. Trigger manually via `workflow_dispatch` first. Only enable cron
once a manual run delivers correctly.

### Phase 5 — Paper period
30 sessions. Do not touch thresholds mid-period; changing the rules while measuring them
destroys the measurement. Review at 15 sessions for bugs only, at 30 for strategy.

### Phase 6 — Review gate
Read the real stats. Then decide about real money — based on your numbers, not on how
good the digest looks.

---

## 14. Security

`.gitignore` from the first commit:

```
.env
journal.json
__pycache__/
*.pyc
logs/
.venv/
```

`.env` locally:

```
ANTHROPIC_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
EMAIL_ADDRESS=...
EMAIL_APP_PASSWORD=...
```

GitHub Actions reads the same names from **Settings → Secrets and variables → Actions**.

**Never** paste a key into a chat, a commit, a screenshot, or an issue. If one is ever
exposed, revoke it in the console immediately and issue a new one — rotation is free,
recovery from a drained key is not.

Keep the console spend limit set. It is the last line of defense against a bug that
loops an API call.

---

## 15. Cost

Real numbers from a live `--limit 10` API call (one full batch at the real
`BATCH_SIZE=10`, extended thinking disabled): `input_tokens=9814`,
`output_tokens=2151`, `thinking_tokens=0`. JSON output was complete and valid
(10/10 setups parsed, no retries). This replaces the earlier thinking-inflated
estimate below.

Claude Sonnet 5 pricing: $2.00/$10.00 per MTok (introductory, through
2026-08-31), reverting to $3.00/$15.00 per MTok standard afterward.

| Item | Intro pricing (through 2026-08-31) | Standard pricing (after) |
|---|---|---|
| Per batch of 10 (measured) | ~$0.041 | ~$0.062 |
| Per scan (3 batches of 10, MAX_SURVIVORS=30) | ~$0.12 | ~$0.19 |
| Per month (2 scans/day, ~22 weekdays -> ~44 scans) | ~$5.43 | ~$8.15 |
| Weekly stats review call | negligible | negligible |

Well under the configured monthly cap. Costs scale with **survivor count**, not universe
size — raising `MAX_SURVIVORS` from 30 to 100 roughly triples spend; going from 400 to
600 symbols changes nothing.

Schedule dropped from 3 scans/day to 2 (see §3) specifically to cut this monthly
figure — the 10:30 AM scan was the same per-call cost as the other two for one
extra scan/day, ~50% of total monthly spend for coverage that mostly overlapped
with setups still valid at 12:30 PM.

---

## 16. What this system cannot do

Worth having written down, in the repo, where it can be re-read after a good week.

- **It does not recognize chart patterns.** No head-and-shoulders, flags, or wedges.
  It reads indicators and trend alignment. That is a different, narrower thing.
- **It has no order flow, level 2, or tick data.** Free data stack, daily/intraday OHLCV only.
- **It cannot tell you whether the strategy has an edge.** Claude will produce fluent,
  confident-sounding analysis for a setup with no edge exactly as readily as for one with
  an edge. Only the journal answers that question, and only after enough samples.
- **Conviction is not probability.** An 8/10 means "best-looking of this batch," not
  "80% likely to work."
- **Backtests and reels are marketing.** Public strategy results are trivially overfit
  and nobody posts losing months. Your own logged numbers are the only ones worth acting on.
- **Between 70% and 95% of retail traders lose money.** A scanner improves your process.
  It does not move you into the winning group by itself.
