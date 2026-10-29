# 01 - Structure of trade-scanner

Snapshot date: 2026-10-28 (the day the schedule was disabled). Everything below is read from the repo, `journal.json`, the git history and the GitHub Actions history as of that day. Numbers are computed by the scripts in `docs/analysis/scripts/`.

## 1. What this repo is

An automated equity/futures scanner, run on GitHub Actions. Twice a day it takes about 500 symbols, filters them with plain indicator math down to at most 30, sends those 30 (with news headlines) to Claude in batches of 10, logs every returned setup to `journal.json`, sends a digest of the top setups to Telegram/email/Discord, and on the next scan marks each logged setup as a win, loss or scratch. It is a paper-trading experiment (`config.PAPER_MODE = True`); no orders are ever placed.

PLAN.md calls the "v1" strategy "technical alignment": a symbol is interesting when 30m, 4h and daily trends all point the same way. The repo also contains a historical replay package (`backtest/`) and a stub for a "v2 dip-buy" strategy (`backtest/strategies/v2_dipbuy.py`, `generate()` raises `NotImplementedError`). There is no "v2.1" anywhere in the repo (searched code, docs, plans, commit messages).

## 2. Directory map

### Root: the live scanner

| File | Lines | What it does |
|---|---|---|
| `main.py` | 295 | Entry point. Guards (weekday, holiday list, +/-90 min scan window, already-ran-today), resolves open alerts, runs the pipeline, builds the digest. `--daily-report` runs the once-a-day summary instead. `--dry-run`, `--limit N`, `--force-run` for manual use. |
| `config.py` | 108 | Every tunable: universe, filter thresholds, alert gates, Claude settings, journal settings, schedule, 2026 holiday list. |
| `data.py` | 191 | Universe scrape (S&P 500 from Wikipedia, Nasdaq-100 from slickcharts, plus 4 futures), batched yfinance download for 30m/1h/daily, `fetch_market_context` (SPY/QQQ/VIX line). |
| `indicators.py` | 159 | EMA 9/21/50/200, RSI14, MACD, Bollinger, ATR14, ADX14, volume ratio, 20-bar range, per timeframe; trend classification; alignment score; `bars_since_flip`. |
| `filter.py` | 104 | Hard rejects and survivor scoring; keeps top `MAX_SURVIVORS` (30). |
| `news.py` | 104 | Yahoo Finance RSS per survivor, VADER sentiment, 24h window (48h on Monday). |
| `analyst.py` | 396 | Batched Claude call (10 symbols per call) returning JSON setups. Horizon is computed in code from alignment, not by Claude. System prompt is sent as a cached block. |
| `journal.py` | 289 | Writes alert records, resolves outcomes against 30m bars, computes stats, asks Claude for a plain-English stats paragraph every 30 closed alerts. |
| `digest.py` | 413 | Builds the per-scan digest, the no-setup digest, and the daily report; splits for Telegram's 4096 limit. |
| `notify.py` | 133 | Telegram, Gmail SMTP, Discord webhook; each isolated so one failing does not block the others. |
| `display.py` | 55 | `rich` terminal animation, disabled when `CI` is set (the print still happens in CI, only the sleep is skipped). |
| `journal.json` | 1132 records | The live paper-trading log. Gitignored but force-added by the workflow every run. |
| `scripts/dedupe_journal.py` | | One-off cleanup written for the Aug 7-13 duplicate-run incident. |
| `tests/` | 13 files + `tests/backtest/` (12 files) | `python -m pytest -q` on 2026-10-28: **313 passed**, 22 warnings. |
| `.github/workflows/scan.yml` | | The only workflow. Cron removed on 2026-10-28; `workflow_dispatch` (with a `force_run` input) kept. Workflow also disabled with `gh workflow disable`. |

### Docs and project memory

