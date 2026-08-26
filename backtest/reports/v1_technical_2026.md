# Backtest report — v1_technical

> **TECHNICAL PROXY GATE -- READ BEFORE USING ANY NUMBER BELOW. Live, survivors of filter.run_filter are scored by analyst.py (Claude, with news headlines), floored at config.MIN_CONVICTION, sorted by conviction and capped at config.MAX_ALERTS. This backtest has no analyst: no free historical news archive covers this date range, so analyst.py cannot be replayed at all. In its place, each slot's survivors are ranked by filter.score_survivor and the top config.MAX_ALERTS are kept. That approximates live alert VOLUME, which is what makes these hit rates comparable to the live journal's at all -- roughly the same number of alerts per scan, rather than the 30-per-slot MAX_SURVIVORS cap. It does NOT replay Claude's conviction judgement. Signal SELECTION therefore differs from live even where volume matches, and the conviction column below is a within-slot rank, not a model score. Do not read these rates as a forecast of the live system's performance; read them as the quality of the technical filter's ordering, in isolation.**

## Run

- window: `2026-01-02 09:00:00` → `2026-08-21 15:30:00`
- scan slots replayed: 320
- universe: 441 tickers with cached 30m history
- signals before the technical_proxy_gate: 9599 (MAX_SURVIVORS = 30)
- alerts after the technical_proxy_gate: 2560 (MAX_ALERTS = 8 per slot)
- conviction column: rank proxy: score_survivor rank within the scan slot, mapped 0-10 (top-ranked = 10). NOT analyst.py model conviction; no news input.
- wall clock: 0.3 min

## Overall

| segment | hit_rate | adj_hit_rate | scratch_rate | avg_rr | wins | losses | scratches | open | resolved |
|---|---|---|---|---|---|---|---|---|---|
| all | 60.7% | 51.2% | 88.8% | +0.058 | 173 | 112 | 2251 | 24 | 2536 |

Resolved 2536 of 2560 alerts; 24 still open at the end of the window (an open alert has no outcome and is excluded from every rate above).

## By strategy

| strategy | hit_rate | adj_hit_rate | scratch_rate | avg_rr | wins | losses | scratches | open | resolved |
|---|---|---|---|---|---|---|---|---|---|
| v1_technical | 60.7% | 51.2% | 88.8% | +0.058 | 173 | 112 | 2251 | 24 | 2536 |

## By month

| month | hit_rate | adj_hit_rate | scratch_rate | avg_rr | wins | losses | scratches | open | resolved |
|---|---|---|---|---|---|---|---|---|---|
| 2026-01 | 40.4% | 48.6% | 85.3% | +0.002 | 19 | 28 | 273 | 0 | 320 |
| 2026-02 | 82.9% | 54.4% | 86.5% | +0.145 | 34 | 7 | 263 | 0 | 304 |
| 2026-03 | 31.8% | 48.9% | 93.8% | -0.013 | 7 | 15 | 330 | 0 | 352 |
| 2026-04 | 76.1% | 53.6% | 86.3% | +0.124 | 35 | 11 | 289 | 1 | 335 |
| 2026-05 | 73.3% | 53.3% | 85.9% | +0.117 | 33 | 12 | 275 | 0 | 320 |
| 2026-06 | 64.9% | 51.6% | 89.0% | +0.068 | 24 | 13 | 299 | 0 | 336 |
| 2026-07 | 44.8% | 49.6% | 91.8% | +0.010 | 13 | 16 | 323 | 0 | 352 |
| 2026-08 | 44.4% | 49.5% | 91.7% | +0.009 | 8 | 10 | 199 | 23 | 217 |

## How to read these numbers

- `hit_rate` = wins / (wins + losses). `adj_hit_rate` = (wins + 0.5 x scratches) / resolved. `scratch_rate` = scratches / resolved. `avg_rr` realizes each signal's own `rr` on a win, -1.0 on a loss or an ambiguous bar, 0.0 on a scratch. All four come from `journal._rate_block`, unmodified — the same function that produces the live paper-trading digest.
- Every signal's target sits at exactly `config.MIN_RR`, so `rr` is constant across the run and `avg_rr` is a deterministic function of the hit and scratch rates rather than an independent result.
- A bar that touches both target and stop resolves as `ambiguous` and counts as a loss, exactly as it does live. 30m bars cannot say which came first.
- See `backtest/README.md` for scope, data sources and methodological limits.
