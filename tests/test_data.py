# tests/test_data.py
import io
import pandas as pd
import pytest
import requests
import data
from data import build_universe, fetch_ohlcv, fetch_all_timeframes, fetch_market_context, _scrape_tickers, _sp500_tickers, _nasdaq100_tickers


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")


_SP500_HTML = """
<table><tr><th>Symbol</th><th>Name</th></tr>
<tr><td>AAPL</td><td>Apple</td></tr>
<tr><td>BRK.B</td><td>Berkshire</td></tr>
</table>
"""

_NASDAQ100_HTML = """
<table><tr><th>#</th><th>Company</th><th>Symbol</th></tr>
<tr><td>1</td><td>Apple</td><td>AAPL</td></tr>
<tr><td>2</td><td>Class B Co</td><td>GOOG.L</td></tr>
</table>
"""


def test_scrape_tickers_success_sp500_shape(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(_SP500_HTML))
    result = _scrape_tickers("https://example.com/sp500", 0, "Symbol")
    assert result == ["AAPL", "BRK-B"]


def test_scrape_tickers_success_nasdaq100_shape(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(_NASDAQ100_HTML))
    result = _scrape_tickers("https://example.com/nasdaq100", 0, "Symbol")
    assert result == ["AAPL", "GOOG-L"]


def test_scrape_tickers_raises_on_network_error(monkeypatch):
    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("boom")
    monkeypatch.setattr(requests, "get", _raise)
    with pytest.raises(RuntimeError):
        _scrape_tickers("https://example.com/broken", 0, "Symbol")


def test_scrape_tickers_raises_on_missing_column(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(_SP500_HTML))
    with pytest.raises(RuntimeError):
        _scrape_tickers("https://example.com/sp500", 0, "Ticker")  # wrong column name


def test_scrape_tickers_raises_on_table_index_out_of_range(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(_SP500_HTML))
    with pytest.raises(RuntimeError):
        _scrape_tickers("https://example.com/sp500", 5, "Symbol")

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


def _daily_df(closes):
    idx = pd.date_range("2026-07-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [1_000_000] * len(closes)}, index=idx)


def test_fetch_market_context_formats_all_three(monkeypatch):
    def _fake_fetch(symbols, timeframe):
        assert timeframe == "daily"
        return {
            "SPY": _daily_df([500.0, 501.5]),
            "QQQ": _daily_df([400.0, 398.0]),
            "^VIX": _daily_df([14.2]),
        }
    monkeypatch.setattr(data, "fetch_ohlcv", _fake_fetch)
    result = fetch_market_context()
    assert result == "SPY +0.3% | QQQ -0.5% | VIX 14.2"


def test_fetch_market_context_skips_missing_symbol(monkeypatch):
    def _fake_fetch(symbols, timeframe):
        return {"SPY": _daily_df([500.0, 505.0])}  # QQQ and ^VIX omitted, as fetch_ohlcv does for missing data
    monkeypatch.setattr(data, "fetch_ohlcv", _fake_fetch)
    result = fetch_market_context()
    assert result == "SPY +1.0%"


def test_fetch_market_context_returns_empty_on_exception(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(data, "fetch_ohlcv", _raise)
    assert fetch_market_context() == ""


def test_fetch_market_context_returns_empty_when_nothing_available(monkeypatch):
    monkeypatch.setattr(data, "fetch_ohlcv", lambda symbols, timeframe: {})
    assert fetch_market_context() == ""
