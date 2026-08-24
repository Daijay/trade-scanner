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

def test_lxml_importable():
    """pd.read_html's default flavor requires lxml (html5lib, the fallback
    flavor's dependency, is not installed in this project) -- this guards
    against a repeat of the 'ModuleNotFoundError: No module named lxml' CI
    failure that surfaced when requirements.txt was missing it."""
    import lxml  # noqa: F401

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


# -- Minimum-bar-count validation ----------------------------------------
# Regression guard for the Aug 21 2026 outage: VMRK (a ticker rename) carried
# 400 daily bars but only 28 1h bars, which resampled to 8 4h bars. ta's
# AverageTrueRange(window=14) indexes position 13 and raised IndexError,
# aborting the entire 499-symbol scan.

def _ohlcv(n, freq, start="2026-08-18 09:30"):
    idx = pd.date_range(start, periods=n, freq=freq, tz="America/New_York")
    return pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1_000_000.0},
        index=idx,
    )


def _patch_frames(monkeypatch, per_symbol):
    """per_symbol: {timeframe: {symbol: df}}."""
    def _fake_fetch(symbols, timeframe):
        return per_symbol[timeframe]
    monkeypatch.setattr(data, "fetch_ohlcv", _fake_fetch)


def test_fetch_all_timeframes_drops_symbol_with_too_few_4h_bars(monkeypatch):
    # 28 1h bars resample to 8 4h bars -- the exact VMRK shape.
    _patch_frames(monkeypatch, {
        "30m": {"VMRK": _ohlcv(52, "30min")},
        "4h": {"VMRK": _ohlcv(28, "1h")},
        "daily": {"VMRK": _ohlcv(400, "1D")},
    })
    assert fetch_all_timeframes(["VMRK"]) == {}


def test_fetch_all_timeframes_keeps_symbol_with_enough_bars(monkeypatch):
    _patch_frames(monkeypatch, {
        "30m": {"OK": _ohlcv(300, "30min")},
        "4h": {"OK": _ohlcv(300, "1h")},
        "daily": {"OK": _ohlcv(300, "1D")},
    })
    result = fetch_all_timeframes(["OK"])
    assert set(result.keys()) == {"OK"}
    assert set(result["OK"].keys()) == {"30m", "4h", "daily"}


def test_fetch_all_timeframes_drops_only_the_short_symbol(monkeypatch):
    _patch_frames(monkeypatch, {
        "30m": {"OK": _ohlcv(300, "30min"), "SHORT": _ohlcv(52, "30min")},
        "4h": {"OK": _ohlcv(300, "1h"), "SHORT": _ohlcv(28, "1h")},
        "daily": {"OK": _ohlcv(300, "1D"), "SHORT": _ohlcv(400, "1D")},
    })
    assert set(fetch_all_timeframes(["OK", "SHORT"]).keys()) == {"OK"}


def test_fetch_all_timeframes_drops_symbol_with_too_few_daily_bars(monkeypatch):
    _patch_frames(monkeypatch, {
        "30m": {"IPO": _ohlcv(300, "30min")},
        "4h": {"IPO": _ohlcv(300, "1h")},
        "daily": {"IPO": _ohlcv(5, "1D")},
    })
    assert fetch_all_timeframes(["IPO"]) == {}
