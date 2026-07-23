# Phase 1 Scan Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the no-API scan pipeline (universe → OHLCV → indicators → hard filter → terminal display) so `py -3.11 main.py --dry-run` prints a survivor table of ~30 names out of a 300-500 symbol universe.

**Architecture:** `data.py` builds the symbol universe and fetches batched multi-timeframe OHLCV via `yfinance`. `indicators.py` computes EMA/RSI/MACD/BBands/ATR/ADX/volume via `ta` and `stockstats` and derives a 3-timeframe alignment score. `filter.py` applies the hard reject rules from PLAN.md §6 then scores and ranks survivors. `display.py` renders a `rich` scan animation (auto-disabled under `CI`). `main.py` wires guards (weekday, market-holiday) and orchestrates the pipeline for `--dry-run`.

**Tech Stack:** Python 3.11 (`py -3.11`), `yfinance`, `pandas` 3.0.5, `ta`, `stockstats`, `rich`, `pytz`.

## Global Constraints

- Invoke Python as `py -3.11`, never `python` (system default is 3.14).
- Do not import `pandas-ta`, `pyfolio`, or `TA-Lib`.
- Every tunable number lives in `config.py`. No magic numbers elsewhere.
- No Claude API calls, no news, no Telegram, no journal in this phase.
- `.gitignore` (already created) must keep `.env`, `journal.json`, `__pycache__/`, `*.pyc`, `logs/`, `.venv/` out of git.
- `MAX_SURVIVORS` hard cap is 30; excess survivors are dropped, not silently kept.

---

### Task 1: Smoke-test `ta` and `stockstats` against pandas 3.0.5

**Files:**
- Create: `tests/smoke_test_indicators.py`

**Interfaces:**
- Produces: confirmation that `ta.add_all_ta_features` (or targeted `ta` indicator classes) and `stockstats.StockDataFrame` both run on a pandas 3.0.5 DataFrame without raising. This determines whether Task 4 (`indicators.py`) may depend on both libraries or must fall back to `finta`.

- [ ] **Step 1: Write the throwaway smoke test**

```python
# tests/smoke_test_indicators.py
"""Throwaway script: confirm ta and stockstats work against pandas 3.0.5 + yfinance output."""
import sys
import yfinance as yf

def main():
    df = yf.download("AAPL", period="60d", interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        print("FAIL: yfinance returned no data")
        sys.exit(1)
    # yfinance 0.2.x returns MultiIndex columns for single-ticker download in some versions
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = df.columns.get_level_values(0)

    ta_ok = True
    try:
        import ta
        rsi = ta.momentum.RSIIndicator(close=df["Close"], window=14).rsi()
        macd = ta.trend.MACD(close=df["Close"]).macd()
        print("ta OK — last RSI:", rsi.iloc[-1], "last MACD:", macd.iloc[-1])
    except Exception as e:
        ta_ok = False
        print("ta FAILED:", repr(e))

    stockstats_ok = True
    try:
        from stockstats import StockDataFrame
        sdf = StockDataFrame.retype(df.copy())
        rsi_ss = sdf["rsi_14"]
        macd_ss = sdf["macd"]
        print("stockstats OK — last RSI:", rsi_ss.iloc[-1], "last MACD:", macd_ss.iloc[-1])
    except Exception as e:
        stockstats_ok = False
        print("stockstats FAILED:", repr(e))

    print(f"\nSUMMARY: ta={'OK' if ta_ok else 'FAIL'} stockstats={'OK' if stockstats_ok else 'FAIL'}")
    if not (ta_ok and stockstats_ok):
        sys.exit(1)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `py -3.11 tests/smoke_test_indicators.py`
Expected: `SUMMARY: ta=OK stockstats=OK` and exit code 0.

If either prints `FAILED`, STOP and report back before continuing — do not silently switch to `finta` without telling the user (per PLAN.md §1).

- [ ] **Step 3: Commit**

```bash
git add tests/smoke_test_indicators.py
git commit -m "test: smoke-test ta and stockstats against pandas 3.0.5"
```

---

### Task 2: `config.py`

**Files:**
- Create: `config.py`

**Interfaces:**
- Produces: module-level constants consumed by every other file — `SP500_ENABLED`, `NASDAQ100_ENABLED`, `FUTURES`, `EXTRA`, `UNIVERSE_CAP`, `MIN_AVG_VOLUME`, `MIN_PRICE`, `MAX_SURVIVORS`, `MIN_ATR_PCT`, `MAX_ALERTS`, `MIN_CONVICTION`, `MIN_RR`, `MODEL`, `MODEL_CHEAP`, `BATCH_SIZE`, `MAX_TOKENS`, `SCRATCH_AFTER_BARS`, `STATS_EVERY`, `PAPER_MODE`, `MIN_PAPER_SESSIONS`, `SCAN_ANIMATION`, `TICKER_DELAY`, plus Phase-1-only additions: `TIMEFRAMES`, `MARKET_TZ`, `MARKET_OPEN`, `MARKET_CLOSE`, `SCAN_TIMES_PT`, `SCAN_TOLERANCE_MINUTES`.

- [ ] **Step 1: Write `config.py`**

```python
# config.py
# Every tunable value lives here. No magic numbers anywhere else in the codebase.

