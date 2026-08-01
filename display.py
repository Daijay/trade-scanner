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
