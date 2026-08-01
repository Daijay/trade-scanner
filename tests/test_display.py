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


def test_flash_chart_noop_when_disabled(monkeypatch):
    import config
    import display
    monkeypatch.setattr(config, "SCAN_CHART_ANIMATION", False)
    monkeypatch.delenv("CI", raising=False)

    calls = []
    monkeypatch.setattr(display, "_get_chart_figure", lambda: calls.append("called"))

    import pandas as pd
    df = pd.DataFrame({
        "Open": [1.0], "High": [1.5], "Low": [0.5], "Close": [1.2],
    }, index=pd.DatetimeIndex(["2026-07-30"]))

    display.flash_chart("AAPL", df, True)
    assert calls == []


def test_flash_chart_noop_under_ci(monkeypatch):
    import config
    import display
    monkeypatch.setattr(config, "SCAN_CHART_ANIMATION", True)
    monkeypatch.setenv("CI", "true")

    calls = []
    monkeypatch.setattr(display, "_get_chart_figure", lambda: calls.append("called"))

    import pandas as pd
    df = pd.DataFrame({
        "Open": [1.0], "High": [1.5], "Low": [0.5], "Close": [1.2],
    }, index=pd.DatetimeIndex(["2026-07-30"]))

    display.flash_chart("AAPL", df, True)
    assert calls == []


def test_flash_chart_renders_when_enabled(monkeypatch):
    import config
    import display
    monkeypatch.setattr(config, "SCAN_CHART_ANIMATION", True)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(config, "TICKER_DELAY", 0.0)

    import pandas as pd
    df = pd.DataFrame({
        "Open": [1.0, 1.1, 1.2],
        "High": [1.5, 1.4, 1.3],
        "Low": [0.5, 0.6, 0.7],
        "Close": [1.2, 1.0, 1.25],
    }, index=pd.DatetimeIndex(["2026-07-28", "2026-07-29", "2026-07-30"]))

    display.flash_chart("AAPL", df, True)
    display.close_chart_window()


def test_print_survivor_table_does_not_raise():
    survivors = [
        {
            "symbol": "NVDA", "score": 42.5,
            "analysis": {"alignment": 3, "snapshots": {"daily": {"close": 950.2}}},
            "reason": "", "min_bars_since_flip": 5,
        },
    ]
    print_survivor_table(survivors)