import datetime

# -- Universe -----------------------------------------------------------
SP500_ENABLED = True
NASDAQ100_ENABLED = True
FUTURES = ["NQ=F", "ES=F", "YM=F", "RTY=F"]
EXTRA: list[str] = []          # manual adds
UNIVERSE_CAP = 500              # hard ceiling after dedupe

# -- Timeframes (Phase 1: data + indicators) -----------------------------
# yfinance interval strings and how much history to pull for each.
TIMEFRAMES = {
    "30m": {"interval": "30m", "period": "60d"},
    "4h":  {"interval": "1h",  "period": "180d"},   # yfinance has no native 4h; resampled from 1h
    "daily": {"interval": "1d", "period": "400d"},
}

# -- Filter thresholds (tune these, not the code) ------------------------
MIN_AVG_VOLUME = 1_000_000      # liquidity floor
MIN_PRICE = 5.00                # no penny stocks
MAX_SURVIVORS = 30              # hard cap into Claude
MIN_ATR_PCT = 0.5               # skip dead, non-moving names

# -- Alerting -------------------------------------------------------------
MAX_ALERTS = 8
MIN_CONVICTION = 6              # out of 10
MIN_RR = 1.5

# -- Claude ----------------------------------------------------------------
MODEL = "claude-sonnet-5"                  # verify current string in console
MODEL_CHEAP = "claude-haiku-4-5-20251001"  # fallback if cost climbs
BATCH_SIZE = 10                  # symbols per API call
MAX_TOKENS = 4000

# -- Journal ----------------------------------------------------------------
SCRATCH_AFTER_BARS = 12         # bars open with no hit -> scratch
STATS_EVERY = 30                # resolved alerts per review
PAPER_MODE = True               # flip only after 30 reviewed sessions
MIN_PAPER_SESSIONS = 30

# -- Display -----------------------------------------------------------------
SCAN_ANIMATION = True           # auto-disabled when CI env var present
TICKER_DELAY = 0.2              # seconds per ticker

# -- Schedule / market guards --------------------------------------------
MARKET_TZ = "America/Vancouver"
MARKET_OPEN = datetime.time(6, 30)
MARKET_CLOSE = datetime.time(13, 0)
# (scan label -> nominal PT time)
SCAN_TIMES_PT = {
    "premarket": datetime.time(6, 0),
    "midsession": datetime.time(10, 30),
    "preclose": datetime.time(12, 30),
}
SCAN_TOLERANCE_MINUTES = 20
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `py -3.11 -c "import config; print(config.MAX_SURVIVORS, config.FUTURES)"`
Expected: `30 ['NQ=F', 'ES=F', 'YM=F', 'RTY=F']`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add config.py with all Phase 1 tunables"
```

---

### Task 3: `data.py` — universe builder + batched multi-timeframe OHLCV fetch

**Files:**
- Create: `data.py`
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: `config.SP500_ENABLED`, `config.NASDAQ100_ENABLED`, `config.FUTURES`, `config.EXTRA`, `config.UNIVERSE_CAP`, `config.TIMEFRAMES`.
- Produces:
  - `build_universe() -> list[str]` — deduped, capped ticker list.
  - `fetch_ohlcv(symbols: list[str], timeframe: str) -> dict[str, pandas.DataFrame]` — one entry per symbol that returned data; symbols with no data are simply absent from the dict (logged, not raised).
  - `fetch_all_timeframes(symbols: list[str]) -> dict[str, dict[str, pandas.DataFrame]]` — `{symbol: {"30m": df, "4h": df, "daily": df}}`, only symbols with data on **all three** timeframes included.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data.py
import pandas as pd
from data import build_universe, fetch_ohlcv, fetch_all_timeframes

def test_build_universe_dedupes_and_caps(monkeypatch):
    import config
    monkeypatch.setattr(config, "SP500_ENABLED", False)
    monkeypatch.setattr(config, "NASDAQ100_ENABLED", False)
    monkeypatch.setattr(config, "FUTURES", ["NQ=F", "ES=F"])
    monkeypatch.setattr(config, "EXTRA", ["ES=F", "AAPL"])   # ES=F duplicated on purpose
    monkeypatch.setattr(config, "UNIVERSE_CAP", 500)
    universe = build_universe()
    assert universe.count("ES=F") == 1
    assert "AAPL" in universe
    assert "NQ=F" in universe

def test_fetch_ohlcv_single_symbol_daily():
    result = fetch_ohlcv(["AAPL"], "daily")
    assert "AAPL" in result
    df = result["AAPL"]
    assert isinstance(df, pd.DataFrame)
    assert {"Open", "High", "Low", "Close", "Volume"}.issubset(df.columns)
    assert len(df) > 50

def test_fetch_ohlcv_skips_bad_symbol():
    result = fetch_ohlcv(["AAPL", "THIS_IS_NOT_A_REAL_TICKER_XYZ"], "daily")
    assert "AAPL" in result
    assert "THIS_IS_NOT_A_REAL_TICKER_XYZ" not in result

def test_fetch_all_timeframes_shape():
    result = fetch_all_timeframes(["AAPL"])
    assert "AAPL" in result
    assert set(result["AAPL"].keys()) == {"30m", "4h", "daily"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/test_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'data'` (or ImportError).

