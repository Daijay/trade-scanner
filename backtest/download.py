# backtest/download.py
"""Bulk historical price download into a local parquet cache.

Two sources, split by timeframe (see the plan's Decision Gate):

* ``30min`` bars come from hfdatalibrary via its **two-step signed-URL flow**.
  ``GET /v1/bars/{TICKER}`` returns full 2002-present history with no date
  filtering (~14.7 GB across our universe) and is deliberately never used.
  Instead we request a short-lived signed URL from
  ``GET /v1/download-token/{TICKER}`` (authenticated with ``X-API-Key``) and
  then fetch that URL with **no** auth header.
* ``daily`` bars come from yfinance with ``auto_adjust=False``.

Both frames are therefore stored **unadjusted**, which is the convention
recorded in the manifest. Do not flip ``auto_adjust`` without also changing the
manifest and the README: mixing back-adjusted daily bars with unadjusted
intraday bars silently mis-prices high-yield names by up to ~3%.

Nothing in this module writes to the live scan pipeline; it only reads
``data.build_universe()``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

from data import build_universe

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

HFDL_BASE_URL = "https://api.hfdatalibrary.com/v1"

#: hfdatalibrary allows 100 data downloads per minute.
HFDL_SLEEP_SECONDS = 0.7

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
INDEX_NAME = "timestamp"
ADJUSTMENT = "unadjusted"

_SIGNED_URL_KEYS = ("url", "signed_url", "download_url")


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------

def _api_key() -> str | None:
    """Read HFDL_API_KEY from the environment / repo-root .env.

    Never logged, never written to the manifest, never included in an error
    string.
    """
    load_dotenv(REPO_ROOT / ".env")
    key = os.getenv("HFDL_API_KEY")
    return key.strip() if key else None


# --------------------------------------------------------------------------
# hfdatalibrary: 30-minute bars via the signed-URL flow
# --------------------------------------------------------------------------

def _normalize_hfdl(df: pd.DataFrame) -> pd.DataFrame:
    """Return the frame with a sorted DatetimeIndex named ``timestamp``.

    hfdatalibrary hands back a RangeIndex with ``datetime`` as an ordinary
    column (datetime64[ns], tz-naive, US/Eastern wall clock).
    """
    if "datetime" in df.columns:
        df = df.set_index("datetime")
    df.index = pd.to_datetime(df.index)
    df.index.name = INDEX_NAME
    df = df.sort_index()
    keep = [c for c in (*OHLCV_COLUMNS, "source") if c in df.columns]
    return df[keep]


def fetch_hfdl(
    ticker: str,
    timeframe: str = "30min",
    version: str = "clean",
    timeout: int = 300,
) -> tuple[pd.DataFrame | None, str | None]:
    """Fetch one ticker's full history from hfdatalibrary.

    Returns ``(df, None)`` on success and ``(None, reason)`` on failure. Never
    raises: a single bad ticker must not abort a 500-ticker batch.
    """
    key = _api_key()
    if not key:
        return None, "HFDL_API_KEY not set"

    token_url = f"{HFDL_BASE_URL}/download-token/{ticker}"
    params = {"timeframe": timeframe, "format": "parquet", "version": version}
    try:
        resp = requests.get(
            token_url, params=params, headers={"X-API-Key": key}, timeout=60
        )
    except requests.RequestException as e:
        return None, f"token request failed: {type(e).__name__}"

    if resp.status_code != 200:
        return None, f"token HTTP {resp.status_code}"

    try:
        payload = resp.json()
    except ValueError:
        return None, "token response was not JSON"

    signed = None
    if isinstance(payload, dict):
        for k in _SIGNED_URL_KEYS:
            if payload.get(k):
                signed = payload[k]
                break
    if not signed:
        return None, "no signed URL in token response"

    # Deliberately no auth header here: the signature is the credential.
    try:
        data_resp = requests.get(signed, timeout=timeout)
    except requests.RequestException as e:
        return None, f"download request failed: {type(e).__name__}"

    if data_resp.status_code != 200:
        return None, f"download HTTP {data_resp.status_code}"

    try:
        df = pd.read_parquet(io.BytesIO(data_resp.content))
    except Exception as e:  # noqa: BLE001 - corrupt payload must not abort batch
        return None, f"parquet parse failed: {type(e).__name__}"

    if df.empty:
        return None, "empty parquet"

    try:
        return _normalize_hfdl(df), None
    except Exception as e:  # noqa: BLE001
        return None, f"schema normalize failed: {type(e).__name__}"


# --------------------------------------------------------------------------
# yfinance: unadjusted daily bars
# --------------------------------------------------------------------------

def _extract_ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    df = raw
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker in raw.columns.get_level_values(0):
            df = raw[ticker]
        elif ticker in raw.columns.get_level_values(-1):
            df = raw.xs(ticker, axis=1, level=-1)
        else:
            return None
    df = df.dropna(how="all")
    if df.empty or "Close" not in df.columns or df["Close"].isna().all():
        return None
    keep = [c for c in OHLCV_COLUMNS if c in df.columns]
    df = df[keep].copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = INDEX_NAME
    return df.sort_index()


def fetch_yf_daily(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Batched **unadjusted** daily bars.

    ``auto_adjust=False`` is required: it keeps the daily frame on the same
    (raw, unadjusted) price convention as the hfdatalibrary intraday frames.
    """
    if not tickers:
        return {}
    raw = yf.download(
        tickers=tickers,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=False,
        group_by="ticker",
        progress=False,
        threads=True,
    )
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = _extract_ticker_frame(raw, t)
        if df is not None:
            out[t] = df
    return out


