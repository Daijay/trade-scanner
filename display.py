"""rich-based terminal scan animation. Auto-disabled under CI (GitHub Actions sets CI=true)."""

import os
import time

from rich.console import Console
from rich.table import Table

import config

console = Console()


def animation_enabled() -> bool:
    return bool(config.SCAN_ANIMATION) and os.getenv("CI") is None


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
    table.add_column("Reason")

    for s in survivors:
        close = s["analysis"]["snapshots"]["daily"]["close"]
        table.add_row(
            s["symbol"],
            f"{s['score']:.1f}",
            f"{s['analysis']['alignment']}/3",
            f"{close:.2f}",
            s.get("reason", ""),
        )

    console.print(table)
