# backtest/ — point-in-time replay of the v1 technical filter

This package replays trade-scanner's **existing** technical filter over
historical bars and reports `hit_rate`, `adj_hit_rate`, `scratch_rate` and
`avg_rr` through `journal.py`'s own functions, so the numbers line up with the
live paper-trading journal's.

It imports `filter.py`, `indicators.py`, `config.py` and `journal.py` and edits
none of them. Nothing here writes `journal.json`.

```
PYTHONPATH=. python -m backtest.run --start 2026-01-02 --end 2026-08-21
```

---

## (a) What this measures: technical signal quality **in isolation**

The only question this engine can answer is *how good is the ordering produced
by `filter.passes_hard_filter` and `filter.score_survivor`, on its own?*

It is not a simulation of the live product. The live product is the filter
**plus** an analyst stage that reads news, and that stage is absent here (see
(b) and (c)). Treat the output as a measurement of one component, not a forecast
of system performance, and never as a forecast of money.

Two further consequences of the mechanical setup, both stated in the report
itself:

- Entry is the last daily close; the stop is 2.0 x the **daily** `atr14`; the
  target sits at exactly `config.MIN_RR`. Live, Claude picks these within an ATR
  band. Because `rr` is therefore constant across the whole run, `avg_rr` is a
  deterministic function of the hit and scratch rates, not an independent
  result.
- A 30m bar that touches both target and stop resolves `ambiguous` and counts as
  a loss, exactly as live. The bars cannot say which came first.

## (b) News sentiment is deliberately excluded

`analyst.py` sends each survivor's indicator snapshot **plus its recent news
headlines** to Claude. There is no free historical news archive covering
Jan–Aug 2026 at headline granularity, so the input that stage requires does not
exist for this window at any price this project is willing to pay.

Rather than fabricate it, the whole news dimension is out of scope for the
backtest and is validated separately, by the live paper-trading journal, which
does run the analyst and does see real headlines. **This is an intentional scope
decision, not an oversight.** The consequence is stated plainly rather than
worked around: no number this engine produces says anything about whether news
sentiment adds or destroys value.

## (c) The technical proxy gate, and how it differs from live

`filter.run_filter` caps a scan slot at `config.MAX_SURVIVORS` (30), and in the
2026 cache **that cap binds on every single slot** — the replay produces exactly
30 signals per slot, roughly 1,300 a month, against the live journal's ~560
alerts in three weeks. Hit rates over populations that different are not
comparable, which would defeat the entire purpose of routing the arithmetic
through `journal.py`.

Live:

```
filter.run_filter -> analyst.py (Claude conviction, WITH news)
                  -> conviction >= config.MIN_CONVICTION
                  -> sort by conviction -> [: config.MAX_ALERTS]
```

Backtest (`backtest.engine.technical_proxy_gate`):

```
filter.run_filter -> rank by filter.score_survivor
                  -> [: config.MAX_ALERTS]
```

Both constants are read from `config`, never hardcoded.

**What the gate buys and what it does not.** It approximates live alert
**volume** — roughly the same number of alerts per scan — which is the only
reason the hit rates are comparable at all. It does **not** replay Claude's
conviction judgement. Signal **selection** therefore differs from live even
where volume matches: the live system may alert on a name this gate drops, and
drop a name this gate alerts on, because it is reading something this engine
cannot see.

Two further differences worth being explicit about:

- **The `conviction` column is not conviction.** It is the survivor's rank by
  `score_survivor` *within its own scan slot*, mapped linearly onto 0–10 with
  the top name at 10. It is ordinal, uncalibrated, and not comparable across
  slots: the best name in a thin slot scores the same 10 as the best name in a
  strong one. Every signal's `reason` string opens with `RANK-PROXY CONVICTION`
  so the proxy is visible in any raw dump, not just the report.
- **`config.MIN_CONVICTION` is deliberately not applied.** Live it is a
  threshold on a calibrated judgement. Applied to an ordinal rank it would look
  like the live floor while meaning something else entirely, so the cap alone
  provides the volume match.

The caveat is printed at the top of every rendered report
(`report.PROXY_GATE_CAVEAT`), not only here, so a hit rate copied out of the
report carries it along.

## (d) Known methodological limits

### 1. Survivorship bias — direction: **optimistic**

`data.build_universe()` scrapes *today's* S&P 500 / Nasdaq-100 membership.
Replaying Jan–Aug 2026 against the Aug 2026 constituent list over-represents
names that survived the period and names that were added *because* they had
already performed. No free point-in-time constituent list exists. Every rate in
the report is therefore biased upward by an unmeasured amount, and the bias is
larger for longer windows.

### 2. Ticker reassignment

The universe contains renamed tickers whose history splices unrelated
instruments. Two proven cases: VMRK (crashed the live scanner, fixed
2026-08-24) and BNY (two instruments concatenated under one symbol, a +1272%
phantom bar). `backtest/screen.py` screens for these — single-bar returns above
500%, interior gaps beyond 30 calendar days, and tickers holding under 50% of
the median bar count — and writes `data/_screen_report_*.csv`. A splice that is
subtler than those thresholds would survive the screen undetected.

### 3. Earnings dates are as-known-**today**

`yfinance.Ticker.get_earnings_dates()` returns the schedule as it stands now,
not as it was known at simulated time. Earnings are scheduled weeks ahead, so
this is a small leak — but it is a leak, and it is documented rather than
silently accepted. (v1 does not gate on earnings; the cache exists for v2's
blackout rule, `backtest/earnings.py`.)

