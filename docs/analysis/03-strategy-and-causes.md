# 03 - Strategy as implemented, timeline, and why the results were poor

Sources: code at HEAD, `PLAN.md`, `STATUS.md`, `INVESTIGATION.md`, `AUDIT.md`, the plans under `docs/superpowers/`, 122 commits of git history, 233 Actions runs, `journal.json`, and replays in `docs/analysis/scripts/`. Wherever a claim rests on a replay rather than on the journal itself, the replay is named.

## 1. The strategy as actually implemented (not as planned)

**Selection (code, not Claude).** From about 500 names (S&P 500 + Nasdaq-100 + 4 futures), keep those with 20-day average volume >= 1M, price >= $5, daily ATR% >= 0.5 and **trend alignment >= 2**, where a timeframe is bullish when close > EMA21, EMA9 > EMA21 and MACD histogram > 0 (bearish is the mirror) and alignment counts the non-neutral timeframes only if all of them agree. Rank by `alignment*10 + volume surge + closeness to a 20-day range edge + ADX + Bollinger squeeze` and keep the top 30. In practice that is almost always alignment 3 (1119 of 1132 records), i.e. **buy names that are already trending up on 30m, 4h and daily, sell names trending down on all three.**

What that selects, measured from daily bars at the signal (n = 1116):
- Longs: median position in the 20-day range 0.98 (at the high), 2.2 ATR above EMA21, RSI 64, prior 20-day return +8.7%.
- Shorts: median range position 0.00 (at the low), 2.1 ATR below EMA21, RSI 37.6, prior 20-day return -6.4%.

So the implemented strategy is **momentum continuation entered at the edge of the 20-day range, already extended from its mean**. It is not an early-trend or pullback strategy (that is what the unbuilt v2 was meant to be).

**Entry.** Claude's `entry` is, in practice, the last price the data feed had at scan time: for scans after the 13:00 PT close it equals that day's close (97% match within 0.1%); for midday scans it is a partial-bar price (about 80% match neither the prior nor the same-day close); for pre-open scans only 42% match the previous close within 0.05%. There is no limit-order, next-bar or confirmation logic, and the signal is computed on that same partial bar. PLAN.md says entry anchors to the last daily close. Backtest `v1_technical` hard-codes entry = last daily close, which is a different thing from live.

**Stop.** Claude chooses it. The prompt asks for 1.5x to 2.5x the horizon-matched ATR14 (daily for swing). Nothing in code checks this. Realized: median 1.50 ATR, **50.0% below 1.5 ATR, 41.6% below 1.0 ATR, 45.7% inside the requested band**, 4.4% above. Median 3.74% of entry.

**Target.** Claude chooses it; the prompt says rr below 1.5 must be returned with conviction 0, and nothing enforces it. Mean `rr` 1.76; median target 5.85% from entry, 2.08 ATR. 151 records have rr < 1.5 (22 of them alerted).

**Hold horizon.** Planned: swing 2 to 4 days, intraday same session. Implemented: **a record is judged on 30m bars and closed as a scratch once more than 12 bars have been inspected, roughly one regular session (13 bars)**, regardless of horizon. In the sparse period it is judged mostly on the last half session before the resolving run (`02-performance.md`, section 4). No code reads the `horizon` field for scoring.

**What gets counted.** Every setup Claude returns for the 30 survivors is a "trade" in `journal.json` (also conviction 0 to 5), but only conviction >= 6, top 8 per scan, was ever sent.

## 2. Timeline

All dates from git history, `STATUS.md`/`INVESTIGATION.md`, and the Actions history. PT.

