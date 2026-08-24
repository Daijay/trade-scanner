# Trade-Scanner Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A point-in-time backtest engine that replays trade-scanner's existing technical filter over historical bars and reports hit_rate / adj_hit_rate / scratch_rate / avg_rr directly comparable to the live paper-trading journal.

**Architecture:** A read-only `BarStore` owns the parquet cache and is the only component that ever sees full history. It hands out `PointInTimeView` objects bound to a simulated `as_of` timestamp; every read is truncated at the storage boundary before it returns, so a view cannot physically hold a future row. A `Strategy` protocol adapts v1's existing `filter.py` / `indicators.py` to that view without modifying them. Results are resolved and scored through `journal.py`'s existing functions.

**Tech Stack:** Python 3.11+, pandas, pyarrow (parquet), pytest. No new dependencies.

**Spec:** This document (user brief, 2026-08-24). Phases 0-4 as specified.

## Global Constraints

- **Do not modify the live scan pipeline.** `main.py`, `analyst.py`, `news.py`, `notify.py`, `digest.py`, `config.py` trading parameters, and `filter.py`'s live logic are off limits. The engine imports and calls them; it never edits them.
- All new code lives under `backtest/` and `tests/backtest/`.
- Reuse `journal.py`'s `resolve_alert`, `_rate_block`, and `compute_stats` rather than reimplementing outcome logic — comparability to live numbers is the entire point.
- Cached data lives under `data/` (already gitignored). Never commit parquet files.
- No news or sentiment input anywhere in the engine. This is a documented scope decision, not an oversight (see Task 12).

---

## Decision Gate (blocks Phase 1)

Two facts, both measured against the live APIs on 2026-08-24, determine the data source. **This gate must be closed by the user before Task 2 begins.**

**Fact 1 — yfinance cannot supply sub-hourly history for this date range.**

| interval | lookback limit | Jan 2026 reachable? |
|---|---|---|
| `1m` | 30 days | no |
| `30m` | 60 days | no |
| `1h` | 730 days | **yes** |

v1's filter runs on `{30m, 4h, daily}`. yfinance alone therefore **cannot reproduce the 30m timeframe at all** for Jan-Aug 2026, so `alignment` — which counts agreeing timeframes and drives both `horizon` and the survivor score — cannot be computed faithfully from yfinance.

**Fact 2 — hfdatalibrary's post-March-2022 data is IEX-only**, ~2-3% of consolidated volume, per its own Known Issues page. Absolute volume is unusable and OHLC deviates from the consolidated tape.

**Recommended resolution: hybrid.** The two sources fail in complementary places.

| frame | source | rationale |
|---|---|---|
| `daily` | yfinance daily | consolidated volume + close; `MIN_AVG_VOLUME` and `MIN_PRICE` read only this frame (`filter.py:13-17`), so the liquidity gate stays correct |
| `4h` | hfdatalibrary 1-min, resampled | bar shape only |
| `30m` | hfdatalibrary 1-min, resampled | bar shape only; otherwise unobtainable at any price that is free |

`vol_ratio` is volume divided by its own 20-period mean, so an IEX-only numerator and denominator largely cancel — degraded but not meaningless. `atr14` on 30m/4h will deviate from consolidated; `atr14` on daily, which drives stop sizing, stays correct.

**Fallback if the user declines to register:** yfinance 1h only, with `30m` proxied by 1h bars and alignment computed over `{1h-as-30m, 4h, daily}`. This is a documented approximation, not a faithful replay, and Task 12's README must say so plainly.

**User action required:** registration at hfdatalibrary.com requires accepting the IEX Historical Data Terms of Use under a named account. That is not an agreement an assistant can enter on the user's behalf. The user registers (ORCID/Google SSO or email, no card), then supplies the API key via `.env` as `HFDL_API_KEY`.

---

## Known Methodological Limits (must appear in the README, Task 12)

