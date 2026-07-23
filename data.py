# data.py
"""Universe construction and batched multi-timeframe OHLCV fetching."""

import io
import logging

import pandas as pd
import requests
import yfinance as yf

import config

logger = logging.getLogger(__name__)


def _wiki_tickers(url: str, table_index: int, symbol_col: str) -> list[str]:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
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
        df.columns = df.columns.get_level_values(-1)
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