- [ ] **Step 3: Write `data.py`**

```python
# data.py
"""Universe construction and batched multi-timeframe OHLCV fetching."""

import logging

import pandas as pd
import yfinance as yf

import config

logger = logging.getLogger(__name__)


def _wiki_tickers(url: str, table_index: int, symbol_col: str) -> list[str]:
    try:
        tables = pd.read_html(url)
        col = tables[table_index][symbol_col]
        return [str(s).replace(".", "-").strip() for s in col.tolist()]
    except Exception as e:
        logger.warning("Failed to fetch tickers from %s: %r", url, e)
        return []


def _sp500_tickers() -> list[str]:
    return _wiki_tickers(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 0, "Symbol"
    )


def _nasdaq100_tickers() -> list[str]:
    return _wiki_tickers(
        "https://en.wikipedia.org/wiki/Nasdaq-100", 4, "Ticker"
    )


def build_universe() -> list[str]:
    """Combine enabled index lists + futures + manual extras, dedupe, cap."""
    symbols: list[str] = []
    if config.SP500_ENABLED:
        symbols.extend(_sp500_tickers())
    if config.NASDAQ100_ENABLED:
        symbols.extend(_nasdaq100_tickers())
    symbols.extend(config.FUTURES)
    symbols.extend(config.EXTRA)

    deduped = list(dict.fromkeys(symbols))  # preserves order, dedupes
    return deduped[: config.UNIVERSE_CAP]


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def fetch_ohlcv(symbols: list[str], timeframe: str) -> dict[str, pd.DataFrame]:
    """Batched OHLCV fetch for one timeframe. Symbols with no usable data are omitted."""
    tf = config.TIMEFRAMES[timeframe]
    result: dict[str, pd.DataFrame] = {}
    if not symbols:
        return result

    raw = yf.download(
        tickers=symbols,
        period=tf["period"],
        interval=tf["interval"],
        group_by="ticker",
        progress=False,
        auto_adjust=True,
        threads=True,
    )

    if len(symbols) == 1:
        df = _flatten_columns(raw)
        if not df.empty and not df["Close"].isna().all():
            result[symbols[0]] = df.dropna(how="all")
        return result

    for symbol in symbols:
        try:
            df = raw[symbol]
        except (KeyError, IndexError):
            continue
        df = df.dropna(how="all")
        if df.empty or "Close" not in df.columns or df["Close"].isna().all():
            continue
        result[symbol] = df

    return result


def fetch_all_timeframes(symbols: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    """Fetch 30m/4h/daily for all symbols; keep only symbols present on all three."""
    per_tf = {tf: fetch_ohlcv(symbols, tf) for tf in config.TIMEFRAMES}

    result: dict[str, dict[str, pd.DataFrame]] = {}
    for symbol in symbols:
        frames = {}
        for tf in config.TIMEFRAMES:
            if symbol in per_tf[tf]:
                frames[tf] = per_tf[tf][symbol]
        if set(frames.keys()) == set(config.TIMEFRAMES.keys()):
            result[symbol] = frames
        else:
            missing = set(config.TIMEFRAMES.keys()) - set(frames.keys())
            logger.info("Dropping %s: missing timeframes %s", symbol, missing)

    return result
```

Note: `config.TIMEFRAMES["4h"]` uses `interval: "1h"` because yfinance has no native `4h` bar; `indicators.py` (Task 4) is responsible for resampling 1h → 4h before computing indicators.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/test_data.py -v`
Expected: PASS (4 passed). This test hits the real network (yfinance + Wikipedia) — if it fails on a transient network error, rerun once before treating it as a real failure.

- [ ] **Step 5: Commit**

```bash
git add data.py tests/test_data.py
git commit -m "feat: add universe builder and batched OHLCV fetch"
```

---

### Task 4: `indicators.py` — indicator computation + timeframe alignment score

**Files:**
- Create: `indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: `pandas.DataFrame` with `Open/High/Low/Close/Volume` columns, as produced by `data.fetch_all_timeframes`.
- Produces:
  - `resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame` — OHLCV resampled to 4-hour bars.
  - `compute_indicators(df: pd.DataFrame) -> dict` — single-timeframe snapshot: `{"ema9": float, "ema21": float, "ema50": float, "ema200": float, "rsi14": float, "macd_hist": float, "bb_upper": float, "bb_lower": float, "atr14": float, "atr_pct": float, "adx14": float, "vol_ratio": float, "range_high_20d": float, "range_low_20d": float, "close": float}` (last-bar values).
  - `classify_trend(snapshot: dict) -> str` — one of `"bullish"`, `"bearish"`, `"neutral"`, per PLAN.md §5 rule: bullish = `close > ema21 and ema9 > ema21 and macd_hist > 0`; bearish = the inverse (`close < ema21 and ema9 < ema21 and macd_hist < 0`); else neutral.
  - `analyze_symbol(frames: dict[str, pd.DataFrame]) -> dict` — takes `{"30m": df, "4h": df, "daily": df}`, returns `{"snapshots": {tf: dict}, "trends": {tf: str}, "alignment": int}` where `alignment` is the count of timeframes agreeing on a single non-neutral direction (0-3), and 0 if there's no single direction all agreeing timeframes share (e.g. one bullish + one bearish + one neutral = 0, per "count of timeframes agreeing").

