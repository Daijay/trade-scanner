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
