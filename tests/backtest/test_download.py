"""Offline tests for backtest.download. No network: requests/yfinance are faked."""

import io
import json

import pandas as pd
import pytest

from backtest import download


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _bars_30m(start="2026-01-02 09:30", periods=6):
    idx = pd.date_range(start, periods=periods, freq="30min")
    return pd.DataFrame(
        {
            "datetime": idx,
            "Open": range(periods),
            "High": range(periods),
            "Low": range(periods),
            "Close": range(periods),
            "Volume": [100] * periods,
            "source": ["iex"] * periods,
        }
    )


def _parquet_bytes(df):
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


class FakeHFDL:
    """Records every GET so tests can assert on the endpoints actually used."""

    def __init__(self, missing=(), rows=6):
        self.calls = []          # list of (url, params, headers)
        self.missing = set(missing)
        self.rows = rows

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if "/download-token/" in url:
            ticker = url.rsplit("/", 1)[-1]
            if ticker in self.missing:
                return FakeResponse(status_code=404)
            return FakeResponse(
                payload={"url": f"https://signed.example.com/{ticker}?sig=abc"}
            )
        # the signed URL
        ticker = url.rsplit("/", 1)[-1].split("?")[0]
        return FakeResponse(content=_parquet_bytes(_bars_30m(periods=self.rows)))


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(download, "_api_key", lambda: "test-key")


# --------------------------------------------------------------------------
# fetch_hfdl
# --------------------------------------------------------------------------

def test_fetch_hfdl_uses_signed_url_two_step_not_bars_endpoint(monkeypatch, fake_key):
    fake = FakeHFDL()
    monkeypatch.setattr(download.requests, "get", fake.get)

    df, err = download.fetch_hfdl("AAPL")

    assert err is None
    assert len(fake.calls) == 2

    token_call, data_call = fake.calls
    assert token_call["url"].endswith("/download-token/AAPL")
    assert token_call["headers"]["X-API-Key"] == "test-key"
    assert token_call["params"] == {
        "timeframe": "30min", "format": "parquet", "version": "clean"
    }

    # step 2 hits the signed URL with NO auth header
    assert data_call["url"].startswith("https://signed.example.com/")
    assert not data_call["headers"]

    # the full-history endpoint is never touched
    assert all("/bars/" not in c["url"] for c in fake.calls)


def test_fetch_hfdl_normalizes_index(monkeypatch, fake_key):
    monkeypatch.setattr(download.requests, "get", FakeHFDL().get)
    df, err = download.fetch_hfdl("AAPL")
    assert err is None
    assert df.index.name == "timestamp"
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume", "source"]


def test_fetch_hfdl_accepts_alternate_signed_url_keys(monkeypatch, fake_key):
    for key in ("url", "signed_url", "download_url"):
        class Alt(FakeHFDL):
            def get(self, url, params=None, headers=None, timeout=None):
                self.calls.append({"url": url, "params": params, "headers": headers})
                if "/download-token/" in url:
                    return FakeResponse(payload={key: "https://signed.example.com/X"})
                return FakeResponse(content=_parquet_bytes(_bars_30m()))

        monkeypatch.setattr(download.requests, "get", Alt().get)
        df, err = download.fetch_hfdl("AAPL")
        assert err is None, key
        assert df is not None


def test_fetch_hfdl_404_returns_reason_and_does_not_raise(monkeypatch, fake_key):
    monkeypatch.setattr(download.requests, "get", FakeHFDL(missing={"SMCI"}).get)
    df, err = download.fetch_hfdl("SMCI")
    assert df is None
    assert "404" in err


def test_fetch_hfdl_without_key_returns_reason(monkeypatch):
    monkeypatch.setattr(download, "_api_key", lambda: None)
    df, err = download.fetch_hfdl("AAPL")
    assert df is None
    assert "HFDL_API_KEY" in err


# --------------------------------------------------------------------------
# fetch_yf_daily
# --------------------------------------------------------------------------

def _daily(periods=200, start="2026-01-02"):
    idx = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame(
        {
            "Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0,
            "Adj Close": 0.9, "Volume": 1000,
        },
        index=idx,
    )