| Date | Event |
|---|---|
| 2026-07-22 | PLAN.md and Phase 1 (universe, indicators, filter, display) committed. |
| 2026-07-23 | `bars_since_flip` info field; Nasdaq-100 scrape fix; `news.py`, `analyst.py`; extended thinking disabled; scan schedule cut from 3 to 2 per day for cost. |
| 2026-07-24 to 25 | `journal.py`, `digest.py`, `notify.py`, GitHub Actions workflow added with "cron-defined (not yet enabled)". `AUDIT.md` (2026-07-25) lists drift between plan and code. |
| 2026-07-26 | Scan-window tolerance enforced; market-context line added. |
| 2026-07-27 to 31 | First scheduled runs; 8 scheduled failures plus a manual one. Cause for Jul 27-29: `lxml` missing and 3 fix commits unpushed (`INVESTIGATION.md`). Jul 29 Discord added. Jul 31: cron moved 45 minutes early because Actions was delaying the :00/:30 slots by 50 minutes to 2 hours; `force_run` added; digest crash on Claude's output shape fixed. |
| **2026-07-31 20:13** | **First journal record** (a manual dispatch, AMZN). This is the start of the validation period. |
| 2026-07-31 to 08-02 | Chart-flash feature built and fully reverted; daily-report email and cron added; TradingView prototype moved to its own repo. |
| 2026-08-03 | First weekday with unattended scans. |
| 2026-08-07 to 13 | **Duplicate-scan-run incident**: the same slot ran more than once; duplicate records. Hit rate for these dates is the worst of any period (10.8%). |
| **2026-08-14** | **Dedup fix**: `_scan_already_ran_today`, `(ticker, scan, date)` guard in `log_alerts`, `dedupe_journal.py` run (99 records removed), `STATS_BASELINE_DATE` set so the digest's "Overall" line starts at a clean baseline. |
| 2026-08-18 to 21 | 14 scheduled runs failed (VMRK short-history symbol crashed the pipeline). Almost no records these days. |
| 2026-08-24 | VMRK fix; prompt caching made to engage; backtest engine built (Tasks 1 to 12). |
| 2026-08-25 | Parallel/resumable backtest; Jan to Aug 2026 v1_technical report committed. **Last human commit, 23:16 PT.** |
| 2026-08-26 to 10-28 | Only `trade-scanner-bot` commits (15 `journal.json` updates). Most scheduled runs fire outside the tolerance window and exit; from Aug 31 on there are only 14 scan batches, all midday ('preclose'), on 14 dates. 1 failed run (Oct 5). |
| 2026-10-28 | Schedule removed, workflow disabled, data backed up. |

**Why human updates stopped.** The repo does not say. The last human work is the backtest harness and its README, which states that "v2 dip-buy" has no rules yet (stub only) and that news-sentiment value can only be checked by the live journal. There is no commit message, issue, STATUS or PLAN entry that records a decision to pause or retire. What can be said from the data: the 432 closed records dated before the Aug 14 baseline show a 15.3% hit rate, and after Aug 25 the repo received no further human changes while the bot kept running. The reason is not recoverable from the repo and I am not guessing at it.

## 3. Hypothesis tests

Method notes. "Placebo" = take the same entry, stop distance and target distance, flip the direction, replay on daily bars. "Replay N" = hold the recorded stop and target for N sessions after entry (first full session after a midday/after-close signal, the signal session itself for a pre-open one), first touch decides, same bar touching both is a loss, timeout valued 0, journal R convention. Confidence intervals bootstrap whole scan dates. Daily bars cannot see intrabar order, so this is an approximation of what 30m bars would show. Data: yfinance unadjusted daily; ATR from the `ta` library as the scanner uses.

### H1. Entry timing vs the signal bar

**Verdict: weakly supported at most; not the main driver.**
- The entry is the scan-time price on a partial bar, so the "signal" and the "entry" are the same instant, and a pre-open scan trades from a stale price. Premarket batches hit 15.9% vs preclose 22.5% (p = 0.17) and runs after the 13:00 close hit 29.6% (n = 71 decisive).
- For premarket records the gap from entry to the next open was against the position by more than 0.5 stop-distances in 23.6% of cases and in favour by more than 0.5 in 14.3%. But the median gap is -0.08 stop-distances.
- Not testable further: the journal does not store the signal bar or any fill.

### H2. Hold-horizon mismatch (planned 2 to 4 days, scored over about one session)

