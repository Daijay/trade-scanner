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