| Path | What it is |
|---|---|
| `PLAN.md` | The original build plan (phases, ground rules, config, schema, costs). Partly out of date: it still describes a 3-way scan split in places and a SPY-bar holiday check that was replaced by a hard-coded holiday list. |
| `STATUS.md` (2026-08-02), `INVESTIGATION.md` (2026-07-29), `AUDIT.md` (2026-07-25) | Point-in-time reports. Untracked in git when this analysis started (they were never committed). `AUDIT.md` is stale by its own admission in `STATUS.md`. |
| `docs/superpowers/plans/` (9 files), `docs/superpowers/specs/` (1) | Per-phase implementation plans. |
| `SETUP.md` | Telegram/Discord/email/GitHub Secrets setup. |
| `.superpowers/sdd/` | Task ledger and review diffs from the subagent-driven build (gitignored). |
| `backtest_run.log` | Log of the 2026-08-25 backtest run. |
| `CLAUDE.md` | Does not exist in this repo. |

### `backtest/` (added 2026-08-24/25): point-in-time replay of the v1 filter

| File | Role |
|---|---|
| `download.py`, `earnings.py`, `screen.py`, `reconcile.py`, `store.py` | Bulk price cache (30m from hfdatalibrary/IEX, daily from yfinance), earnings-date cache, data-integrity screen, split/dividend reconciliation between the two sources, parquet reader. |
| `pit.py` | `PointInTimeView`: truncates history at a simulated "as of" time to prevent look-ahead. |
| `market_calendar.py` | Simulated scan slots (2 per trading day). |
| `strategy.py`, `strategies/v1_technical.py` | `Strategy` protocol; v1 adapter that calls the live `filter.py`/`indicators.py` unchanged. Entry = last daily close, stop = 2.0 x daily ATR, target = `MIN_RR` (1.5) x stop. |
| `strategies/v2_dipbuy.py` | Interface stub only. Intended rules (in its docstring): `close > ema200`, `ema50 > ema200`, `rsi14 < ~40`, close in the lower half of the 20d range, earnings blackout, positive revenue trend (the revenue module does not exist). |
| `engine.py`, `run.py`, `report.py` | Replay loop, parallel/resumable runner, report. Resolution uses the live `journal.resolve_alert` unchanged. |
| `reports/v1_technical_2026.{md,csv}` | The only report: 320 slots, 2026-01-02 to 2026-08-21, 2560 alerts. |
| `README.md` | Scope and limits (news excluded, survivorship bias, ticker reassignment, adjustment mismatch). |

### `data/` (gitignored, 255 MB; backed up to `data-final-backup/` on 2026-10-28)

| Path | Contents |
|---|---|
| `ohlcv_daily/` | 500 parquet files, daily OHLCV, 2024-01-02 to 2026-08-21 (662 rows for a full-history ticker). Columns Open, High, Low, Close, Volume. |
| `ohlcv_30m/` and `ohlcv_30m_adj/` | 441 files each, 30m bars 2025-01-02 09:30 to 2026-08-21 15:30 (5312 rows for a full-history ticker). Extra `source` column. `_adj` is the split/dividend-reconciled copy the backtest reads. |
| `ohlcv_1h/` | 101 files, 1h bars 2026-01-02 to 2026-08-21 (1120 rows). Partial. |
| `earnings.parquet` | 12,253 rows: `ticker`, `earnings_date`. |
| `_signal_cache/` | 320 JSON files, one per backtest slot (`v1_technical-YYYYMMDDTHHMM.json`). |
| `_manifest.json`, `_download*.log/csv`, `_screen_report_{daily,30m}.csv`, `_reconcile_report.csv`, `_earnings_failures.json` | Download audit trail. 500 tickers requested, 441 with 30m data, 59 download failures. |

## 3. Pipeline flow