**Verdict: supported as a measurement problem; ruled out as the fix.**
- 61.6% of closed trades are scratches. Replaying the same stops and targets for 5 sessions turns the 660 scratches into 80 wins, 149 losses, 431 still-open timeouts.
- But holding longer does not help the recorded geometry. Journal-style R per trade and win/loss counts of replays: N = 1: 79 W / 237 L, -0.088 R; N = 3: 141 W / 362 L, -0.108 R; N = 5: 168 W / 428 L, -0.133 R; N = 10: 211 W / 570 L, -0.205 R; N = 20: 250 W / 653 L, -0.232 R. 95% CIs for all N exclude zero on the negative side (e.g. N = 5: -0.209 to -0.053, by scan date).
- The jump in hit rate (25.0% at N = 1 to 27.7% at N = 20) is small; the losses grow faster than the wins. A longer window reveals the outcome rather than improving it.

### H3. Targets and stops too wide for realized volatility

**Verdict: ruled out for stops; for targets, "barely moved" is real but is a window effect.**
- Realized volatility matched the ATR: mean true range over the 5 sessions after entry was 0.97 of the signal ATR (median 0.90). Volatility did not collapse.
- Stops are not too wide, they are too tight (H4).
- Targets sit at a median 2.08 ATR. In the first session after entry the price reached the target in only 8.0% of trades and moved on a median 0.33 of its stop distance (0.19 of its target distance). 41.8% of trades stayed within +/-0.5 R of entry in the first session. A 2 ATR target is simply not reached in one session by most names; it is reached in a few days: with the 5-session replay of the recorded geometry 168 of 1070 trades (15.7%) reach the target and 44.3% are still unresolved.

### H4. Stops not fit to ATR

**Verdict: supported.**
- 41.6% of swing stops are below 1.0 daily ATR, 50.0% below 1.5; only 45.7% are in the 1.5 to 2.5 band that the prompt asks for. Not enforced in code.
- Outcome by stop size (journal outcomes): under 1 ATR 49 W / 271 decisive = 18.1% (464 records); 1.5 to 2.5 ATR 28 / 91 = 30.8% (504 records). Fisher p = 0.017, subject to clustering.
- In the 5-session replay of recorded geometry, journal-style R by stop quartile is -0.274 (0.14 to 0.58 ATR), -0.103 (0.58 to 1.2), -0.077 (1.2 to 2.0), -0.100 (over 2.0). Stops under about 0.6 ATR are stopped out by ordinary noise and the placebo on the same quartile is +0.209.
- Why Claude set these stops is not in the data. One possibility is a miscomputed or wrong-timeframe ATR (for example a 30m ATR used for a swing); that was not tested.

### H5. Duplicate trades

**Verdict: real, affects the sample not the rate; the Aug 7 to 13 incident explains the worst week.**
- 99 duplicate rows were removed on Aug 14 (ids present in an earlier journal revision, absent now). No `(ticker, scan, date)` duplicates remain.
- 34.5% of records (390 of 1132) repeat a ticker and direction already logged in the previous 5 calendar days; 55 ticker-days appear in both scans of the same day (110 records). Repeats hit 17.7% (31 / 175 decisive), first sightings 22.9% (54 / 236). 382 distinct tickers, 491 ticker-direction pairs. The same idea is counted many times, so the true independent sample is smaller than the headline and CIs on rates are too narrow.
- Hit rate during the duplicate-run week alone was 10.8% (17 / 157), but it is not outlandish compared with the rest of August (these records are mostly valid, deduplicated trades from a bad week).
- Removing duplicates cannot create the 14%.

### H6. Market regime

**Verdict: partly supported, mostly untestable.**
- The window is SPY 747.0 to 772.3 (+3.4%), VIX 14.2 to 20.0 (mean 15.9). Sub-windows: Jul 31 to Aug 14 SPY +3.9%; Aug 14 to 31 -1.2%; September +0.1%; October +1.1%.
- In August, 5-session replay: longs -0.148 R vs placebo +0.102 R (n = 508), shorts -0.238 vs +0.216 (n = 203). Both directions lost and their mirror gained, which looks like short-term reversal of extended names in a quiet, range-bound tape, not like a one-sided trend failure. September and October have too few decisive trades (n = 10 to 36 decisive per cell) to say anything.
- The backtest (Jan to Aug, same filter, mechanical stops) shows the monthly hit rate swinging from 31.8% (March) and 40.4% (January) up to 82.9% (February), so regime sensitivity is plausible, but that is a different population (see section 5).
- One regime in the live journal means this hypothesis cannot be properly tested here.