1. **Survivorship bias.** `data.build_universe()` scrapes *today's* S&P 500 / Nasdaq-100 membership. Replaying Jan-Aug 2026 against the Aug 2026 constituent list over-represents names that survived and were added. No free point-in-time constituent list exists. State the bias and its direction (optimistic); do not hide it.
2. **Ticker reassignment.** VMRK (crashed the live scanner, fixed 2026-08-24) and BNY (two instruments concatenated under one symbol, +1272% phantom bar) prove the universe contains renamed tickers whose history splices unrelated instruments. Task 4 screens for these.
3. **Earnings dates are as-known-today.** `yfinance.Ticker.get_earnings_dates()` returns the schedule as it stands now, not as it was known at simulated time. Since earnings are scheduled weeks ahead this is a small leak, but it is a leak. Documented, not silently accepted.
4. **Dividend adjustment.** yfinance back-adjusts daily bars but not intraday. Measured: CVX intraday-vs-daily close diff decays 2.8% (Jan) to 0.76% (Aug), matching $5.34 of dividends paid. The engine must pick one convention per frame and record it in the manifest; mixing adjusted daily with unadjusted intraday silently mis-prices high-yield names by up to ~3%.

---

## File Structure

| File | Responsibility |
|---|---|
| `backtest/__init__.py` | package marker |
| `backtest/store.py` | `BarStore` — owns parquet cache, sole holder of full history |
| `backtest/pit.py` | `PointInTimeView` — the truncating read boundary. **Critical file.** |
| `backtest/calendar.py` | trading-day / scan-slot iteration for the simulation clock |
| `backtest/earnings.py` | earnings-date cache + blackout-window lookup |
| `backtest/strategy.py` | `Strategy` protocol + `Signal` dataclass |
| `backtest/strategies/v1_technical.py` | adapts v1 `filter.py` / `indicators.py` to a view |
| `backtest/strategies/v2_dipbuy.py` | interface stub for v2, raises `NotImplementedError` |
| `backtest/engine.py` | simulation loop: clock -> view -> strategy -> signals |
| `backtest/report.py` | resolution + stats via `journal.py`, report rendering |
| `backtest/download.py` | Phase 1 bulk fetch (hfdatalibrary and/or yfinance) |
| `backtest/screen.py` | data-integrity screen for corrupt tickers |
| `backtest/README.md` | scope decision + methodological limits |
| `tests/backtest/test_pit_leakage.py` | adversarial future-data tests. **Critical test file.** |

---

## Phase 0 — Validate the data source

### Task 1: Sample fetch and source comparison

**Files:** Create `backtest/download.py`, `tests/backtest/test_download.py`

**Interfaces:**
- Produces: `fetch_hfdl(tickers, start, end, api_key) -> dict[str, pd.DataFrame]`, `fetch_yf(tickers, start, end, interval) -> dict[str, pd.DataFrame]`

- [ ] **Step 1: Confirm the gate is closed.** Do not start until the user has either supplied `HFDL_API_KEY` or explicitly chosen the yfinance-only fallback. If neither, stop and ask.
- [ ] **Step 2: Fetch 5 tickers x 1 month of 1-min data** — AAPL, NVDA, CVX, PLTR, SMCI. Deliberately mixed: mega-cap, high-yield dividend payer, no-dividend, high-volatility.
- [ ] **Step 3: Run the comparison harness.** Resample each 1-min series to daily and compare against yfinance daily over the same window. Report max/median close diff %, volume ratio, bar-count coverage, and count of trading days with zero bars.
- [ ] **Step 4: Apply accept/reject criteria.** Accept if median close diff < 0.5% on non-dividend names and >= 95% of trading days have bars. Reject and fall back otherwise.
- [ ] **Step 5: Report to the user before Task 2.** Hard stop — Phase 1 is a multi-GB download and must not start on an unvalidated source.

---

## Phase 1 — Data download

### Task 2: Bulk price download and parquet cache

**Files:** Modify `backtest/download.py`, create `tests/backtest/test_store.py`

