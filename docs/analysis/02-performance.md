# 02 - Performance of the live paper journal

Source: `journal.json` as of 2026-10-28 (1132 records, 2026-07-31 to 2026-10-28), plus daily bars from yfinance and the git/Actions history. All figures are computed by `docs/analysis/scripts/`. Definitions follow `journal.py`: scratch = 0.5 in the adjusted hit rate, ambiguous = loss, R = `rr` on a win, -1 on a loss or ambiguous, 0 on a scratch.

## 0. Read this first: which population, which number

`journal.json` contains **every setup Claude returned**, not only the ones that reached an inbox. `alerted` is `true` on all 1132 rows, but only conviction >= 6 (top 8 per scan) were ever sent. The figures differ a lot by population, so the "14% hit rate" in circulation needs pinning down:

| Population | Closed | Wins | Losses (+amb.) | Hit rate (excl. scratch) | Adjusted hit rate |
|---|---|---|---|---|---|
| **All records** | 1071 | 85 | 326 | **20.7%** (Wilson 95% CI 17.0% to 24.9%) | **38.7%** |
| Actually sent (conviction >= 6) | 436 | 28 | 139 | 16.8% (11.9% to 23.2%) | 37.3% |
| Conviction exactly 6 | 284 | 15 | 92 | **14.0%** (8.7% to 21.8%) | 36.4% |
| Before the 2026-08-14 baseline date | 432 | 32 | 177 | 15.3% | 33.2% |
| From 2026-08-14 (the digest's "Overall" scope) | 639 | 53 | 149 | 26.2% | 42.5% |
| Aug 7-13 duplicate-run period | 312 | 17 | 140 | 10.8% | 30.3% |

The 14% figure matches the conviction-6 bucket (the largest alerted bucket, 301 records) almost exactly. It is not the overall rate. The overall rate is 20.7% and is itself well below the 36.3% a zero-edge trade would be expected to win at this average payoff (see section 6).

## 1. Totals

| | Count | Share of closed |
|---|---|---|
| Records | 1132 | |
| Closed | 1071 | |
| Open | 61 | |
| Wins | 85 | 7.9% |
| Losses | 323 | 30.2% |
| Ambiguous (counted as loss) | 3 | 0.3% |
| Scratches | 660 | 61.6% |

- Hit rate excluding scratches: 85 / (85 + 323 + 3) = **20.7%**.
- Adjusted hit rate with scratch = 0.5: (85 + 330) / 1071 = **38.7%**.
- Average realized R per closed trade: **-0.177**. Sum: **-189.1 R**. Average winning `rr`: 1.61, so breakeven hit rate (no scratches) would be 38.3%.
- Open: 61 = 59 from the last two scan days (Oct 26 and Oct 28) plus 2 that can never close (EQR from Aug 17, PSKY from Oct 1; no bars are ever returned for them, see `01-structure.md`).

## 2. Breakdowns

Hit = wins / (wins + losses + ambiguous). Adj = scratch counted 0.5. Cells with fewer than about 30 closed trades are noise.

### Timeframe alignment (horizon label)

Alignment 3 = `swing`, alignment 2 = `intraday` (the code maps one to the other).

| Horizon | Records | Closed | W | L | Scr | Hit | Adj | Avg R |
|---|---|---|---|---|---|---|---|---|
| swing (3/3) | 1119 | 1059 | 83 | 320 | 656 | 20.6% | 38.8% | -0.176 |
| intraday (2/3) | 13 | 12 | 2 | 6 | 4 | 25.0% | 33.3% | -0.203 |

Only 13 intraday records exist because the survivor score gives alignment 3 a 10-point head start under a 30-name cap. **Intraday vs swing cannot be compared**: 12 closed intraday trades, 8 of them decisive.

### Scan time

| Scan | Closed | W | L | Scr | Hit | Adj | Avg R |
|---|---|---|---|---|---|---|---|
| premarket (nominal 06:00 PT) | 301 | 18 | 95 | 188 | 15.9% | 37.2% | -0.203 |
| preclose (nominal 12:30 PT) | 770 | 67 | 231 | 472 | 22.5% | 39.4% | -0.166 |

Fisher exact test on wins vs losses: p = 0.17. Not distinguishable from chance. By actual run time (not the label): scans before 07:30 PT hit 15.9% (n=113 decisive), scans between 11:00 and 13:00 PT 20.3% (n=227), scans after the 13:00 close 29.6% (n=71). Direction is consistent with "entry price from before the open is worse", but the differences are not tested to significance and the after-close group is mostly one night (2026-07-31) plus late runs.

Entry-to-open gap for premarket records, in units of the stop distance: median -0.08; 23.6% opened more than 0.5 stop-distances against the position, 14.3% more than 0.5 in favour.

### Direction

| Bias | Closed | W | L | Scr | Hit | Adj |
|---|---|---|---|---|---|---|
| long | 678 | 67 | 240 | 371 | 21.8% | 37.2% |
| short | 393 | 18 | 86 | 289 | 17.3% | 41.3% |

Fisher p = 0.40.

### Conviction

| Conviction | Closed | W | L | Scr | Hit | Adj | Avg R |
|---|---|---|---|---|---|---|---|
| 0 | 76 | 13 | 16 | 47 | 44.8% | 48.0% | -0.013 |
| 2 | 45 | 7 | 10 | 28 | 41.2% | 46.7% | -0.023 |
| 3 | 146 | 20 | 36 | 90 | 35.7% | 44.5% | -0.046 |
| 4 | 160 | 6 | 51 | 103 | 10.5% | 35.9% | -0.262 |
| 5 | 208 | 11 | 74 | 123 | 12.9% | 34.9% | -0.258 |
| **6** | 284 | 15 | 92 | 177 | **14.0%** | 36.4% | -0.224 |
| 7 | 140 | 12 | 46 | 82 | 20.7% | 37.9% | -0.159 |
| 8 | 12 | 1 | 1 | 10 | 50.0% | 50.0% | +0.082 |

Conviction does not rank outcomes the way it is meant to. Low conviction (0 to 3) beats the alerted bands: hit 39.2% (40/102) vs 14.6% (45/309) for 4 to 8, Fisher p < 0.0001. Among the alerted levels, 6 -> 7 improves (14.0% -> 20.7%) and 8 is 1 win out of 2 decisive. Several conviction-0 to 3 rows are weakly specified (`rr` below 1.5, forced to 0 by the prompt rule); their better hit rate coincides with wider tolerance of noise, not with better selection. Whether conviction >= 6 (16.8%) differs from < 6 (23.4%) is not significant (p = 0.11). Claude's own prompt says conviction is a within-batch ranking, so this is consistent with it carrying no absolute information.

### Day of week (of the scan)

| Day | Closed | W | L | Scr | Hit | Adj |
|---|---|---|---|---|---|---|
| Monday | 366 | 34 | 107 | 225 | 24.1% | 40.0% |
| Tuesday | 241 | 19 | 52 | 170 | 26.8% | 43.2% |
| Wednesday | 150 | 12 | 44 | 94 | 21.4% | 39.3% |
| Thursday | 120 | 3 | 44 | 73 | 6.4% | 32.9% |
| Friday | 194 | 17 | 79 | 98 | 17.7% | 34.0% |

The scans did not run evenly across weekdays (only 29 distinct dates have any record), and many trades opened on one day resolve on another. Thursday's 3 wins in 47 decisive trades comes from a handful of scan dates. No inference is warranted.

### Sector (S&P 500 GICS where available)

| Sector | Closed | W | L | Scr | Hit |
|---|---|---|---|---|---|
| Health Care | 160 | 11 | 54 | 95 | 16.9% |
| Information Technology | 164 | 15 | 57 | 92 | 20.8% |
| Financials | 141 | 8 | 34 | 99 | 19.0% |
| Industrials | 137 | 7 | 34 | 96 | 17.1% |
| Consumer Discretionary | 99 | 14 | 31 | 54 | 31.1% |
| Utilities | 80 | 9 | 20 | 51 | 31.0% |
| Communication Services | 64 | 4 | 18 | 42 | 18.2% |
| Energy | 58 | 7 | 25 | 26 | 21.9% |
| Consumer Staples | 50 | 0 | 20 | 30 | 0.0% |
| Materials | 46 | 4 | 15 | 27 | 21.1% |
| Real Estate | 48 | 3 | 15 | 30 | 16.7% |
| Unclassified (not in S&P table: CTVA, EQR, PSKY, TAP, TTD, WBD, futures) | 24 | 3 | 3 | 18 | 50.0% |

Consumer Staples 0 wins in 20 decisive trades and Consumer Discretionary 14/45 stand out, but with 11 sectors, small cells and about 28 effective scan days, sector differences cannot be separated from luck. No sector has more than 164 closed trades.

### Symbol

382 distinct tickers; median 3 records per ticker, maximum 11 (PFE). The top 10 tickers make up 8.1% of records. 61 tickers have 5 or more closed records. No ticker has enough decisive trades for a rate to mean anything (PFE 0 wins in 2 decisive; VEEV 3 of 5; APA 2 of 4; CRWD 1 of 7). Symbol-level conclusions: none.

### Month

| Month | Records | W | L | Scr | Hit | Avg R |
|---|---|---|---|---|---|---|
| 2026-07 (one scan) | 30 | 4 | 5 | 21 | 44.4% | +0.050 |
| 2026-08 | 712 | 55 | 272 | 384 | 16.8% | -0.248 |
| 2026-09 | 120 | 10 | 13 | 97 | 43.5% | +0.012 |
| 2026-10 | 270 | 16 | 36 | 158 | 30.8% | -0.075 |

August is 63% of all records (two scans a day were still landing in the window) and carries almost all the loss: -176 R of the -189 R. September and October have far fewer, sparser trades and a 75 to 81% scratch rate (see section 4).

## 3. Moves vs target and stop distance

Daily-bar view (yfinance, unadjusted), swing records unless stated.

- Median stop distance 3.74% of entry; median target distance 5.85%. Mean `rr` 1.76.
- Stop distance in daily ATR(14) units (ATR measured at the prior close; n = 1103 swing): median 1.50, 25th percentile 0.61, 75th 2.00, 90th 2.15. The prompt asks for 1.5 to 2.5. **45.7% are inside that band, 50.0% are below 1.5 ATR, and 41.6% are below 1.0 ATR.** 4.4% are above 2.5. Target distance median 2.08 ATR.
- Hit rate by stop size in ATR: under 1 ATR: 49 wins / 271 decisive = 18.1% (464 records); 1 to 1.5: 10.7% (3/28); 1.5 to 2.5: **30.8%** (28/91, 504 records, but 79.7% of them scratch); 2.5 to 4: 0/8.
- Realized volatility vs the ATR the stops should be built on: mean true range over the next 5 sessions was 0.97 of the signal-time ATR (median 0.90). Volatility did not collapse after entry, so the ATR was a fair guide; the stops that sit below 1 ATR are simply tight relative to normal daily noise.
- Next-session move (first session after the signal, close vs entry), relative to the trade's own stop distance: median absolute move 0.33 of the stop distance and 0.19 of the target distance. 39.1% of swing trades closed that first session within 0.25 stop distances of entry. Using high/low: median favourable excursion 0.27 R and median adverse excursion 0.33 R in the first session; 41.8% of all trades stayed within 0.5 R both ways ("barely moved"). 16.1% touched +1 R in the first session; 20.5% touched -1 R; 8.0% reached the target.
- Scratched trades are not flat in the long run: replaying the same stops and targets for 5 sessions, the 660 journal scratches would have become 80 wins, 149 losses and 431 still-unresolved timeouts. In other words 12% of scratches would have been wins and 23% losses with a longer look.

## 4. How outcomes were actually measured (affects every number above)

- **Scoring window ≈ one session.** Scratch fires once cumulative inspected 30m bars exceed 12 (a regular session has 13). Scratches close with `bars_open` between 13 and 24; 227 of 660 closed at exactly 13. PLAN.md describes a swing hold of 2 to 4 days.
- **Dense period (Jul 31 to Aug 26, two scans a day):** median 1 calendar day from signal to resolution; 28% of closed trades had at least one full trading session that was never inspected; scratch rate 53.6%.
- **Sparse period (from Aug 27, scans mostly skipped):** median 7 calendar days to resolution; 98.9% of closed trades had at least one un-inspected full session (mean 4.5 un-inspected sessions); scratch rate 77.5%. The resolver only looks at bars after the *nominal* previous scan, so in this period a trade is judged on roughly the last half session before the run that closed it. 31 of 389 scratches (8.0%) in the group with an un-inspected session did touch their stop or target in the gap, so the recorded outcome is wrong for at least those; the other scratches are unaffected only if the price stayed between stop and target throughout.
- **Why scans were skipped:** GitHub started the 06:00 and 12:30 PT cron slots about 4 hours and 2 hours late in the cases sampled, outside the +/-90 minute guard (`01-structure.md`, bug 1).

Net effect: the scratch rate (61.6% overall) is largely a property of the measurement window and of which runs happened, not a statement about the signal.

## 5. R-multiples and equity curve

Journal convention, in order of resolving run:

- Closed trades 1071, mean R -0.177, standard deviation 0.703, total **-189.1 R**.
- Maximum drawdown from the running peak: **-197.3 R** (the curve peaks at +4.9 R after the third resolved trade, bottoms at -192.3 R at trade 1012 and ends at -189.1 R).
- By resolution month: Aug -168.3 R (663 trades, -0.254 each); Sep -5.6 R (138, -0.041); Oct -15.2 R (270, -0.056).
- Alerted only (conviction >= 6): 436 trades, -84.8 R, max drawdown -89.8 R, mean -0.195.
- Below the alert gate (conviction < 6): 635 trades, -104.3 R, mean -0.164.
- Both halves lose; the Sep/Oct figures are close to flat only because 75 to 81% of those trades are scratches valued at 0.

R is capped at -1 for every loss by construction. The journal stores no fills, so gap-through-stop losses larger than 1 R are invisible. For reference, in 106 of the 323 recorded losses the worst move over the next 5 sessions exceeded 3x the stop distance.

### Best and worst trades

- Largest recorded wins by `rr`: WBD long 2026-08-17 06:04 (+3.00 R), APA long 2026-08-17 (+2.50), HPE long 2026-08-12 (+2.46), APA long 2026-08-07 (+2.19), FOX long 2026-08-13 (+2.18). Every top-5 win is a long.
- The journal cannot rank losers because every loss is exactly -1 R. By the size of the move against the position over the next 5 sessions, the worst are four PCG long records on Aug 24 to 26 (about -28% each from entry, only 5.4 to 8.4% stops) and CDW long on 2026-08-04 (-26.6%). The PCG move is large enough that I did not verify it against a second data source; treat it as possibly a data artifact.

## 6. Is 20.7% actually bad? A zero-edge benchmark

With a stop at distance 1 and a target at distance `rr` and no drift, the long-run chance of reaching the target first is 1 / (1 + `rr`). With the average `rr` of 1.76 that is **36.3%**; with the mean winning `rr` of 1.61 it is 38.3%. The observed 20.7% (17.0% to 24.9%) is clearly below that.

Two things cut the benchmark in opposite directions:
- A short scoring window and stops closer than targets make the stop more likely to be touched first than the infinite-horizon formula says, which lowers the expected hit rate.
- The mirror-image placebo (same entries, same distances, opposite direction) held for 1 session produced 92 wins / 268 decisive = 34.3% while the actual signals produced 79 / 316 = 25.0%.

So 20.7% is both below the naive benchmark and below what the same entries produce when traded in the opposite direction. How much is mechanics and how much is the direction signal is the subject of `03-strategy-and-causes.md`.

## 7. Sample size and what can be concluded

- 1071 closed records look like a lot, but they are **heavily clustered**: 29 scan dates, 38 date/scan batches, 30 symbols per batch chosen from one common filter. Daily bars on the same date move together, and 34.5% of records (390) repeat a ticker and direction already logged within the previous 5 calendar days (382 distinct tickers, 491 ticker-direction pairs, 846 ticker-direction-weeks). The effective number of independent observations is nearer to the number of scan dates (about 28) than to 1071.
- Only 411 closed trades are decisive (win/loss), and only 85 are wins.
- Everything happens in one window: SPY went from 747.0 to 772.3 (+3.4%), VIX stayed between 14.2 and 20.0 (mean 15.9). No bear market, no volatility spike, no earnings season control.

**Can be said with the data:**
1. The recorded hit rate is 20.7% overall and 16.8% for alerts that were actually sent; the 14% figure applies to the conviction-6 bucket.
2. The average realized R is negative (-0.177) in every month with enough trades, and for both halves of the conviction split.
3. 61.6% of trades are scratches, mostly because the scoring window is about one session and because of skipped scans.
4. 41.6% of swing stops are tighter than 1 daily ATR, against a prompt that asks for 1.5 to 2.5.
5. Conviction does not rank outcomes: low-conviction setups did better than 4 to 7.

**Cannot be said with the data:**
- Whether intraday (2/3 alignment) beats swing (3/3): 12 closed intraday trades.
- Any symbol, sector or day-of-week effect: cells too small, scan days too few.
- Whether premarket is worse than preclose (p = 0.17), or long worse than short (p = 0.40).
- Whether the strategy would work in another market regime: only one calm, mildly rising regime is covered.
- True loss size: no fills recorded.
- Whether the news input adds or subtracts value: no journal field separates it.