# --------------------------------------------------------------------------
# cache bookkeeping
# --------------------------------------------------------------------------

def _read_cached(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - a corrupt cache file is just a refetch
        return None


def _range_ok(
    df: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    start_tol_days: int = 7,
    end_tol_days: int = 10,
) -> bool:
    if df is None or df.empty:
        return False
    idx = pd.to_datetime(df.index)
    return (
        idx.min() <= start + pd.Timedelta(days=start_tol_days)
        and idx.max() >= end - pd.Timedelta(days=end_tol_days)
    )


def _is_complete(
    path: Path,
    ticker: str,
    frame: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    prior_manifest: dict,
) -> bool:
    """Resume predicate: has this ticker/frame already been fully fetched?

    A file *existing* is not the same as a file *covering the requested start*.
    When the requested window is widened (adding indicator warm-up history, say),
    every cached file still on disk covers only the old, narrower range, and a
    resume predicate that only checked ``path.exists()`` — or that accepted any
    previously recorded request — would skip them all and silently leave the
    cache short.

    So there are exactly two ways to be complete:

    1. The cached file's **actual first bar** is at (or just after) the requested
       start and its actual last bar reaches the requested end — the real test,
       applied to the data rather than to bookkeeping.
    2. A previous run recorded this ticker/frame as fetched for a range that
       **contains** the currently requested one. This covers tickers whose
       history legitimately begins after the requested start (recent IPOs,
       renamed symbols): the vendor has nothing earlier, so condition 1 can
       never be satisfied and refetching every run would be pure waste.
       Crucially it is a *containment* test, not an equality test: a narrower
       recorded range never satisfies a wider request.
    """
    if not path.exists():
        return False
    entry = (prior_manifest.get("tickers", {}).get(ticker, {}) or {}).get(frame)
    if entry:
        rec_start = entry.get("requested_start")
        rec_end = entry.get("requested_end")
        if rec_start and rec_end and \
                rec_start <= str(start.date()) and rec_end >= str(end.date()):
            return True
    return _range_ok(_read_cached(path), start, end)


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, compression="snappy")