**Interfaces:**
- Consumes: `fetch_hfdl` / `fetch_yf` from Task 1
- Produces: `data/ohlcv_1m/{TICKER}.parquet` (or `data/ohlcv_1h/` on fallback); `BarStore` reads these

**Scale warning:** ~500 tickers x 390 bars/day x ~160 trading days is roughly 31M bars. Extrapolating from the measured 1h cache (~45 bytes/bar compressed), expect **1.2-1.5 GB**. Confirm free disk before starting.

- [ ] **Step 1: Build the universe** via `data.build_universe()` — already S&P 500 + Nasdaq-100 + `EXTRA`, capped at `UNIVERSE_CAP`. Do not reimplement it.
- [ ] **Step 2: Download with resumability.** Skip tickers whose parquet already exists and covers the full range. A 500-ticker download will be interrupted; it must resume without refetching.
- [ ] **Step 3: Write one parquet per ticker**, snappy-compressed, index named `timestamp`, columns `Open/High/Low/Close/Volume`.
- [ ] **Step 4: Emit a manifest** at `data/_manifest.json` recording per ticker: source, interval, first/last bar, row count, adjustment convention, download timestamp.
- [ ] **Step 5: Commit** the code, never the data.

### Task 3: Earnings-date cache

**Files:** Create `backtest/earnings.py`, `tests/backtest/test_earnings.py`

**Interfaces:**
- Produces: `EarningsCalendar.load(path)`, `.dates_for(ticker) -> list[date]`, `.in_blackout(ticker, on_date, days_before, days_after) -> bool`

- [ ] **Step 1: Write the failing test** — a date 2 days before a known earnings date is inside a (3,1) window; 5 days before is outside.

```python
def test_in_blackout_window():
    cal = EarningsCalendar({"AAPL": [date(2026, 4, 30)]})
    assert cal.in_blackout("AAPL", date(2026, 4, 28), days_before=3, days_after=1)
    assert not cal.in_blackout("AAPL", date(2026, 4, 25), days_before=3, days_after=1)

def test_ticker_with_no_earnings_never_raises():
    cal = EarningsCalendar({})
    assert cal.dates_for("SPY") == []
    assert not cal.in_blackout("SPY", date(2026, 4, 28), 3, 1)
```

- [ ] **Step 2: Run, confirm both fail.**
- [ ] **Step 3: Implement** using `yfinance.Ticker(t).get_earnings_dates(limit=...)`, cached to `data/earnings.parquet`. Tolerate tickers with no earnings data (ETFs) by returning an empty list, never raising.
- [ ] **Step 4: Run tests, confirm pass.**
- [ ] **Step 5: Commit.**

### Task 4: Data integrity screen

**Files:** Create `backtest/screen.py`, `tests/backtest/test_screen.py`

- [ ] **Step 1: Write failing tests** for the three defects already observed in real data: a single-bar return above 500% (BNY), an interior gap longer than 30 calendar days (BNY), and a ticker holding under 50% of the median bar count.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement** `screen_store(store) -> tuple[list[str], dict[str, str]]` returning clean tickers and a reason string per rejection. Write `data/_screen_report.csv`.
- [ ] **Step 4: Run tests, confirm pass. Then run against the real cache** and eyeball the reject list — every rejection must have a plausible corporate-action explanation.
- [ ] **Step 5: Commit.**

### Task 4b: Cross-frame price reconciliation (added 2026-08-24, after Task 4)

**Files:** Create `backtest/reconcile.py`, `tests/backtest/test_reconcile.py`

**Interfaces:**
- Produces: `daily_ratio(h30, daily) -> pd.Series`, `detect_segments(ratio, jump_tol) -> list[tuple]`, `reconcile_30m(h30, daily) -> tuple[pd.DataFrame, dict]`, `reconcile_all(root) -> dict`
- Produces on disk: `data/ohlcv_30m_adj/{TICKER}.parquet`, `data/_reconcile_report.csv`

