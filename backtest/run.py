# backtest/run.py
"""CLI entry point: replay a strategy and write its report.

    PYTHONPATH=. python -m backtest.run --start 2026-01-02 --end 2026-08-21

Writes the rendered markdown report and the raw alert records side by side. The
raw records matter as much as the report: every rate in the report is derived
from them by ``journal._rate_block``, so anyone who doubts a number can
recompute it, and anyone who wants a segmentation this report does not print
can build it without a 40-minute re-run.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from backtest.engine import run_backtest
from backtest.report import render_report
from backtest.store import BarStore

DEFAULT_START = "2026-01-02"
DEFAULT_END = "2026-08-21"
DEFAULT_OUT = "backtest/reports/v1_technical_2026.md"

STRATEGIES = {"v1_technical": "backtest.strategies.v1_technical:V1Technical"}


def _load(name: str):
    module, cls = STRATEGIES[name].split(":")
    mod = __import__(module, fromlist=[cls])
    return getattr(mod, cls)()


def _progress(t0):
    def report(done: int, total: int, slot, alerts: int) -> None:
        elapsed = time.time() - t0
        rate = elapsed / done
        eta = rate * (total - done)
        print(
            f"[{done}/{total}] {slot}  alerts={alerts}  "
            f"{rate:.1f}s/slot  elapsed={elapsed / 60:.1f}m  eta={eta / 60:.1f}m",
            flush=True,
        )

    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy", default="v1_technical", choices=sorted(STRATEGIES))
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--root", default="data")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--limit-slots", type=int, default=None, help="smoke-test a prefix of the run")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.ERROR)
    store = BarStore(args.root)
    universe = store.tickers("30m")
    print(f"universe: {len(universe)} tickers with 30m history", flush=True)

    from backtest.market_calendar import scan_slots

    slots = list(scan_slots(args.start, args.end, root=args.root))
    if args.limit_slots:
        slots = slots[: args.limit_slots]
    print(f"slots: {len(slots)} from {slots[0]} to {slots[-1]}", flush=True)

    t0 = time.time()
    results = run_backtest(
        _load(args.strategy),
        store=store,
        slots=slots,
        universe=universe,
        progress=_progress(t0),
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(results), encoding="utf-8")

    raw = out.with_suffix(".csv")
    pd.DataFrame(results["alerts"]).to_csv(raw, index=False)

    print(f"\nwrote {out} and {raw}", flush=True)
    print(
        f"slots={results['slots']} before_gate={results['signals_before_gate']} "
        f"after_gate={results['signals_after_gate']} "
        f"wall={results['wall_clock_s'] / 60:.1f}m",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