def _manifest_entry(
    path: Path, source: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp
) -> dict | None:
    df = _read_cached(path)
    if df is None or df.empty:
        return None
    idx = pd.to_datetime(df.index)
    return {
        "source": source,
        "timeframe": timeframe,
        "path": str(path).replace("\\", "/"),
        "first_bar": str(idx.min()),
        "last_bar": str(idx.max()),
        "rows": int(len(df)),
        "adjustment": ADJUSTMENT,
        "requested_start": str(start.date()),
        "requested_end": str(end.date()),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _write_failures(path: Path, failures: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "frame", "reason"])
        w.writeheader()
        for row in failures:
            w.writerow(row)


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def download_universe(
    out_root: str | Path = "data",
    start: str = "2026-01-01",
    end: str = "2026-08-25",
    tickers: list[str] | None = None,
    sleep_seconds: float = HFDL_SLEEP_SECONDS,
    yf_batch: int = 40,
    start_30m: str | None = None,
    start_daily: str | None = None,
) -> dict:
    """Download 30m (hfdatalibrary) + daily (yfinance) bars for the universe.

    Resumable: any ticker whose parquet already covers the requested range is
    skipped without a network call. Writes ``_manifest.json`` and
    ``_download_failures.csv`` under ``out_root``.

    ``start_30m`` / ``start_daily`` override *start* per frame. They exist
    because the two frames need different amounts of **indicator warm-up**
    history before the simulation window: ``indicators.compute_indicators``
    needs 200 periods for ``ema200``, so the daily frame needs ~200 trading
    days ahead of the window and the 30m frame needs enough bars for 200
    derived 4h buckets (~3.25 buckets per session, so ~62 sessions). Without
    that head-room every ticker's ``ema200`` is NaN at every simulated instant,
    ``alignment`` collapses to 0, and ``filter.passes_hard_filter`` rejects the
    entire universe with "missing or malformed data".
    """
    out_root = Path(out_root)
    dir_30m = out_root / "ohlcv_30m"
    dir_daily = out_root / "ohlcv_daily"
    dir_30m.mkdir(parents=True, exist_ok=True)
    dir_daily.mkdir(parents=True, exist_ok=True)

    manifest_path = out_root / "_manifest.json"
    failures_path = out_root / "_download_failures.csv"
    prior = _load_manifest(manifest_path)

    universe = list(tickers) if tickers is not None else build_universe()
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    start_30m_ts = pd.Timestamp(start_30m) if start_30m else start_ts
    start_daily_ts = pd.Timestamp(start_daily) if start_daily else start_ts

    failures: list[dict] = []

    # ---- 30-minute bars, one signed-URL round trip per ticker --------------
    fetched_30m = 0
    for i, ticker in enumerate(universe, 1):
        path = dir_30m / f"{ticker}.parquet"
        if _is_complete(path, ticker, "30min", start_30m_ts, end_ts, prior):
            continue
        df, err = fetch_hfdl(ticker, timeframe="30min", version="clean")
        if sleep_seconds:
            time.sleep(sleep_seconds)
        if err is not None:
            logger.info("30m fetch failed for %s: %s", ticker, err)
            failures.append({"ticker": ticker, "frame": "30min", "reason": err})
            continue
        sliced = df.loc[(df.index >= start_30m_ts) & (df.index <= end_ts)]
        if sliced.empty:
            failures.append(
                {"ticker": ticker, "frame": "30min", "reason": "no bars in requested range"}
            )
            continue
        _write_parquet(sliced, path)
        fetched_30m += 1
        if i % 25 == 0:
            logger.info("30m progress: %d/%d (%d fetched)", i, len(universe), fetched_30m)

    # ---- daily bars, batched -----------------------------------------------
    need_daily = [
        t for t in universe
        if not _is_complete(dir_daily / f"{t}.parquet", t, "daily", start_daily_ts, end_ts, prior)
    ]
    for i in range(0, len(need_daily), yf_batch):
        chunk = need_daily[i:i + yf_batch]
        try:
            frames = fetch_yf_daily(chunk, str(start_daily_ts.date()), end)
        except Exception as e:  # noqa: BLE001 - a bad batch must not abort the run
            for t in chunk:
                failures.append(
                    {"ticker": t, "frame": "daily", "reason": f"batch failed: {type(e).__name__}"}
                )
            continue
        for t in chunk:
            df = frames.get(t)
            if df is None or df.empty:
                failures.append({"ticker": t, "frame": "daily", "reason": "no yfinance data"})
                continue
            _write_parquet(df, dir_daily / f"{t}.parquet")

    # ---- manifest, rebuilt from what is actually on disk --------------------
    manifest: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_start": str(start_ts.date()),
        "requested_start_30min": str(start_30m_ts.date()),
        "requested_start_daily": str(start_daily_ts.date()),
        "requested_end": str(end_ts.date()),
        "adjustment": ADJUSTMENT,
        "sources": {"30min": "hfdatalibrary", "daily": "yfinance"},
        "tickers": {},
    }
    for ticker in universe:
        entry: dict = {}
        e30 = _manifest_entry(
            dir_30m / f"{ticker}.parquet", "hfdatalibrary", "30min", start_30m_ts, end_ts
        )
        if e30:
            entry["30min"] = e30
        ed = _manifest_entry(
            dir_daily / f"{ticker}.parquet", "yfinance", "daily", start_daily_ts, end_ts
        )
        if ed:
            entry["daily"] = ed
        if entry:
            manifest["tickers"][ticker] = entry

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    _write_failures(failures_path, failures)

    logger.info(
        "download_universe: %d tickers, %d with 30m, %d failures",
        len(universe), sum(1 for v in manifest["tickers"].values() if "30min" in v), len(failures),
    )
    return {"manifest": manifest, "failures": failures}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    result = download_universe()
    print(
        f"tickers in manifest: {len(result['manifest']['tickers'])} "
        f"failures: {len(result['failures'])}"
    )