- [ ] **Step 1: Write the failing test**

```python
# tests/test_indicators.py
import numpy as np
import pandas as pd
from indicators import resample_to_4h, compute_indicators, classify_trend, analyze_symbol


def _make_trending_df(n=300, start=100.0, step=0.3, freq="1h"):
    idx = pd.date_range("2025-01-01", periods=n, freq=freq)
    close = start + np.arange(n) * step
    high = close + 0.5
    low = close - 0.5
    open_ = close - 0.1
    volume = np.full(n, 2_000_000.0)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def test_resample_to_4h_reduces_row_count():
    df_1h = _make_trending_df(n=100, freq="1h")
    df_4h = resample_to_4h(df_1h)
    assert len(df_4h) < len(df_1h)
    assert {"Open", "High", "Low", "Close", "Volume"}.issubset(df_4h.columns)


def test_compute_indicators_returns_expected_keys():
    df = _make_trending_df(n=300, freq="1d")
    snap = compute_indicators(df)
    for key in (
        "ema9", "ema21", "ema50", "ema200", "rsi14", "macd_hist",
        "bb_upper", "bb_lower", "atr14", "atr_pct", "adx14",
        "vol_ratio", "range_high_20d", "range_low_20d", "close",
    ):
        assert key in snap
        assert snap[key] is not None
        assert not (isinstance(snap[key], float) and np.isnan(snap[key]))


def test_classify_trend_strong_uptrend_is_bullish():
    df = _make_trending_df(n=300, step=0.5, freq="1d")   # steady strong uptrend
    snap = compute_indicators(df)
    assert classify_trend(snap) == "bullish"


def test_classify_trend_strong_downtrend_is_bearish():
    df = _make_trending_df(n=300, step=-0.5, freq="1d")
    snap = compute_indicators(df)
    assert classify_trend(snap) == "bearish"


def test_analyze_symbol_full_alignment():
    frames = {
        "30m": _make_trending_df(n=300, step=0.3, freq="1h"),
        "4h": _make_trending_df(n=300, step=0.3, freq="1h"),
        "daily": _make_trending_df(n=300, step=0.5, freq="1d"),
    }
    result = analyze_symbol(frames)
    assert set(result["snapshots"].keys()) == {"30m", "4h", "daily"}
    assert set(result["trends"].keys()) == {"30m", "4h", "daily"}
    assert result["trends"]["daily"] == "bullish"
    assert result["alignment"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/test_indicators.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'indicators'`.

- [ ] **Step 3: Write `indicators.py`**

