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
    try:
        adx_last = _last(ta_lib.trend.ADXIndicator(high, low, close, window=14).adx())
    except (IndexError, ValueError):
        # ta's ADXIndicator indexes into position `window` internally and raises
        # IndexError when the dataframe has fewer than window+1 rows (short-history
        # symbols). Treat as insufficient data, consistent with other indicators' NaN.
        adx_last = float("nan")

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
        "adx14": adx_last,
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
