# tests/test_digest.py
import datetime

import config
import digest


def _stats(total_resolved=10, wins=6, losses=3, scratches=1):
    hit_rate = wins / (wins + losses) if (wins + losses) > 0 else None
    adj_hit_rate = (wins + 0.5 * scratches) / total_resolved if total_resolved else None
    scratch_rate = scratches / total_resolved if total_resolved else None
    return {
        "hit_rate": hit_rate,
        "adj_hit_rate": adj_hit_rate,
        "scratch_rate": scratch_rate,
        "avg_rr": 0.8,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "total_resolved": total_resolved,
        "breakdowns": {},
    }


def _setup(**overrides):
    base = {
        "ticker": "NVDA",
        "bias": "long",
        "conviction": 8,
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "rr": 2.0,
        "horizon": "swing",
        "timeframes": {
            "30m": {"trend": "up"},
            "4h": {"trend": "up"},
            "daily": {"trend": "down"},
        },
        "news_read": "net bullish, 3 headlines",
        "reasoning": "Strong breakout above resistance with volume confirmation.",
    }
    base.update(overrides)
    return base


_NOW = datetime.datetime(2026, 7, 22, 6, 0)


# -- build_digest ---------------------------------------------------------

def test_build_digest_full_ordering():
    setups = [
        _setup(ticker="NVDA"),
        _setup(ticker="AMD", bias="short", conviction=6),
    ]
    scan_counts = {"scanned": 480, "filtered": 25, "alerts": 2}
    stats = _stats()

    msg = digest.build_digest(
        "premarket", _NOW, setups, scan_counts, stats,
        market_context="SPY +0.3% | QQQ +0.5% | VIX 14.2",
    )

    assert "PRE-MARKET SCAN" in msg
    idx_header = msg.index("PRE-MARKET SCAN")
    idx_context = msg.index("SPY +0.3%")
    idx_counts = msg.index("Scanned 480")
    idx_setups_divider = msg.index("SETUPS")
    idx_nvda = msg.index("NVDA")
    idx_amd = msg.index("AMD")
    idx_journal_divider = msg.index("JOURNAL")
    idx_hit_rate = msg.index("Hit rate")

    assert (idx_header < idx_context < idx_counts < idx_setups_divider
            < idx_nvda < idx_amd < idx_journal_divider < idx_hit_rate)


def test_build_digest_preclose_header():
    msg = digest.build_digest("preclose", _NOW, [], {"scanned": 1, "filtered": 0, "alerts": 0}, None)
    assert "PRE-CLOSE SCAN" in msg


def test_build_digest_stats_none_no_crash():
    setups = [_setup()]
    scan_counts = {"scanned": 100, "filtered": 10, "alerts": 1}

    msg = digest.build_digest("premarket", _NOW, setups, scan_counts, None)

    assert "JOURNAL" in msg
    assert "Hit rate" not in msg
    assert "None" not in msg


def test_build_digest_empty_market_context_omits_line():
    setups = [_setup()]
    scan_counts = {"scanned": 100, "filtered": 10, "alerts": 1}

    msg = digest.build_digest("premarket", _NOW, setups, scan_counts, _stats(), market_context="")

    assert "SPY" not in msg
    header_end = msg.index("Scanned")
    # no stray market-context line/placeholder between header and scan-counts line
    assert msg[:header_end].count("\n\n") == 1


def test_build_digest_setup_contains_key_fields():
    setups = [_setup(ticker="NVDA")]
    scan_counts = {"scanned": 100, "filtered": 10, "alerts": 1}

    msg = digest.build_digest("premarket", _NOW, setups, scan_counts, _stats())

    assert "NVDA" in msg
    assert "swing" in msg
    assert "100.0" in msg or "100" in msg
    assert "net bullish, 3 headlines" in msg
    assert "Strong breakout above resistance with volume confirmation." in msg
    assert "🟢" in msg  # conviction 8 -> green


def test_build_digest_low_conviction_yellow():
    setups = [_setup(ticker="AMD", conviction=6)]
    scan_counts = {"scanned": 100, "filtered": 10, "alerts": 1}

    msg = digest.build_digest("premarket", _NOW, setups, scan_counts, _stats())

    assert "🟡" in msg


def test_build_digest_paper_mode_suffix(monkeypatch):
    monkeypatch.setattr(config, "PAPER_MODE", True)
    msg = digest.build_digest("premarket", _NOW, [], {"scanned": 1, "filtered": 0, "alerts": 0}, _stats())
    assert "(paper)" in msg

    monkeypatch.setattr(config, "PAPER_MODE", False)
    msg = digest.build_digest("premarket", _NOW, [], {"scanned": 1, "filtered": 0, "alerts": 0}, _stats())
    assert "(paper)" not in msg


# -- build_no_setup_digest --------------------------------------------------

def test_build_no_setup_digest_with_stats():
    msg = digest.build_no_setup_digest(
        "premarket", _NOW, {"scanned": 100, "filtered": 0, "alerts": 0},
        "No symbols cleared the conviction threshold today.",
        _stats(), session_number=5,
    )

    assert msg  # non-empty
    assert "No symbols cleared the conviction threshold today." in msg
    assert "Session 5" in msg
    assert "Hit rate" in msg


def test_build_no_setup_digest_without_stats():
    msg = digest.build_no_setup_digest(
        "preclose", _NOW, {"scanned": 100, "filtered": 0, "alerts": 0},
        "Market too choppy.",
        None, session_number=1,
    )

    assert msg
    assert "Market too choppy." in msg
    assert "Session 1" in msg
    assert "Hit rate" not in msg
    assert "None" not in msg


# -- split_for_telegram -----------------------------------------------------

def test_split_for_telegram_under_limit_unchanged():
    msg = "short message"
    result = digest.split_for_telegram(msg, limit=4096)
    assert result == [msg]


_TICKER_NAMES = ["ALPHA", "BRAVO", "CHRLI", "DELTA", "ECHOX",
                 "FXTRT", "GLFXX", "HOTLX", "INDXX", "JULTX"]


def test_split_for_telegram_splits_over_limit():
    setups = [_setup(ticker=t, reasoning="X" * 100) for t in _TICKER_NAMES]
    scan_counts = {"scanned": 100, "filtered": 10, "alerts": 10}
    msg = digest.build_digest("premarket", _NOW, setups, scan_counts, _stats())

    chunks = digest.split_for_telegram(msg, limit=500)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 500

    all_tickers = _TICKER_NAMES
    recovered = []
    for ticker in all_tickers:
        count = sum(chunk.count(ticker) for chunk in chunks)
        assert count == 1
        recovered.append(ticker)
    assert recovered == all_tickers
