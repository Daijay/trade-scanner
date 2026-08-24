# data.py
"""Universe construction and batched multi-timeframe OHLCV fetching."""

import io
import logging

import pandas as pd
import requests
import yfinance as yf

import config
import indicators

logger = logging.getLogger(__name__)


def _scrape_tickers(url: str, table_index: int, symbol_col: str) -> list[str]:
    """Fetch and parse a constituent ticker table. Fails loudly (RuntimeError)
    on any network/parse problem rather than silently returning [] -- a broken
    source must surface, not silently shrink the universe."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to fetch tickers from {url}: {e!r}") from e

    try:
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as e:
        raise RuntimeError(f"Failed to parse HTML tables from {url}: {e!r}") from e

    if table_index >= len(tables):
        raise RuntimeError(
            f"Table index {table_index} out of range for {url} (found {len(tables)} tables)"
        )

    table = tables[table_index]
    if symbol_col not in table.columns:
        raise RuntimeError(
            f"Column {symbol_col!r} not found in table {table_index} at {url} "
            f"(columns: {list(table.columns)})"
        )

    tickers = [str(s).replace(".", "-").strip() for s in table[symbol_col].tolist()]
    if not tickers:
        raise RuntimeError(f"No tickers parsed from {url} (table {table_index}, column {symbol_col!r})")
    return tickers


def _sp500_tickers() -> list[str]:
    return _scrape_tickers(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 0, "Symbol"
    )


def _nasdaq100_tickers() -> list[str]:
    return _scrape_tickers(
        "https://www.slickcharts.com/nasdaq100", 0, "Symbol"
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


def fetch_market_context() -> str:
    """SPY/QQQ daily % change + VIX level for the digest header. PLAN.md §10.
    Never raises -- returns "" on any failure or missing data, since this is
    decorative context that must not block or crash the scan."""
    try:
        frames = fetch_ohlcv(["SPY", "QQQ", "^VIX"], "daily")
    except Exception as e:
        logger.warning("fetch_market_context failed: %r", e)
        return ""

    parts = []
    for symbol in ("SPY", "QQQ"):
        df = frames.get(symbol)
        if df is None or len(df) < 2:
            continue
        prev_close = df["Close"].iloc[-2]
        close = df["Close"].iloc[-1]
        if prev_close == 0:
            continue
        pct = (close - prev_close) / prev_close * 100
        sign = "+" if pct >= 0 else ""
        parts.append(f"{symbol} {sign}{pct:.1f}%")

    vix_df = frames.get("^VIX")
    if vix_df is not None and len(vix_df) >= 1:
        parts.append(f"VIX {vix_df['Close'].iloc[-1]:.1f}")

    return " | ".join(parts)


def _short_timeframes(frames: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Timeframes holding fewer than config.MIN_BARS_PER_TIMEFRAME usable bars.

    Counts bars on the frame indicators actually run on, not the raw fetched
    frame: '4h' arrives as 1h bars, so a symbol can hold plenty of 1h rows and
    still resample to far too few 4h bars. VMRK did exactly that on Aug 21 2026
    -- 400 daily bars, 28 1h bars, 8 4h bars -- and the 8-row 4h frame crashed
    ta's AverageTrueRange(window=14), aborting the whole scan."""
    short = {}
    for tf, df in frames.items():
        n = len(indicators.effective_frame(tf, df))
        if n < config.MIN_BARS_PER_TIMEFRAME:
            short[tf] = n
    return short


def fetch_all_timeframes(symbols: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    """Fetch 30m/4h/daily for all symbols; keep only symbols present on all three."""
    per_tf = {tf: fetch_ohlcv(symbols, tf) for tf in config.TIMEFRAMES}

    result: dict[str, dict[str, pd.DataFrame]] = {}
    for symbol in symbols:
        frames = {}
        for tf in config.TIMEFRAMES:
            if symbol in per_tf[tf]:
                frames[tf] = per_tf[tf][symbol]
        if set(frames.keys()) != set(config.TIMEFRAMES.keys()):
            missing = set(config.TIMEFRAMES.keys()) - set(frames.keys())
            logger.info("Dropping %s: missing timeframes %s", symbol, missing)
            continue

        short = _short_timeframes(frames)
        if short:
            logger.info(
                "Dropping %s: insufficient history %s (need >= %d bars each)",
                symbol, short, config.MIN_BARS_PER_TIMEFRAME,
            )
            continue

        result[symbol] = frames

    return result
