"""rich-based terminal scan animation. Auto-disabled under CI (GitHub Actions sets CI=true)."""

import os
import sys
import time

from rich.console import Console
from rich.table import Table

import config

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass  # not a reconfigurable stream (e.g. redirected/piped in some environments)

console = Console()

_chart_fig = None
_chart_ax = None


def _get_chart_figure():
    """Lazily creates (once) and returns the persistent matplotlib figure/axes
    used for the live chart-flash window. Only ever called when
    chart_animation_enabled() is True -- importing matplotlib.pyplot here
    (not at module top-level) keeps it fully inert under CI/disabled runs."""
    global _chart_fig, _chart_ax
    import matplotlib.pyplot as plt
    if _chart_fig is None:
        plt.ion()
        _chart_fig, _chart_ax = plt.subplots(figsize=(8, 4))
    return _chart_fig, _chart_ax


def animation_enabled() -> bool:
    return bool(config.SCAN_ANIMATION) and os.getenv("CI") is None


def chart_animation_enabled() -> bool:
    return bool(config.SCAN_CHART_ANIMATION) and os.getenv("CI") is None


def flash_ticker(symbol: str, passed: bool, reason: str, alignment: int) -> None:
    status = "[green]passed[/green]" if passed else "[red]filtered[/red]"
    mark = "✅" if passed else "✗"
    label = "" if passed else f"  {reason}"
    console.print(f"  scanning ▸ {symbol:<8} {mark} {status}   align {alignment}/3{label}")
    if animation_enabled():
        time.sleep(config.TICKER_DELAY)


def flash_chart(symbol: str, daily_df, passed: bool) -> None:
    """Redraws the persistent chart window with `symbol`'s last 30 daily
    bars as a simple OHLC candlestick plot, then pauses for
    config.TICKER_DELAY seconds. True no-op (no matplotlib import/state
    touched at all) unless chart_animation_enabled() is True."""
    if not chart_animation_enabled():
        return

    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.lines import Line2D

    fig, ax = _get_chart_figure()
    ax.clear()

    bars = daily_df.tail(30)
    for i, (_, row) in enumerate(bars.iterrows()):
        color = "green" if row["Close"] >= row["Open"] else "red"
        ax.add_line(Line2D([i, i], [row["Low"], row["High"]], color=color, linewidth=1))
        body_bottom = min(row["Open"], row["Close"])
        body_height = abs(row["Close"] - row["Open"]) or 0.01
        ax.add_patch(Rectangle((i - 0.3, body_bottom), 0.6, body_height, color=color))

    status = "passed" if passed else "filtered"
    ax.set_title(f"{symbol} — {status}")
    ax.set_xlim(-1, len(bars))
    if not bars.empty:
        ax.set_ylim(bars["Low"].min() * 0.98, bars["High"].max() * 1.02)

    fig.canvas.draw_idle()
    plt.pause(config.TICKER_DELAY)


def close_chart_window() -> None:
    """Closes the persistent chart window, if one was ever opened. Safe to
    call even if flash_chart was never invoked this run."""
    global _chart_fig, _chart_ax
    if _chart_fig is not None:
        import matplotlib.pyplot as plt
        plt.close(_chart_fig)
        _chart_fig = None
        _chart_ax = None


def print_survivor_table(survivors: list[dict]) -> None:
    table = Table(title="Survivors")
    table.add_column("Symbol")
    table.add_column("Score", justify="right")
    table.add_column("Alignment", justify="right")
    table.add_column("Close", justify="right")
    table.add_column("Fresh", justify="right")
    table.add_column("Reason")

    for s in survivors:
        close = s["analysis"]["snapshots"]["daily"]["close"]
        fresh = s.get("min_bars_since_flip")
        fresh_str = "" if fresh is None else (f"{fresh}+" if fresh == 20 else f"{fresh}")
        table.add_row(
            s["symbol"],
            f"{s['score']:.1f}",
            f"{s['analysis']['alignment']}/3",
            f"{close:.2f}",
            fresh_str,
            s.get("reason", ""),
        )

    console.print(table)