### H7 (added). Is there any directional edge at all?

**Verdict: no evidence of a positive edge; no proof of a negative one at the return level.**
- Signal-direction forward return from entry to the Nth close, mean % per trade (95% CI by scan date): 1 session +0.15% (-0.33 to +0.64); 3 sessions -0.07% (-0.69 to +0.56); 5 sessions -0.54% (-1.30 to +0.24); 10 sessions -0.72% (-1.82 to +0.32). Longs at 5 sessions -0.97% (-2.08 to +0.04); shorts +0.20% (-0.68 to +1.26). All CIs include zero.
- Signed forward return in units of the trade's own stop distance: 1 session +0.057, 3 sessions +0.017, 5 sessions -0.144, 10 sessions -0.018, all with CIs including zero.
- With ATR-fit geometry (stop = k x daily ATR with k from 1 to 3, target 1.5x the stop, exit at the 3rd, 5th or 10th close), same entries and directions, mean return per trade was between -0.13% and -0.70% (every CI includes zero); the flipped-direction placebo was between +0.12% and +0.84%. This is a point-estimate gap in favour of the reverse trade, but its CI was not computed for the placebo, and one regime does not justify trading it.
- Compared with the zero-edge benchmark (36.3%), 20.7% overall is low, but short windows and stops closer than targets lower the benchmark somewhat; the placebo hit rate at one session (34.3%) is close to the benchmark, the actual (25.0%) is below it.

## 4. Did the v1 data support the v2.1 changes?

The repo contains no "v2.1" and no written spec for one (searched code, plans, specs, backtest docs, commit messages). The four changes in the request are tested below as stated, using v1 data. Where the repo has anything related, it is `backtest/strategies/v2_dipbuy.py`, which describes 200-day-EMA trend, RSI < 40 dip, earnings blackout and a revenue trend (not built) and stops at `daily ATR x multiplier`.

| Change | What v1 data says | Backed by data? |
|---|---|---|
| **Wider stops** | Stops of 1.5 to 2.5 ATR hit 30.8% vs 18.1% for stops under 1 ATR (p = 0.017). Under a 5-session hold, the tightest quartile loses -0.274 R vs -0.077 to -0.100 R for wider ones. But ATR-fit stops (1 to 3 ATR) with 1.5 rr still average -0.13% to -0.70% per trade (CIs include zero), and the result barely depends on k. Backtest with 2.0 ATR stops: 60.7% hit but 88.8% scratch and avg R +0.058 (see section 5). | **Partly.** Supported as a fix for noise stop-outs. Not supported as a source of positive expectancy. |
| **Time stop** | A time stop is what the journal's 12-bar scratch already is, in accidental form. Mean signed return in stop-distance units at 1, 3, 5, 10 sessions is +0.057, +0.017, -0.144, -0.018, none different from zero. Under the recorded geometry shorter holds lose less (-0.088 R at 1 session to -0.232 at 20). | **Neutral.** It standardizes scoring; the data does not show that any particular horizon is profitable. |
| **200-day MA filter** | 78.4% of trades are already with the 200-day trend (84.5% of longs above, 67.8% of shorts below). Journal outcomes: with-trend 53 W / 313 decisive = **16.9%**; against-trend 29 W / 93 = **31.2%** (p = 0.005, clustered). 5-session replay: with-trend hit 27.1%, jR -0.142; against-trend 29.8%, jR -0.125. | **Not supported.** If anything the opposite sign, from one regime; do not read it as "trade against the trend" either. |
| **Mid-caps** | Universe has no mid-caps. Closest proxy, ATR% terciles (median 2.1 / 2.9 / 4.0%): hit 23.5% / 15.1% / 22.8%. No pattern. | **Untestable** with v1 data. |