```python
# indicators.py
"""Indicator computation (ta + stockstats) and timeframe alignment scoring."""

import numpy as np
import pandas as pd
import ta as ta_lib
from stockstats import StockDataFrame


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """yfinance has no native 4h interval; build it from 1h bars."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = df_1h.resample("4h").agg(agg).dropna(how="any")
    return out


def _last(series: pd.Series) -> float:
    val = series.iloc[-1]
    return float(val) if pd.notna(val) else float("nan")


def compute_indicators(df: pd.DataFrame) -> dict:
    """Last-bar snapshot of every indicator PLAN.md §5 requires, for one timeframe."""
    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

    ema9 = ta_lib.trend.EMAIndicator(close, window=9).ema_indicator()
    ema21 = ta_lib.trend.EMAIndicator(close, window=21).ema_indicator()
    ema50 = ta_lib.trend.EMAIndicator(close, window=50).ema_indicator()
    ema200 = ta_lib.trend.EMAIndicator(close, window=200).ema_indicator()
    rsi14 = ta_lib.momentum.RSIIndicator(close, window=14).rsi()
    macd = ta_lib.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_hist = macd.macd_diff()
    bb = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    atr = ta_lib.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    adx = ta_lib.trend.ADXIndicator(high, low, close, window=14).adx()

    vol_avg20 = volume.rolling(20).mean()
    range_high_20d = high.rolling(20).max()
    range_low_20d = low.rolling(20).min()

    last_close = _last(close)
    last_atr = _last(atr)
    last_vol = _last(volume)
    last_vol_avg = _last(vol_avg20)

    return {
        "ema9": _last(ema9),
        "ema21": _last(ema21),
        "ema50": _last(ema50),
        "ema200": _last(ema200),
        "rsi14": _last(rsi14),
        "macd_hist": _last(macd_hist),
        "bb_upper": _last(bb_upper),
        "bb_lower": _last(bb_lower),
        "atr14": last_atr,
        "atr_pct": (last_atr / last_close * 100.0) if last_close else float("nan"),
        "adx14": _last(adx),
        "vol_ratio": (last_vol / last_vol_avg) if last_vol_avg else float("nan"),
        "range_high_20d": _last(range_high_20d),
        "range_low_20d": _last(range_low_20d),
        "close": last_close,
    }


def classify_trend(snapshot: dict) -> str:
    """PLAN.md §5: bullish = price > EMA21 AND EMA9 > EMA21 AND MACD hist > 0. Bearish = inverse."""
    close, ema9, ema21, macd_hist = (
        snapshot["close"], snapshot["ema9"], snapshot["ema21"], snapshot["macd_hist"],
    )
    if any(np.isnan(v) for v in (close, ema9, ema21, macd_hist)):
        return "neutral"
    if close > ema21 and ema9 > ema21 and macd_hist > 0:
        return "bullish"
    if close < ema21 and ema9 < ema21 and macd_hist < 0:
        return "bearish"
    return "neutral"


def analyze_symbol(frames: dict) -> dict:
    """frames = {'30m': df, '4h': df, 'daily': df}. '4h' may be raw 1h; resample first if so."""
    snapshots = {}
    trends = {}
    for tf, df in frames.items():
        working = df
        if tf == "4h" and len(df) > 0:
            # Accept either pre-resampled 4h bars or raw 1h bars.
            inferred_freq = df.index.to_series().diff().median()
            if inferred_freq is not None and inferred_freq < pd.Timedelta(hours=2):
                working = resample_to_4h(df)
        snap = compute_indicators(working)
        snapshots[tf] = snap
        trends[tf] = classify_trend(snap)

    directions = [t for t in trends.values() if t != "neutral"]
    if directions and len(set(directions)) == 1:
        alignment = len(directions)
    else:
        alignment = 0

    return {"snapshots": snapshots, "trends": trends, "alignment": alignment}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/test_indicators.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add indicators.py tests/test_indicators.py
git commit -m "feat: add indicator computation and timeframe alignment scoring"
```

---

### Task 5: `filter.py` — hard filter + survivor scoring

**Files:**
- Create: `filter.py`
- Test: `tests/test_filter.py`

**Interfaces:**
- Consumes: `config.MIN_AVG_VOLUME`, `config.MIN_PRICE`, `config.MIN_ATR_PCT`, `config.MAX_SURVIVORS`; per-symbol data shaped like `indicators.analyze_symbol()`'s return plus a `avg_volume` and `price` figure (computed here from the daily frame).
- Produces:
  - `passes_hard_filter(symbol: str, frames: dict, analysis: dict) -> tuple[bool, str]` — `(True, "")` or `(False, reason)`.
  - `score_survivor(analysis: dict) -> float` — higher is better; combines alignment strength, volume surge, range-edge distance, ADX, BB squeeze/expansion.
  - `run_filter(universe_frames: dict[str, dict]) -> tuple[list[dict], list[dict]]` — `(survivors, filtered_out)`. Each entry is `{"symbol": str, "analysis": dict, "score": float, "reason": str}` (`reason` empty for survivors). `survivors` is sorted by `score` descending and capped at `config.MAX_SURVIVORS`; everything else lands in `filtered_out` with its rejection or "excess" reason.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_filter.py
import numpy as np
import pandas as pd
from filter import passes_hard_filter, score_survivor, run_filter
from indicators import analyze_symbol


def _df(n, start, step, freq, volume=2_000_000.0):
    idx = pd.date_range("2025-01-01", periods=n, freq=freq)
    close = start + np.arange(n) * step
    high, low, open_ = close + 0.5, close - 0.5, close - 0.1
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close,
         "Volume": np.full(n, volume)},
        index=idx,
    )


def _good_frames():
    return {
        "30m": _df(300, 100.0, 0.3, "1h"),
        "4h": _df(300, 100.0, 0.3, "1h"),
        "daily": _df(300, 100.0, 0.5, "1d"),
    }


