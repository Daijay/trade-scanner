import os
from display import animation_enabled, flash_ticker, print_survivor_table


def test_animation_enabled_respects_ci_env(monkeypatch):
    import config
    monkeypatch.setattr(config, "SCAN_ANIMATION", True)
    monkeypatch.delenv("CI", raising=False)
    assert animation_enabled() is True
    monkeypatch.setenv("CI", "true")
    assert animation_enabled() is False


def test_animation_enabled_respects_config_flag(monkeypatch):
    import config
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(config, "SCAN_ANIMATION", False)
    assert animation_enabled() is False


def test_flash_ticker_does_not_raise(monkeypatch):
    import config
    monkeypatch.setattr(config, "SCAN_ANIMATION", False)   # skip real sleep in tests
    flash_ticker("AAPL", True, "", 3)
    flash_ticker("XYZ", False, "low ATR", 0)


def test_chart_animation_enabled_respects_ci_env(monkeypatch):
    import config
    from display import chart_animation_enabled
    monkeypatch.setattr(config, "SCAN_CHART_ANIMATION", True)
    monkeypatch.delenv("CI", raising=False)
    assert chart_animation_enabled() is True
    monkeypatch.setenv("CI", "true")
    assert chart_animation_enabled() is False


def test_chart_animation_enabled_respects_config_flag(monkeypatch):
    import config
    from display import chart_animation_enabled
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(config, "SCAN_CHART_ANIMATION", False)
    assert chart_animation_enabled() is False


def test_print_survivor_table_does_not_raise():
    survivors = [
        {
            "symbol": "NVDA", "score": 42.5,
            "analysis": {"alignment": 3, "snapshots": {"daily": {"close": 950.2}}},
            "reason": "", "min_bars_since_flip": 5,
        },
    ]
    print_survivor_table(survivors)
