# tests/test_indicators.py
import numpy as np
import pandas as pd
from indicators import resample_to_4h, compute_indicators, classify_trend, analyze_symbol, bars_since_flip


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
    assert set(result["bars_since_flip"].keys()) == {"30m", "4h", "daily"}
    assert all(isinstance(v, int) and 0 <= v <= 20 for v in result["bars_since_flip"].values())
    assert result["min_bars_since_flip"] == min(result["bars_since_flip"].values())


def test_bars_since_flip_long_stable_trend_returns_cap():
    df = _make_trending_df(n=300, step=0.5, freq="1d")
    assert bars_since_flip(df) == 20


def test_bars_since_flip_recent_flip_returns_small_count():
    # Long downtrend then a short recent uptrend tail.
    idx = pd.date_range("2025-01-01", periods=300, freq="1d")
    down_part = 200.0 - np.arange(285) * 0.5
    up_part = down_part[-1] + np.arange(15) * 3.0
    close = np.concatenate([down_part, up_part])
    high = close + 0.5
    low = close - 0.5
    open_ = close - 0.1
    volume = np.full(300, 2_000_000.0)
    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )
    n = bars_since_flip(df)
    assert 0 < n < 20