```
GitHub Actions cron (UTC)               scan.yml
  -> main.py guards                     weekday? holiday list? within 90 min of 06:00 or 12:30 PT? scan label already logged today?
  -> resolve_previous_alerts            30m bars strictly after the previous nominal scan time -> journal.resolve_open_alerts
  -> data.build_universe                S&P 500 + Nasdaq-100 + NQ=F/ES=F/YM=F/RTY=F, deduped, cap 500
  -> data.fetch_all_timeframes          30m (60d), 1h resampled to 4h (180d), daily (400d); drop symbols with < 20 bars on any frame
  -> filter.run_filter                  indicators.analyze_symbol per symbol; hard rejects; score; top 30 survive
  -> news.attach_news                   RSS headlines + VADER, survivors only
  -> analyst.analyze_survivors          Claude, batches of 10, JSON array of setups
  -> journal.log_alerts                 EVERY setup Claude returns is appended (all conviction levels)
  -> alertable = conviction >= 6, top 8  digest + notify (Telegram / Gmail / Discord)
  -> journal.json committed back to main by trade-scanner-bot with [skip ci]
```

Stage details as implemented:

- **Universe.** `SP500_ENABLED`, `NASDAQ100_ENABLED`, 4 futures, `UNIVERSE_CAP=500`. Mid-caps are not in the universe.
- **Hard filter** (`filter.passes_hard_filter`): average 20-day volume >= 1,000,000; price >= $5; daily ATR% >= 0.5; alignment >= 2; non-NaN daily snapshot.
- **Alignment** (`indicators.analyze_symbol`): per timeframe, bullish = close > EMA21 and EMA9 > EMA21 and MACD hist > 0; bearish is the inverse; else neutral. Alignment = number of non-neutral timeframes if they all agree, else 0. So 3 = all three agree, 2 = two agree and one is neutral (a disagreeing frame zeroes the score).
- **Survivor score** (`filter.score_survivor`): `alignment*10 + min(vol_ratio,5)*5 + (1 - min(dist_from_20d_high, dist_from_20d_low))*10 + min(adx,50)/5 + max(0, 5 - bb_width*100)`. Alignment 3 beats alignment 2 by 10 points, so the 30-name cap fills almost entirely with alignment-3 names. This is why the journal holds 1119 `swing` (alignment 3) and only 13 `intraday` (alignment 2) records.
- **Horizon** (`config.HORIZON_BY_ALIGNMENT`): 3 -> `swing`, 2 -> `intraday`, set in code and passed to Claude.
- **Claude** (`analyst.py`): model `claude-sonnet-5`, thinking disabled, `MAX_TOKENS=4000`. Receives per-timeframe indicator snapshots, 20d range position, volume ratio, `bars_since_flip` (told to ignore it for scoring), and headlines. Must return entry, stop, target, rr, conviction 0-10 (explicitly a within-batch ranking). Prompt says stops should be 1.5x to 2.5x the horizon-matched ATR and that rr below 1.5 must get conviction 0. **None of that is enforced in code**: `_parse_response` only checks that the required keys exist. `rr` is accepted as given, stop/ATR distance is not checked, and bias/price geometry is not validated (2 of 1132 records have inverted geometry).
- **Alert gate** (`main.run_scan`): `conviction >= 6`, sorted, top 8. The gate does not look at `rr`; 22 records with conviction >= 6 have `rr < 1.5`.
- **Journal logging**: all survivors' setups are logged with `alerted: true`, including conviction 0 to 5 that were never sent to anyone. See section 4.
- **Outcome tracking** (`journal.resolve_alert`): for each 30m bar, long wins if High >= target, loses if Low <= stop; a bar touching both is `ambiguous` and counted as a loss. If nothing is hit, `bars_open += len(bars)`; when `bars_open > SCRATCH_AFTER_BARS (12)` the alert is closed as `scratch`. Realized R convention in `journal.py`: win = its own `rr`, loss/ambiguous = -1, scratch = 0.
- **Stats**: `compute_stats` gives hit rate (wins / (wins + losses), scratches excluded), adjusted hit rate (scratch = half a win), scratch rate and average R, overall and by conviction/scan/direction/horizon. A Claude-written summary is generated every 30 closed alerts.
- **Delivery**: Telegram (split on setup boundaries), Gmail SMTP to self, Discord webhook. Daily report email at about 13:15 PT.

## 4. Data files and schemas