## 5. The backtest is not a rescue, and is not comparable

`backtest/reports/v1_technical_2026.md` (Jan 2 to Aug 21, 320 slots, 2560 alerts) reports 60.7% hit rate, **88.8% scratch rate**, 51.2% adjusted, +0.058 average R. Key differences from live: no news and no Claude (the top 8 by `score_survivor` replace the analyst gate); entry = last daily close; stop 2.0 daily ATR; target 1.5 x stop; survivorship bias (today's index membership); same 12-bar scratch rule, same 30m resolution. The scratch rate shows the same mechanical collapse: with 2 ATR stops and a 3 ATR target, almost nothing resolves inside one session. The 285 decisive trades of 2536 are dominated by the rare large movers. Months: Jan 40.4%, Feb 82.9%, Mar 31.8%, Apr 76.1%, May 73.3%, Jun 64.9%, Jul 44.8%, Aug 44.4% hit rate, with adjusted hit rates all between 48.6% and 54.4%. The backtest therefore supports "the scoring rule turns everything into a coin flip" more than it supports any edge.

## 6. Honest verdict: signal problem or mechanical problem?

**Both are present; only one is proven, and the proven one is the mechanical problem.**

Mechanical problems, each shown in the data:
1. Stops and targets are not fit to a horizon-matched ATR and nothing in code enforces the prompt (41.6% of stops under 1 ATR; hit 18.1% vs 30.8% for properly sized stops).
2. The scoring window is about one session for trades labelled as 2 to 4 day swings; 61.6% of trades end in a scratch that a longer look would have resolved as 12% wins, 23% losses, 65% unresolved.
3. Most scheduled scans never ran because of GitHub cron delay; the sparse period has 98.9% of trades with un-inspected sessions and a 77.5% scratch rate; at least 8% of scratches in that group are mislabeled.
4. The journal mixes sent alerts and unsent setups (`alerted` always true), so the headline rate is a blend; the quoted 14% is the conviction-6 bucket only.

Signal problem: **unproven in either direction.** The implemented signal buys extended strength and sells extended weakness at the edge of the 20-day range. Over 28 scan days in one calm regime it shows no positive forward return at any horizon (all CIs include zero) and loses to its own mirror image on every geometry tried. That is consistent with "no edge" and with "reversal edge in this tape"; it cannot support "has edge".

**What the data supports saying:** fixing the mechanics would make the measurement honest and would remove noise stop-outs, but on this evidence it would not turn the strategy profitable. Expect roughly zero expectancy after fixing, not a positive number.

## 7. The next test

Do not resume live paper trading on the current mechanics. Run one pre-registered offline test on data already on disk (`data-final-backup/ohlcv_daily`, `ohlcv_30m_adj`, and the 9599 pre-gate signals in `data/_signal_cache/`):

1. Take all signals the live filter would have produced for the 320 slots (Jan 2 to Aug 21, all 30 survivors per slot, not only the top 8).
2. Fix the geometry: entry at the next bar open after the scan, stop = 2.0 x daily ATR, target = 3.0 x daily ATR (rr 1.5), one fixed hold of 5 sessions, no scratch rule, score from daily and 30m bars from entry to exit.
3. For every signal also run the flipped-direction placebo, and a random-direction control matched on ticker and date.
4. Report mean return per trade and hit rate for signal vs placebo vs random, by month and by regime (SPY above/below 200-day), with date-clustered confidence intervals and the number of independent scan dates.
5. Decision rule written before running: the filter earns further work only if the signal beats both controls with a CI excluding zero in at least two distinct regimes. If it does not, retire the alignment filter and spend the effort on the pullback (dip-buy) idea, which is the structural opposite and the one thing the live data weakly hints at (extension coincides with worse outcomes).

That test uses 8 months of cross-regime data (not 28 scan days), uses mechanics that can be defended, and answers the only question this analysis could not: whether the signal itself has any directional information.
