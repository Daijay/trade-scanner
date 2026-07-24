# filter.py
"""Hard reject rules + survivor scoring. PLAN.md §6. Pure math, zero API cost."""

import numpy as np

import config


def _avg_volume_and_price(frames: dict) -> tuple[float, float]:
    daily = frames["daily"]
    avg_volume = float(daily["Volume"].tail(20).mean())
    price = float(daily["Close"].iloc[-1])
    return avg_volume, price


def passes_hard_filter(symbol: str, frames: dict, analysis: dict) -> tuple[bool, str]:
    daily_snap = analysis["snapshots"].get("daily")
    if daily_snap is None or any(np.isnan(v) for v in daily_snap.values() if isinstance(v, float)):
        return False, "missing or malformed data"

    avg_volume, price = _avg_volume_and_price(frames)

    if avg_volume < config.MIN_AVG_VOLUME:
        return False, f"avg volume {avg_volume:,.0f} below MIN_AVG_VOLUME {config.MIN_AVG_VOLUME:,}"
    if price < config.MIN_PRICE:
        return False, f"price {price:.2f} below MIN_PRICE {config.MIN_PRICE}"
    if daily_snap["atr_pct"] < config.MIN_ATR_PCT:
        return False, f"ATR% {daily_snap['atr_pct']:.2f} below MIN_ATR_PCT {config.MIN_ATR_PCT}"
    if analysis["alignment"] <= 1:
        return False, f"alignment score {analysis['alignment']} <= 1"

    return True, ""


def score_survivor(analysis: dict) -> float:
    """Higher = more interesting. Combines alignment, volume surge, range position, ADX, BB state."""
    daily = analysis["snapshots"]["daily"]

    alignment_score = analysis["alignment"] * 10.0

    vol_ratio = daily["vol_ratio"] if not np.isnan(daily["vol_ratio"]) else 1.0
    volume_score = min(vol_ratio, 5.0) * 5.0

    rng = daily["range_high_20d"] - daily["range_low_20d"]
    if rng > 0:
        dist_from_high = (daily["range_high_20d"] - daily["close"]) / rng
        dist_from_low = (daily["close"] - daily["range_low_20d"]) / rng
        range_edge_score = (1.0 - min(dist_from_high, dist_from_low)) * 10.0
    else:
        range_edge_score = 0.0

    adx = daily["adx14"] if not np.isnan(daily["adx14"]) else 0.0
    adx_score = min(adx, 50.0) / 5.0

    bb_width = (daily["bb_upper"] - daily["bb_lower"]) / daily["close"] if daily["close"] else 0.0
    squeeze_score = max(0.0, 5.0 - bb_width * 100.0)

    return alignment_score + volume_score + range_edge_score + adx_score + squeeze_score


def run_filter(universe_frames: dict) -> tuple[list[dict], list[dict]]:
    """universe_frames: {symbol: {'30m': df, '4h': df, 'daily': df}}."""
    from indicators import analyze_symbol

    candidates = []
    filtered_out = []

    for symbol, frames in universe_frames.items():
        analysis = analyze_symbol(frames)
        ok, reason = passes_hard_filter(symbol, frames, analysis)
        if not ok:
            filtered_out.append({
                "symbol": symbol, "analysis": analysis, "score": 0.0, "reason": reason,
                "bars_since_flip": analysis["bars_since_flip"],
                "min_bars_since_flip": analysis["min_bars_since_flip"],
            })
            continue
        score = score_survivor(analysis)
        candidates.append({
            "symbol": symbol, "analysis": analysis, "score": score, "reason": "",
            "bars_since_flip": analysis["bars_since_flip"],
            "min_bars_since_flip": analysis["min_bars_since_flip"],
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    survivors = candidates[: config.MAX_SURVIVORS]
    excess = candidates[config.MAX_SURVIVORS :]
    for c in excess:
        c["reason"] = "excess"
    filtered_out.extend(excess)

    return survivors, filtered_out