### `journal.json` (the only live data file)

A JSON list of 1132 alert records, one object each:

| Field | Type | Notes |
|---|---|---|
| `id` | str | `YYYY-MM-DDTHH:MM-TICKER` in scan-local time |
| `timestamp` | ISO str | scan time, America/Vancouver offset |
| `scan` | `premarket` or `preclose` | whichever nominal slot is closest to run time |
| `ticker`, `bias` | str | `long` (722) or `short` (410) |
| `conviction` | int 0-8 | Claude's within-batch ranking; values 0, 2, 3, 4, 5, 6, 7, 8 occur (no 1, no 9 or 10) |
| `entry`, `stop`, `target`, `rr` | float | Claude's numbers |
| `horizon` | `swing` or `intraday` | from alignment, set in code |
| `alerted` | bool | **True for all 1132 rows**, including rows below `MIN_CONVICTION`; the field does not distinguish sent from unsent |
| `status` | `open` or `closed` | 61 open, 1071 closed |
| `position` | `upper`, `lower`, null | where the last bar closed relative to entry while open |
| `resolved_at`, `outcome` | ISO str, win/loss/scratch/ambiguous | `resolved_at` is null for scratches |
| `bars_open` | int | cumulative 30m bars inspected |

Counts: win 85, loss 323, ambiguous 3, scratch 660, open 61. Date range of records: 2026-07-31 20:13 PT to 2026-10-28 11:20 PT. 382 distinct tickers. Records exist on only **29 distinct dates** (38 date/scan batches), see section 6. Per batch: 30 records typical (the 30-survivor cap), occasionally 31 to 39 where two runs landed in one slot.

Other counts: 1119 swing / 13 intraday; premarket 301 / preclose 831; conviction distribution 0:79, 2:49, 3:153, 4:170, 5:219, 6:301, 7:148, 8:13.

The 99 records removed by `scripts/dedupe_journal.py` on 2026-08-14 are gone from the working file; they are visible only in older git revisions of `journal.json` (1231 distinct ids across the 43 revisions, 1132 today).

### Other data

See the `data/` table above. There is no trade log, scan-history or outcome file separate from `journal.json`; "scan history" exists only as the per-run Actions history (233 runs from 2026-07-27 to 2026-10-28) and as the backtest's `_signal_cache/` and `reports/v1_technical_2026.csv` (2560 simulated alerts, columns `id, timestamp, scan, ticker, bias, conviction, entry, stop, target, rr, horizon, alerted, status, position, resolved_at, outcome, bars_open, strategy, reason`).

## 5. Stack, schedule, dependencies

- Python 3.11 locally (PLAN.md: system default 3.14 breaks some libs) and in Actions (`ubuntu-latest`, `actions/setup-python@v5` 3.11).
- `requirements.txt`: yfinance, pandas, numpy, anthropic, requests, beautifulsoup4, lxml, feedparser, python-telegram-bot, pytz, rich, vaderSentiment, python-dotenv, ta, stockstats. Not listed but needed for the backtest: pyarrow (parquet), `HFDL_API_KEY` for the 30m source. `.env` (gitignored, never committed) holds the API keys.
- **Schedule before it was disabled:** `15 12,13 * * 1-5` and `45 18,19 * * 1-5` for the scans (targeting 06:00 and 12:30 PT, deliberately 45 minutes early to ride out Actions queue delay), `40 19,20 * * 1-5` for the daily report. Both UTC offsets are scheduled to cover DST; `main.within_scan_tolerance` (+/-90 min) discards the wrong one.
- Cost per PLAN.md section 15: about $0.12 per scan (3 batches), about $5 to $8 per month.

## 6. Known bugs, gaps and dead code

Verified in the code or the run history; none were fixed as part of this analysis.