**Why this task exists.** Task 4's screen catches *spliced* tickers. It does not
catch the defect found immediately afterwards, because the affected series are
individually well-formed: the 30m and daily frames disagree about corporate
actions. The 30m feed (hfdatalibrary/IEX) is delivered as traded and is not
back-adjusted for splits; the daily feed (yfinance, `auto_adjust=False`) is
nevertheless restated for them. So for any ticker with an action inside the
window, the 30m series sits on one price scale before the action and another
after, while daily runs continuously.

Measured on the real cache as `(30m daily-aggregated close) / (daily close)`,
42 of 441 tickers drifted more than 1% between their first and last 20 days.
Two cohorts:

- **Corporate actions (9 tickers, 2.4%-2394% drift):** BKNG 24.94→1.00 (25:1),
  KLAC 9.99→1.00 (10:1), CVNA 5.00→1.00, CRWD 4.00→1.00, DD 0.333→1.00,
  FDX 1.240→1.00, SPGI 1.057→1.00, HON 0.953→1.00, CME 0.976→1.00.
- **Dividends (33 tickers, 0.9%-1.7% drift):** diagnosed. Each shows exactly one
  step, landing exactly on the **first ex-dividend date inside the window**, of a
  size equal to that single dividend divided by the price to within a few basis
  points (verified against `yfinance` dividends for VZ, MO, KHC, PFE, VICI,
  TROW, BX, TGT — 8 of 8 match). Later ex-dividend dates in the same window
  produce no step, so the 30m feed is back-adjusted for exactly one dividend and
  none after it. Why only the first is affected is a property of the vendor's
  pipeline that is **not** established here; the correction depends only on the
  empirical pattern, not on the mechanism.

This is Known Methodological Limit #4 turning up as a concrete bug rather than a
caveat. Left uncorrected it mis-prices `alignment`, any daily-ATR stop compared
against a 30m entry, and every gap check, by up to 25x on Group A.

**Design.**
- Empirical, never a split table: the correction measures the disagreement
  between the two frames the engine actually reads, so it also catches causes
  nobody has diagnosed (see MNST below).
- Segmentation runs on a **3-point centred median filter**, which passes a true
  step through with its boundary intact but annihilates an isolated one-day
  spike from a stale last print. Segments under 3 observations are merged away,
  which covers a spike on the first or last date where the filter has no room.
- `jump_tol` defaults to **0.008**, chosen from measurement rather than taste.
  Each ticker has at most one step, so its *second*-largest filtered jump
  estimates its own noise floor: median 0.0019, p95 0.0048, p99 0.0068. 0.008
  sits above 99% of that floor, below the smallest step that matters (TGT's
  0.94% dividend), and below the 1% bar the cache is accepted against.
- Prices are divided by the segment's median ratio and **volume multiplied by
  it**, so `price * volume` notional is preserved and the volume level is
  continuous across a split boundary — which is what `vol_ratio`'s 20-period
  mean needs.
- **The raw download is never modified.** Corrected copies go to
  `data/ohlcv_30m_adj/`, leaving `data/ohlcv_30m/` as the record of what the
  vendor actually sent. The correction stays auditable and reversible, and
  `data/_reconcile_report.csv` records every segment, factor, boundary date, and
  before/after drift.

- [x] **Step 1: Write the failing tests first** — a synthetic 25:1 split
  corrected to a flat 1.0, a reverse split (0.333), a clean ticker left
  unchanged with no spurious segmentation, a single-day outlier that must not
  create a boundary, notional preserved across the boundary, and the input frame
  not mutated in place.
- [x] **Step 2: Run, confirm they fail** (`ModuleNotFoundError`).
- [x] **Step 3: Implement** `backtest/reconcile.py`.
- [x] **Step 4: Run tests, confirm pass** — 15 passed.
- [x] **Step 5: Run `reconcile_all` on the real cache and verify.** 441 tickers,
  76 with a detected boundary. Drift over 1% went **42 → 0 of 441**; worst
  remaining drift 0.87%, median 0.15%. Group A: BKNG 2393.9%→0.019%,
  KLAC 898.4%→0.134%, CVNA 399.8%→0.016%, CRWD 300.0%→0.001%, DD 66.8%→0.373%,
  FDX 23.6%→0.364%, HON 5.12%→0.481%, SPGI 5.46%→0.224%, CME 2.40%→0.014%.