### 4. Dividend / split adjustment convention

The two frames disagreed about corporate actions and had to be reconciled.
Per `data/_manifest.json`: 30m bars come from **hfdatalibrary (IEX)** delivered
as traded and **not** back-adjusted for splits; daily bars come from
**yfinance** (`auto_adjust=False`) and are nevertheless restated for them. For
any ticker with an action inside the window the 30m series sat on one price
scale before the action and another after, while daily ran continuously.

Measured as `(30m daily-aggregated close) / (daily close)`, **42 of 441**
tickers drifted more than 1% between their first and last 20 days: 9 corporate
actions (BKNG 25:1, KLAC 10:1, CVNA, CRWD, DD, FDX, SPGI, HON, CME) and 33
single-step dividend adjustments. `backtest/reconcile.py` corrects this
empirically — prices divided by each segment's median ratio, volume multiplied
by it so `price * volume` notional and the `vol_ratio` 20-period mean stay
continuous — writing corrected copies to `data/ohlcv_30m_adj/` and leaving
`data/ohlcv_30m/` as the untouched record of what the vendor sent.
`BarStore` reads only the reconciled directory. Drift over 1% went **42 → 0 of
441**; worst remaining 0.87%, median 0.15%. Full audit trail in
`data/_reconcile_report.csv`.

**Volume caveat on the 30m frame.** IEX is ~2–3% of consolidated volume, so
absolute 30m volume is unusable and 30m OHLC deviates from the consolidated
tape. `vol_ratio` divides volume by its own 20-period mean, so numerator and
denominator largely cancel — degraded but not meaningless. `MIN_AVG_VOLUME` and
`MIN_PRICE` read the **daily** frame only (`filter.py`), which is consolidated
yfinance data, so the liquidity gate is correct. Stops are sized from the
**daily** `atr14` for the same reason.

### Two open items

- **`jump_tol` is calibrated on 8 months and degrades over the 20-month
  warm-up.** The reconciler's segmentation threshold (0.008) was chosen from
  the noise floor measured inside the replay window: within it, only **2 of
  441** tickers still show a residual jump above 1% (and by the report's own
  first-20-days-vs-last-20-days drift measure, 42 → 0). Across the full span of the 30m cache
  (which starts Jan 2025 to give indicators a warm-up), **205 of 441** exceed
  it. The correction is fitted to the window the backtest actually scores, and
  it is *not* trustworthy over the warm-up bars that precede it. Indicators
  computed early in 2026 lean on those bars.
- **MNST's unexplained transient 2x excursion.** MNST's frame ratio is exactly
  2.0 for three consecutive sessions (Jul 20–22, rescaled by the reconciler —
  correct, whatever caused it) *and* an isolated 2.0 on 2026-07-31 that is left
  alone, because a single-bad-print outlier is `screen.py`'s job and not the
  reconciler's. **No screen currently catches it**: it is not a 500% single-bar
  return, not a gap, and not a short frame. Its cause is undiagnosed. Treat any
  MNST signal from late July 2026 as suspect.

## (e) Universe coverage: 441 of 500 (88%)

`config.UNIVERSE_CAP` is 500 and the daily cache holds all 500. The 30m vendor
has no history for **59** of them (ACGL, APP, ARES, BALL, BG, BLDR, BNY, CASY,
COHR, COR, CPAY, CPT, CRH, DOC, ECHO, … — recent index additions, renames, and
one screened splice), so the replay universe is **441 tickers, 88% coverage**.

`PointInTimeView.frames_for` raises `FileNotFoundError` for those names rather
than returning a partial dict: `alignment` counts *agreeing timeframes*, so a
silently missing frame would not make a ticker look unavailable — it would make
it look like a ticker that merely failed the filter, and that bias would be
invisible in the output. `V1Technical` catches the error, skips the name and
**counts** it (`totals["missing_history"]`), so the gap is always visible in the
run summary.

---

## Design notes

- **`BarStore` is the only object that ever sees full history**, and it exposes
  no public method returning untruncated bars. `PointInTimeView` holds a
  *closure* that applies the point-in-time cut before returning anything — no
  attribute chain leads from a view back to a full frame. `pit.py` is the
  critical file; `tests/backtest/test_pit_leakage.py` is an adversarial suite
  with a mutation check (widen the cut by one day and the suite must go red).
- **Bar completeness, not just timestamp.** A daily bar stamped `2026-03-16
  00:00` describes the whole session; a naive `index <= as_of` cut would hand a
  noon scan that day's finished close. Bars become visible only once their
  interval has closed.
- **Truncate, then resample.** `4h` is derived from already-cut 30m bars.
  Resampling full history and cutting afterwards would let the bucket
  containing `as_of` absorb post-`as_of` minutes into its High/Low/Close.
- **Indicator lookback is bounded** (`v1_technical.INDICATOR_LOOKBACK`: 1,000
  bars on 30m/4h, 800 on daily) purely for cost — `compute_indicators` builds
  full series and reads one value. The bound is applied *after* the
  point-in-time cut, so it can only discard older rows. Validated on 30 tickers
  x 2 instants: every field within 9.0e-06 relative, zero differences in
  `classify_trend`, `alignment` or `passes_hard_filter`.
- **Never commit parquet.** `data/` is gitignored. The manifest
  (`data/_manifest.json`) records per ticker: source, interval, first/last bar,
  row count, adjustment convention and download timestamp.