def test_fetch_yf_daily_requires_auto_adjust_false(monkeypatch):
    captured = {}

    def fake_download(**kwargs):
        captured.update(kwargs)
        frames = {t: _daily() for t in kwargs["tickers"]}
        return pd.concat(frames, axis=1)

    monkeypatch.setattr(download.yf, "download", fake_download)
    out = download.fetch_yf_daily(["AAPL", "MSFT"], "2026-01-01", "2026-08-25")

    assert captured["auto_adjust"] is False
    assert captured["interval"] == "1d"
    assert captured["group_by"] == "ticker"
    assert set(out) == {"AAPL", "MSFT"}
    # Adj Close is dropped: the store holds raw, unadjusted prices only
    assert list(out["AAPL"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert out["AAPL"].index.name == "timestamp"


# --------------------------------------------------------------------------
# download_universe
# --------------------------------------------------------------------------

@pytest.fixture
def patched_universe(monkeypatch):
    """Stub yfinance + build_universe; hfdl is patched per test."""
    def fake_download(**kwargs):
        frames = {t: _daily() for t in kwargs["tickers"]}
        return pd.concat(frames, axis=1)

    monkeypatch.setattr(download.yf, "download", fake_download)
    monkeypatch.setattr(download, "build_universe", lambda: ["AAPL", "SMCI", "NVDA"])


def test_404_on_one_ticker_does_not_abort_the_batch(tmp_path, monkeypatch, fake_key,
                                                    patched_universe):
    monkeypatch.setattr(download.requests, "get", FakeHFDL(missing={"SMCI"}).get)

    result = download.download_universe(
        out_root=tmp_path, start="2026-01-01", end="2026-08-25", sleep_seconds=0
    )

    assert (tmp_path / "ohlcv_30m" / "AAPL.parquet").exists()
    assert (tmp_path / "ohlcv_30m" / "NVDA.parquet").exists()
    assert not (tmp_path / "ohlcv_30m" / "SMCI.parquet").exists()

    failures = [f for f in result["failures"] if f["ticker"] == "SMCI"]
    assert failures and "404" in failures[0]["reason"]

    rows = (tmp_path / "_download_failures.csv").read_text().strip().splitlines()
    assert rows[0] == "ticker,frame,reason"
    assert any(r.startswith("SMCI,30min,") for r in rows[1:])

    # daily still ran for every ticker, including the 30m failure
    for t in ("AAPL", "SMCI", "NVDA"):
        assert (tmp_path / "ohlcv_daily" / f"{t}.parquet").exists()


def test_resume_skips_ticker_whose_file_already_covers_the_range(
    tmp_path, monkeypatch, fake_key, patched_universe
):
    # pre-seed a complete AAPL 30m file
    idx = pd.date_range("2026-01-02 09:30", "2026-08-21 16:00", freq="30min")
    seeded = pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0,
         "Volume": 1, "source": "iex"},
        index=idx,
    )
    seeded.index.name = "timestamp"
    (tmp_path / "ohlcv_30m").mkdir(parents=True)
    seeded.to_parquet(tmp_path / "ohlcv_30m" / "AAPL.parquet")

    fake = FakeHFDL()
    monkeypatch.setattr(download.requests, "get", fake.get)

    download.download_universe(
        out_root=tmp_path, start="2026-01-01", end="2026-08-25", sleep_seconds=0
    )

    requested = {c["url"].rsplit("/", 1)[-1] for c in fake.calls if "/download-token/" in c["url"]}
    assert "AAPL" not in requested, "resume refetched an already-complete ticker"
    assert requested == {"SMCI", "NVDA"}

    # and the seeded file is untouched
    after = pd.read_parquet(tmp_path / "ohlcv_30m" / "AAPL.parquet")
    assert len(after) == len(seeded)


def test_second_run_is_a_full_no_op(tmp_path, monkeypatch, fake_key, patched_universe):
    monkeypatch.setattr(download.requests, "get", FakeHFDL().get)
    download.download_universe(
        out_root=tmp_path, start="2026-01-01", end="2026-08-25", sleep_seconds=0
    )

    fake2 = FakeHFDL()
    monkeypatch.setattr(download.requests, "get", fake2.get)
    calls_yf = []
    monkeypatch.setattr(
        download, "fetch_yf_daily",
        lambda t, s, e: calls_yf.append(t) or {},
    )
    download.download_universe(
        out_root=tmp_path, start="2026-01-01", end="2026-08-25", sleep_seconds=0
    )
    assert fake2.calls == []
    assert calls_yf == []


def test_manifest_records_unadjusted_convention(tmp_path, monkeypatch, fake_key,
                                                patched_universe):
    monkeypatch.setattr(download.requests, "get", FakeHFDL().get)

    download.download_universe(
        out_root=tmp_path, start="2026-01-01", end="2026-08-25", sleep_seconds=0
    )

    manifest = json.loads((tmp_path / "_manifest.json").read_text())
    assert manifest["adjustment"] == "unadjusted"

    entry = manifest["tickers"]["AAPL"]
    for frame, source, tf in (("30min", "hfdatalibrary", "30min"),
                              ("daily", "yfinance", "daily")):
        rec = entry[frame]
        assert rec["adjustment"] == "unadjusted"
        assert rec["source"] == source
        assert rec["timeframe"] == tf
        assert rec["rows"] > 0
        assert rec["first_bar"] and rec["last_bar"]
        assert rec["downloaded_at"]


def test_manifest_and_failures_written_even_when_everything_fails(
    tmp_path, monkeypatch, fake_key, patched_universe
):
    monkeypatch.setattr(
        download.requests, "get", FakeHFDL(missing={"AAPL", "SMCI", "NVDA"}).get
    )
    monkeypatch.setattr(download, "fetch_yf_daily", lambda t, s, e: {})

    result = download.download_universe(
        out_root=tmp_path, start="2026-01-01", end="2026-08-25", sleep_seconds=0
    )

    assert (tmp_path / "_manifest.json").exists()
    assert (tmp_path / "_download_failures.csv").exists()
    assert len(result["failures"]) == 6  # 3 tickers x 2 frames
    assert json.loads((tmp_path / "_manifest.json").read_text())["tickers"] == {}


def test_bars_slice_starts_at_requested_start(tmp_path, monkeypatch, fake_key,
                                              patched_universe):
    """Full 2002-present history must be cut to the requested window on write."""
    class OldHistory(FakeHFDL):
        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append({"url": url, "params": params, "headers": headers})
            if "/download-token/" in url:
                return FakeResponse(payload={"url": "https://signed.example.com/X"})
            old = _bars_30m(start="2002-01-02 09:30", periods=4)
            new = _bars_30m(start="2026-03-02 09:30", periods=4)
            return FakeResponse(content=_parquet_bytes(pd.concat([old, new])))

    monkeypatch.setattr(download.requests, "get", OldHistory().get)
    download.download_universe(
        out_root=tmp_path, start="2026-01-01", end="2026-08-25", sleep_seconds=0
    )

    df = pd.read_parquet(tmp_path / "ohlcv_30m" / "AAPL.parquet")
    assert df.index.min() >= pd.Timestamp("2026-01-01")
    assert len(df) == 4