- [x] **Step 6: Commit** the code. Never the parquet.

**One finding worth carrying forward.** MNST is the only 3-segment ticker: its
ratio is exactly 2.0 for three consecutive sessions (Jul 20-22) and 1.0 on
either side, plus an isolated 2.0 on Jul 31. That is a transient feed defect,
not a corporate action, and it is *not* diagnosed. The reconciler rescales the
three-day block (correct — it is a scale error whatever caused it) and leaves
the isolated one-day spike alone, which is why MNST's `max_residual` reads
100.3%. Repairing single-bad-print outliers is `screen.py`'s job, not this
module's.

**Consequence for later tasks.** `BarStore` (Task 5) should read
`data/ohlcv_30m_adj/` rather than `data/ohlcv_30m/`, and the manifest should
record which of the two it used.

---

## Phase 2 — Point-in-time engine (CRITICAL — review gate on Task 6)

### Task 5: BarStore

**Files:** Create `backtest/store.py`, `tests/backtest/test_store.py`

**Interfaces:**
- Produces: `BarStore(root: Path)`, `.tickers() -> list[str]`, `.view(as_of) -> PointInTimeView`

**Design constraint:** `BarStore` is the only object that reads full history, and it exposes **no public method returning untruncated bars.** The raw reader is a module-private *function*, not a method, so a `PointInTimeView` cannot reach it through an attribute chain.

- [ ] **Step 1: Write the failing test.**

```python
def test_store_exposes_no_public_untruncated_reader():
    store = BarStore(FIXTURE_ROOT)
    public = [n for n in dir(store) if not n.startswith("_")]
    assert set(public) <= {"tickers", "view", "root"}, f"unexpected public API: {public}"
```

- [ ] **Step 2: Run it, confirm it fails** (module does not exist).
- [ ] **Step 3: Implement** `BarStore`, with `_read_raw(root, ticker)` as a module-level private function rather than a bound method.
- [ ] **Step 4: Run tests, confirm pass.**
- [ ] **Step 5: Commit.**

### Task 6: PointInTimeView — the hard constraint

**Files:** Create `backtest/pit.py`, `tests/backtest/test_pit_leakage.py`

**Interfaces:**
- Consumes: `BarStore` from Task 5
- Produces: `PointInTimeView`, `.as_of` (read-only), `.bars(ticker, timeframe, lookback=None) -> pd.DataFrame`, `.frames_for(ticker) -> dict[str, pd.DataFrame]`

**Design:** The view does **not** hold a reference to the store. It holds a closure built by `_make_truncated_reader(root, as_of)` which applies the cut before returning anything. There is no code path from a view to an untruncated frame. `__slots__` plus a read-only `as_of` property prevent retargeting the clock after construction.

```python
def _make_truncated_reader(root: Path, as_of: pd.Timestamp):
    def read(ticker: str) -> pd.DataFrame:
        df = _read_raw(root, ticker)          # module-private
        return df.loc[df.index <= as_of]      # the cut, before anything else sees it
    return read


class PointInTimeView:
    __slots__ = ("_read", "_as_of")

    def __init__(self, root: Path, as_of: pd.Timestamp):
        object.__setattr__(self, "_read", _make_truncated_reader(root, as_of))
        object.__setattr__(self, "_as_of", as_of)

    @property
    def as_of(self) -> pd.Timestamp:
        return self._as_of
```

- [ ] **Step 1: Write the adversarial suite first.** All seven must fail before any implementation exists.