1. **Most scheduled scans never ran.** The Actions history has 233 runs (208 scheduled successes, 23 scheduled failures, 2 manual). Only 58 of them lasted over 100 seconds (a real scan takes 3+ minutes; the guard exit takes about 30). Journal batches exist for only 38 date/scan slots. Logs of two sampled scan runs (both on 2026-09-15) and one sampled daily-report run show the cause: GitHub started the "06:00" cron around 09:58 PT and the "12:30" cron around 14:46 PT, both outside the +/-90 minute window, so `main.py` logged `Outside scheduled scan window` and exited. The cause was confirmed on those sampled runs only; the other short runs were not individually inspected. Run counts that lasted over 100 s, by month: Jul 2, Aug 42, Sep 4, Oct 10.
2. **Resolution window depends on which scans happen to run.** `resolve_previous_alerts` only feeds bars after the *nominal previous scan time*, not after the alert's entry. When scans are skipped, the sessions in between are never inspected. In the 1071 closed alerts, 554 had at least one full trading session between the signal day and the day the resolving run happened; 31 of 389 scratches (8.0%) in that group actually touched their stop or target in the uninspected sessions. See `02-performance.md`.
3. **Scratch after 12 bars is about one session.** `SCRATCH_AFTER_BARS = 12` with 30m bars (13 per regular session) while the plan describes swing holds of 2 to 4 days. 660 of 1071 closed alerts (61.6%) are scratches.
4. **Alerts with no bars never expire.** `resolve_open_alerts` skips an open alert if no bars come back for its ticker. Two records have been open since 2026-08-17 (EQR) and 2026-10-01 (PSKY); neither ticker is in the current index tables, and neither will ever close. (The other 59 open records are from the last two scan days and are legitimately open.)
5. **`alerted` is always true**, so the journal cannot tell sent alerts from logged-only setups. Overall stats mix both populations. The digest's own stats and the "14%" quoted figure are on different populations (see `02-performance.md`).
6. **Config and prompt are not enforced in code**: stop-in-ATR-band, `MIN_RR`, price geometry (section 3).
7. **`entry` is the scan-time price, not a close.** Matches to a daily close: 97% of scans run after 13:00 PT match that day's close; about 80% of midday scans (11:00 to 13:00 PT) match neither the prior nor the same-day close within 0.1% (price caught mid-bar); only 42% of pre-market scans match the previous close within 0.05%.
8. **Premarket scans often ran after the open.** Aug 7-14 "premarket" batches have timestamps 06:38 to 06:46 PT (09:38 to 09:46 ET). The 2026-07-31 batch ran at 20:13 PT.
9. **Dead or unused code**: `config.MODEL_CHEAP` is never referenced (grep); `news.get_market_headlines` is defined and never called (grep); `StockDataFrame` is imported in `indicators.py` line 7 and not used; `display.flash_ticker` prints in CI (per AUDIT.md, not re-verified). `AUDIT.md` lists the rest.
10. **2026-only holiday list** (`MARKET_HOLIDAYS`), "must be updated yearly"; it ends 2026-12-25.
11. **Duplicate-run incident (Aug 7-13):** the same slot was run more than once, creating duplicate records. Fixed 2026-08-14 by the `_scan_already_ran_today` guard, the `(ticker, scan, date)` dedupe in `log_alerts`, and `dedupe_journal.py`. The journal has 0 duplicate `(ticker, scan, date)` rows today, but batches larger than 30 on Aug 7, 10, 13, 14 show slots with two runs at different times.
12. **Failed runs:** 23 scheduled failures. Jul 27 to Jul 31 (8 scheduled, plus 1 manual dispatch on Jul 29): the `lxml` dependency and unpushed fixes for the Jul 27-29 ones (INVESTIGATION.md); the Jul 31 one was not inspected. Aug 18-21 (14): the VMRK short-history crash, fixed in commit "Fix scan crash on short-history symbols (VMRK)" on 2026-08-24. One on Oct 5 (cause not inspected).
13. **Local clone drift:** until this analysis the local clone was 15 commits behind origin (only bot `journal.json` commits). `PLAN.md`, `STATUS.md`, `INVESTIGATION.md` and `AUDIT.md` document two earlier episodes of "committed locally, never pushed".