def test_passes_hard_filter_accepts_liquid_moving_aligned_symbol(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_AVG_VOLUME", 1_000_000)
    monkeypatch.setattr(config, "MIN_PRICE", 5.0)
    monkeypatch.setattr(config, "MIN_ATR_PCT", 0.1)
    frames = _good_frames()
    analysis = analyze_symbol(frames)
    ok, reason = passes_hard_filter("TEST", frames, analysis)
    assert ok, reason


def test_passes_hard_filter_rejects_low_volume(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_AVG_VOLUME", 5_000_000)
    frames = {
        "30m": _df(300, 100.0, 0.3, "1h", volume=1000),
        "4h": _df(300, 100.0, 0.3, "1h", volume=1000),
        "daily": _df(300, 100.0, 0.5, "1d", volume=1000),
    }
    analysis = analyze_symbol(frames)
    ok, reason = passes_hard_filter("LOWVOL", frames, analysis)
    assert not ok
    assert "volume" in reason.lower()


def test_passes_hard_filter_rejects_penny_stock(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_PRICE", 5.0)
    frames = {
        "30m": _df(300, 1.0, 0.01, "1h"),
        "4h": _df(300, 1.0, 0.01, "1h"),
        "daily": _df(300, 1.0, 0.01, "1d"),
    }
    analysis = analyze_symbol(frames)
    ok, reason = passes_hard_filter("PENNY", frames, analysis)
    assert not ok
    assert "price" in reason.lower()


def test_passes_hard_filter_rejects_low_alignment():
    flat = _df(300, 100.0, 0.0, "1h")
    flat_daily = _df(300, 100.0, 0.0, "1d")
    frames = {"30m": flat, "4h": flat, "daily": flat_daily}
    analysis = analyze_symbol(frames)
    ok, reason = passes_hard_filter("FLAT", frames, analysis)
    assert not ok
    assert "alignment" in reason.lower()


def test_run_filter_caps_at_max_survivors(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_AVG_VOLUME", 1_000_000)
    monkeypatch.setattr(config, "MIN_PRICE", 5.0)
    monkeypatch.setattr(config, "MIN_ATR_PCT", 0.1)
    monkeypatch.setattr(config, "MAX_SURVIVORS", 2)

    universe_frames = {f"SYM{i}": _good_frames() for i in range(5)}
    survivors, filtered_out = run_filter(universe_frames)
    assert len(survivors) == 2
    assert len(filtered_out) == 3
    assert all(f["reason"] == "excess" for f in filtered_out)
    scores = [s["score"] for s in survivors]
    assert scores == sorted(scores, reverse=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/test_filter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'filter'`.

- [ ] **Step 3: Write `filter.py`**

```python
# filter.py
"""Hard reject rules + survivor scoring. PLAN.md §6. Pure math, zero API cost."""

import numpy as np

import config


def _avg_volume_and_price(frames: dict) -> tuple[float, float]:
    daily = frames["daily"]
    avg_volume = float(daily["Volume"].tail(20).mean())
    price = float(daily["Close"].iloc[-1])
    return avg_volume, price


def passes_hard_filter(symbol: str, frames: dict, analysis: dict) -> tuple[bool, str]:
    daily_snap = analysis["snapshots"].get("daily")
    if daily_snap is None or any(np.isnan(v) for v in daily_snap.values() if isinstance(v, float)):
        return False, "missing or malformed data"

    avg_volume, price = _avg_volume_and_price(frames)

    if avg_volume < config.MIN_AVG_VOLUME:
        return False, f"avg volume {avg_volume:,.0f} below MIN_AVG_VOLUME {config.MIN_AVG_VOLUME:,}"
    if price < config.MIN_PRICE:
        return False, f"price {price:.2f} below MIN_PRICE {config.MIN_PRICE}"
    if daily_snap["atr_pct"] < config.MIN_ATR_PCT:
        return False, f"ATR% {daily_snap['atr_pct']:.2f} below MIN_ATR_PCT {config.MIN_ATR_PCT}"
    if analysis["alignment"] <= 1:
        return False, f"alignment score {analysis['alignment']} <= 1"

    return True, ""


def score_survivor(analysis: dict) -> float:
    """Higher = more interesting. Combines alignment, volume surge, range position, ADX, BB state."""
    daily = analysis["snapshots"]["daily"]

    alignment_score = analysis["alignment"] * 10.0

    vol_ratio = daily["vol_ratio"] if not np.isnan(daily["vol_ratio"]) else 1.0
    volume_score = min(vol_ratio, 5.0) * 5.0

    rng = daily["range_high_20d"] - daily["range_low_20d"]
    if rng > 0:
        dist_from_high = (daily["range_high_20d"] - daily["close"]) / rng
        dist_from_low = (daily["close"] - daily["range_low_20d"]) / rng
        range_edge_score = (1.0 - min(dist_from_high, dist_from_low)) * 10.0
    else:
        range_edge_score = 0.0

    adx = daily["adx14"] if not np.isnan(daily["adx14"]) else 0.0
    adx_score = min(adx, 50.0) / 5.0

    bb_width = (daily["bb_upper"] - daily["bb_lower"]) / daily["close"] if daily["close"] else 0.0
    squeeze_score = max(0.0, 5.0 - bb_width * 100.0)

    return alignment_score + volume_score + range_edge_score + adx_score + squeeze_score


def run_filter(universe_frames: dict) -> tuple[list[dict], list[dict]]:
    """universe_frames: {symbol: {'30m': df, '4h': df, 'daily': df}}."""
    from indicators import analyze_symbol

    candidates = []
    filtered_out = []

    for symbol, frames in universe_frames.items():
        analysis = analyze_symbol(frames)
        ok, reason = passes_hard_filter(symbol, frames, analysis)
        if not ok:
            filtered_out.append({"symbol": symbol, "analysis": analysis, "score": 0.0, "reason": reason})
            continue
        score = score_survivor(analysis)
        candidates.append({"symbol": symbol, "analysis": analysis, "score": score, "reason": ""})

    candidates.sort(key=lambda c: c["score"], reverse=True)
    survivors = candidates[: config.MAX_SURVIVORS]
    excess = candidates[config.MAX_SURVIVORS :]
    for c in excess:
        c["reason"] = "excess"
    filtered_out.extend(excess)

    return survivors, filtered_out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/test_filter.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add filter.py tests/test_filter.py
git commit -m "feat: add hard filter and survivor scoring"
```

---

### Task 6: `display.py` — terminal scan animation

**Files:**
- Create: `display.py`
- Test: `tests/test_display.py`

**Interfaces:**
- Consumes: `config.SCAN_ANIMATION`, `config.TICKER_DELAY`; iterable of `{"symbol": str, "passed": bool, "reason": str, "alignment": int}` produced by wiring `filter.run_filter` output in `main.py`.
- Produces:
  - `animation_enabled() -> bool` — `config.SCAN_ANIMATION and os.getenv("CI") is None`.
  - `flash_ticker(symbol: str, passed: bool, reason: str, alignment: int) -> None` — prints one line, sleeps `config.TICKER_DELAY` seconds only if `animation_enabled()`.
  - `print_survivor_table(survivors: list[dict]) -> None` — `rich.table.Table` of symbol/score/alignment/close/reason columns.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_display.py
import os
from display import animation_enabled, flash_ticker, print_survivor_table


def test_animation_enabled_respects_ci_env(monkeypatch):
    import config
    monkeypatch.setattr(config, "SCAN_ANIMATION", True)
    monkeypatch.delenv("CI", raising=False)
    assert animation_enabled() is True
    monkeypatch.setenv("CI", "true")
    assert animation_enabled() is False


def test_animation_enabled_respects_config_flag(monkeypatch):
    import config
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(config, "SCAN_ANIMATION", False)
    assert animation_enabled() is False


def test_flash_ticker_does_not_raise(monkeypatch):
    import config
    monkeypatch.setattr(config, "SCAN_ANIMATION", False)   # skip real sleep in tests
    flash_ticker("AAPL", True, "", 3)
    flash_ticker("XYZ", False, "low ATR", 0)


def test_print_survivor_table_does_not_raise():
    survivors = [
        {"symbol": "NVDA", "score": 42.5, "analysis": {"alignment": 3, "snapshots": {"daily": {"close": 950.2}}}, "reason": ""},
    ]
    print_survivor_table(survivors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/test_display.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'display'`.

- [ ] **Step 3: Write `display.py`**

```python
# display.py
"""rich-based terminal scan animation. Auto-disabled under CI (GitHub Actions sets CI=true)."""

import os
import time

from rich.console import Console
from rich.table import Table

import config

console = Console()


def animation_enabled() -> bool:
    return bool(config.SCAN_ANIMATION) and os.getenv("CI") is None


def flash_ticker(symbol: str, passed: bool, reason: str, alignment: int) -> None:
    status = "[green]passed[/green]" if passed else "[red]filtered[/red]"
    mark = "✅" if passed else "✗"
    label = "" if passed else f"  {reason}"
    console.print(f"  scanning ▸ {symbol:<8} {mark} {status}   align {alignment}/3{label}")
    if animation_enabled():
        time.sleep(config.TICKER_DELAY)


def print_survivor_table(survivors: list[dict]) -> None:
    table = Table(title="Survivors")
    table.add_column("Symbol")
    table.add_column("Score", justify="right")
    table.add_column("Alignment", justify="right")
    table.add_column("Close", justify="right")

    for s in survivors:
        close = s["analysis"]["snapshots"]["daily"]["close"]
        table.add_row(
            s["symbol"],
            f"{s['score']:.1f}",
            f"{s['analysis']['alignment']}/3",
            f"{close:.2f}",
        )

    console.print(table)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/test_display.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add display.py tests/test_display.py
git commit -m "feat: add rich terminal scan animation, CI auto-disable"
```

---

### Task 7: `main.py` — guards + orchestration + `--dry-run`

**Files:**
- Create: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `data.build_universe`, `data.fetch_all_timeframes`, `filter.run_filter`, `display.flash_ticker`, `display.print_survivor_table`, `config.MARKET_TZ`.
- Produces:
  - `is_weekday(now) -> bool`.
  - `market_open_today(now) -> bool` — checks SPY's most recent daily bar date against `now`'s date (both in `MARKET_TZ`); network call, wrapped so a failure defaults to "assume open" with a logged warning (fail open, since Phase 1 has no real capital at risk and a false skip silently produces no survivor table).
  - `run_scan(dry_run: bool = False) -> list[dict]` — full pipeline; returns the survivor list. Guards (`is_weekday`, `market_open_today`) are skipped when `dry_run=True` so the pipeline can be exercised on weekends/holidays during development.
  - CLI entry: `py -3.11 main.py --dry-run`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main.py
import datetime
import pytz
from main import is_weekday


def test_is_weekday_true_for_wednesday():
    tz = pytz.timezone("America/Vancouver")
    wed = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))   # a Wednesday
    assert is_weekday(wed) is True


def test_is_weekday_false_for_saturday():
    tz = pytz.timezone("America/Vancouver")
    sat = tz.localize(datetime.datetime(2026, 7, 25, 6, 0))   # a Saturday
    assert is_weekday(sat) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 3: Write `main.py`**

```python
# main.py
"""Phase 1 entry point: guards + scan pipeline orchestration."""

import argparse
import datetime
import logging
import sys

import pytz

import config
import data
import display
import filter as filter_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def is_weekday(now: datetime.datetime) -> bool:
    return now.weekday() < 5  # Mon=0 ... Sun=6


def market_open_today(now: datetime.datetime) -> bool:
    """Fail-open: a network/data error assumes the market is open rather than silently skipping."""
    try:
        spy = data.fetch_ohlcv(["SPY"], "daily")
        if "SPY" not in spy or spy["SPY"].empty:
            logger.warning("Could not fetch SPY to confirm market is open; assuming open.")
            return True
        last_bar_date = spy["SPY"].index[-1].date()
        return last_bar_date == now.date()
    except Exception as e:
        logger.warning("market_open_today check failed (%r); assuming open.", e)
        return True


def run_scan(dry_run: bool = False) -> list[dict]:
    tz = pytz.timezone(config.MARKET_TZ)
    now = datetime.datetime.now(tz)

    if not dry_run:
        if not is_weekday(now):
            logger.info("Not a weekday (%s); exiting.", now.date())
            return []
        if not market_open_today(now):
            logger.info("Market not open today (%s); exiting.", now.date())
            return []

    logger.info("Building universe...")
    universe = data.build_universe()
    logger.info("Universe size: %d", len(universe))

    logger.info("Fetching multi-timeframe OHLCV (this may take a while for the full universe)...")
    universe_frames = data.fetch_all_timeframes(universe)
    logger.info("Symbols with complete data: %d", len(universe_frames))

    survivors, filtered_out = filter_mod.run_filter(universe_frames)

    for entry in survivors:
        display.flash_ticker(entry["symbol"], True, "", entry["analysis"]["alignment"])
    for entry in filtered_out:
        if entry["reason"] != "excess":
            display.flash_ticker(entry["symbol"], False, entry["reason"], entry["analysis"]["alignment"])

    display.print_survivor_table(survivors)
    logger.info(
        "Scanned %d -> data OK %d -> survivors %d",
        len(universe), len(universe_frames), len(survivors),
    )
    return survivors


def main() -> int:
    parser = argparse.ArgumentParser(description="Trade scanner Phase 1 pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip weekday/market-open guards")
    args = parser.parse_args()

    run_scan(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/test_main.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full pipeline smoke run**

Run: `py -3.11 main.py --dry-run`
Expected: universe size logged, ticker flash lines print, a `rich` survivor table renders with ≤30 rows, final "Scanned N -> data OK M -> survivors K" log line. This is the Phase 1 completion checkpoint from PLAN.md §13 — after it runs, pull three survivor tickers up on TradingView and eyeball whether the filter is surfacing interesting charts before building Phase 2 on top of it.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add main.py with guards and --dry-run pipeline orchestration"
```

---

## Self-Review Notes

- **Spec coverage:** Universe (§2/§4) → Task 3. Indicators + alignment (§5) → Task 4. Hard filter + scoring (§6) → Task 5. Display + CI auto-disable (§12) → Task 6. Weekday/market-holiday guards (§3) and `--dry-run` (§13) → Task 7. `ta`/`stockstats` smoke test (§1) → Task 1. `config.py` (§4) → Task 2. News, Claude, journal, digest, notify, GitHub Actions are explicitly out of scope for Phase 1 per PLAN.md §13 and are not included here.
- **Placeholder scan:** no TBD/TODO markers; every step has runnable code.
- **Type consistency:** `frames` dict shape `{"30m": df, "4h": df, "daily": df}` is consistent from `data.fetch_all_timeframes` (Task 3) through `indicators.analyze_symbol` (Task 4) through `filter.run_filter` (Task 5) through `main.run_scan` (Task 7). Survivor dict shape `{"symbol", "analysis", "score", "reason"}` is consistent from `filter.run_filter` through `display.print_survivor_table`/`flash_ticker` through `main.run_scan`.