```python
def test_bars_never_exceed_as_of(view_factory):
    v = view_factory("2026-03-15 12:00")
    assert v.bars("AAPL", "daily").index.max() <= v.as_of


def test_explicit_future_request_returns_nothing(view_factory):
    v = view_factory("2026-03-15 12:00")
    df = v.bars("AAPL", "daily")
    assert df[df.index > v.as_of].empty


def test_as_of_cannot_be_reassigned(view_factory):
    v = view_factory("2026-03-15 12:00")
    with pytest.raises((AttributeError, TypeError)):
        v.as_of = pd.Timestamp("2026-08-01")


def test_view_holds_no_reference_to_full_history(view_factory):
    """Strongest guarantee: no attribute chain from a view back to an
    untruncated frame."""
    v = view_factory("2026-03-15 12:00")
    assert not hasattr(v, "_store")
    for name in v.__slots__:
        assert not isinstance(getattr(v, name), pd.DataFrame)


def test_resample_truncates_before_aggregating(view_factory):
    """Subtle leak: resampling full history and truncating afterwards lets
    the bucket containing as_of absorb post-as_of minutes into its
    High/Low/Close. Truncation must happen first."""
    v = view_factory("2026-03-15 11:00")          # deliberately mid-bucket
    bars = v.bars("AAPL", "4h")
    last = bars.iloc[-1]
    minute = v.bars("AAPL", "1m")
    window = minute[minute.index >= bars.index[-1]]
    assert last["High"] == window["High"].max()
    assert last["Close"] == window["Close"].iloc[-1]


def test_earlier_view_unaffected_by_later_view(view_factory):
    """Guards against a caching bug where a later view mutates shared state
    and contaminates an earlier one."""
    early = view_factory("2026-02-01 12:00")
    before = early.bars("AAPL", "daily").copy()
    _ = view_factory("2026-08-01 12:00").bars("AAPL", "daily")
    pd.testing.assert_frame_equal(early.bars("AAPL", "daily"), before)


def test_frames_for_all_timeframes_respect_as_of(view_factory):
    v = view_factory("2026-03-15 12:00")
    for tf, df in v.frames_for("AAPL").items():
        assert df.index.max() <= v.as_of, tf
```

- [ ] **Step 2: Run all seven, confirm all fail.**
- [ ] **Step 3: Implement `PointInTimeView`** per the design above.
- [ ] **Step 4: Run the suite, confirm all seven pass.**
- [ ] **Step 5: Mutation-check the guard.** Temporarily widen the cut to `df.loc[df.index <= as_of + pd.Timedelta(days=1)]` and confirm the suite goes red. A leakage test that cannot detect a deliberately introduced leak is worthless. Revert afterwards.
- [ ] **Step 6: Commit.**
- [ ] **Step 7: Request code review on this task specifically** via `superpowers:requesting-code-review` before starting Task 7.

### Task 7: Simulation clock

**Files:** Create `backtest/calendar.py`, `tests/backtest/test_calendar.py`

**Interfaces:**
- Produces: `scan_slots(start, end) -> Iterator[pd.Timestamp]`

- [ ] **Step 1: Write failing tests** — weekends excluded, US market holidays excluded, and slot times matching `config.SCAN_TIMES` so simulated scans land where live scans land.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Derive holidays from gaps in a liquid ticker's daily bars (AAPL) rather than hardcoding a calendar — the data itself defines the trading days.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit.**

---

## Phase 3 — Strategy plug-in interface

### Task 8: Strategy protocol

**Files:** Create `backtest/strategy.py`, `tests/backtest/test_strategy.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Signal:
    ticker: str
    bias: str
    entry: float
    stop: float
    target: float
    rr: float
    horizon: str
    conviction: int
    reason: str


class Strategy(Protocol):
    name: str

    def generate(self, view: PointInTimeView, universe: list[str]) -> list[Signal]: ...
```

- [ ] **Step 1: Write a failing test** using a two-line fake strategy, asserting the engine calls `generate` once per slot with a view whose `as_of` equals that slot.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement the protocol and dataclass.**
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit.**

### Task 9: v1 technical strategy adapter

**Files:** Create `backtest/strategies/v1_technical.py`, `tests/backtest/test_v1_strategy.py`

