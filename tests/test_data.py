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