**Interfaces:**
- Consumes: `Strategy`, `PointInTimeView`, and — unmodified — `filter.run_filter`, `indicators.analyze_symbol`

**Constraint:** imports `filter.py` and `indicators.py`; edits neither. Conviction is *not* available here — that comes from `analyst.py`, which requires news. Use `score_survivor`'s rank as the conviction proxy and say so in the README.

- [ ] **Step 1: Write the failing test** — given a view and a 3-ticker universe, `generate` returns signals only for tickers passing `passes_hard_filter`, with ATR-derived stops.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement:** `view.frames_for(t)` produces `{30m, 4h, daily}`, feed to `filter.run_filter({t: frames})`, map survivors to `Signal`, deriving stop from daily `atr14` and target from `config.MIN_RR`.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit.**

### Task 10: v2 dip-buy interface stub

**Files:** Create `backtest/strategies/v2_dipbuy.py`, `tests/backtest/test_v2_stub.py`

- [ ] **Step 1: Write the failing test** — `V2DipBuy` satisfies the `Strategy` protocol, and `generate` raises `NotImplementedError` naming the three inputs it will consume: price, earnings blackout, fundamentals trend.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement the stub**, with a docstring specifying the intended criteria and the `EarningsCalendar.in_blackout` and future `FundamentalsView.revenue_trend` call sites.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit.**

---

## Phase 4 — Output

### Task 11: Report generation via journal.py

**Files:** Create `backtest/report.py`, `backtest/engine.py`, `tests/backtest/test_report.py`

**Interfaces:**
- Consumes: `journal.resolve_alert`, `journal.compute_stats` (both unmodified)
- Produces: `run_backtest(strategy, start, end) -> dict`, `render_report(results) -> str`

**Constraint:** outcome resolution and rate computation go through `journal.py`. Do not reimplement hit / loss / scratch logic — identical code paths are what make these numbers comparable to live.

- [ ] **Step 1: Write the failing test** — a hand-built set of 4 signals (1 win, 1 loss, 1 scratch, 1 still open) yields `hit_rate == 0.5` and `scratch_rate == 1/3`, matching `journal.compute_stats` on equivalent records.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Resolve each signal against *post-entry* bars fetched from a view constructed at resolution time — never the generating view. Segment reports by strategy and by month.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit.**

### Task 12: README and scope documentation

**Files:** Create `backtest/README.md`

- [ ] **Step 1: Write the README** covering, explicitly:
  - (a) this backtest measures technical/fundamental signal quality **in isolation**;
  - (b) news sentiment is deliberately excluded and validated separately via live paper trading, because no free historical news archive covers this date range — an intentional scope decision, not an oversight;
  - (c) the four methodological limits above (survivorship, ticker reassignment, earnings as-known-today, dividend adjustment), each with its direction of bias;
  - (d) which data source was actually used and its volume caveat;
  - (e) that conviction is a `score_survivor` proxy, not `analyst.py` output.
- [ ] **Step 2: Commit.**

---

## Self-Review

**Spec coverage:** Phase 0 maps to Task 1. Phase 1 to Tasks 2-4 (price + earnings, as specified). Phase 2 to Tasks 5-7, with the hard constraint in Task 6 and its review gate at Task 6 Step 7. Phase 3 to Tasks 8-10 (v1 adapter plus the v2 interface point). Phase 4 to Tasks 11-12. "Don't touch the live pipeline" is in Global Constraints and enforced by Task 9's constraint note. The README scope decision is Task 12 Step 1(b).

**Gaps deliberately left:** v2's `FundamentalsView` (revenue growth, guidance changes) is named in Task 10's stub but not built. v2's strategy rules do not exist yet, and building its data layer before its rules would be speculative. The interface point is defined so it can be added later without reshaping the engine.

**Type consistency:** `PointInTimeView.bars()`, `.frames_for()`, and `.as_of` are used identically in Tasks 6, 8, 9, and 11. `Signal` fields match the setup keys `journal.log_alerts` expects, so `journal.resolve_alert` accepts them unchanged.
