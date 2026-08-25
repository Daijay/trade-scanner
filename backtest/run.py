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
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from backtest.engine import run_backtest
from backtest.report import render_report
from backtest.store import BarStore
from backtest.strategy import Signal

DEFAULT_START = "2026-01-02"
DEFAULT_END = "2026-08-21"
DEFAULT_OUT = "backtest/reports/v1_technical_2026.md"

#: Per-slot generated signals, so an interrupted run resumes instead of
#: restarting. Lives under ``data/`` (gitignored) -- generated artefacts of a
#: specific cache, never source. A full 441-ticker replay is ~30 CPU-minutes of
#: indicator work; losing it to a Ctrl+C is avoidable, so it is avoided.
DEFAULT_SIGNAL_CACHE = "data/_signal_cache"

STRATEGIES = {"v1_technical": "backtest.strategies.v1_technical:V1Technical"}


# ------------------------------------------------------------------ workers
#
# Signal *generation* is embarrassingly parallel: each slot builds its own
# PointInTimeView and shares no state with any other, and the strategy is a pure
# function of (view, universe). Resolution is NOT parallel and stays in
# run_backtest, where it walks slots in order accumulating bars_open exactly as
# main.py does live.
#
# Workers return the FULL ungated signal list. Gating stays in run_backtest, so
# there is exactly one implementation of the gate and one place the
# before/after counts are computed, whether or not a pool was used.

_W: dict = {}


def _init_worker(root: str, strategy_name: str, universe: list, cache_dir: str) -> None:
    _W["store"] = BarStore(root)
    _W["strategy"] = _load(strategy_name)
    _W["universe"] = universe
    _W["cache_dir"] = Path(cache_dir) if cache_dir else None


def _cache_path(cache_dir: Path, strategy_name: str, slot) -> Path:
    return Path(cache_dir) / f"{strategy_name}-{pd.Timestamp(slot):%Y%m%dT%H%M}.json"


def _read_cached(path: Path):
    """(signals, counts) for a cached slot, or None if absent/unreadable.

    A truncated file from a process killed mid-write is treated as a miss and
    regenerated -- never as an empty slot, which would silently drop signals.
    """
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    try:
        return [Signal(**s) for s in payload["signals"]], payload["counts"]
    except (KeyError, TypeError, ValueError):
        return None


def _write_cached(path: Path, signals, counts) -> None:
    """Write atomically: a kill mid-write must not leave a half-slot behind."""
    payload = {
        "signals": [asdict(s) for s in signals],
        "counts": {k: v for k, v in counts.items() if k != "as_of"},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def _generate_slot(slot):
    strategy = _W["strategy"]
    cache_dir = _W.get("cache_dir")
    if cache_dir is not None:
        path = _cache_path(cache_dir, strategy.name, slot)
        hit = _read_cached(path)
        if hit is not None:
            return slot, hit[0], hit[1], True
    signals = strategy.generate(_W["store"].view(slot), _W["universe"])
    counts = dict(strategy.last_run)
    if cache_dir is not None:
        _write_cached(_cache_path(cache_dir, strategy.name, slot), signals, counts)
    return slot, signals, counts, False


class _Replay:
    """Serves signals a pool already generated, so run_backtest is unchanged.

    The engine still applies the gate, still builds resolution views, still
    walks slots in order. Only the generate() call is answered from a cache.
    """

    def __init__(self, name, conviction_source, by_slot, totals):
        self.name = name
        self.conviction_source = conviction_source
        self._by_slot = by_slot
        self.totals = totals

    def generate(self, view, universe):
        return self._by_slot[view.as_of]


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


def _pregenerate(args, slots, universe, t0):
    """Generate every slot's signals across a process pool."""
    from concurrent.futures import ProcessPoolExecutor

    by_slot: dict = {}
    totals = {"slots": 0, "requested": 0, "scanned": 0, "missing_history": 0,
              "survivors": 0, "signals": 0, "unusable_geometry": 0}
    done = 0
    cached = 0
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(args.root, args.strategy, universe, args.cache_dir),
    ) as pool:
        for slot, signals, last_run, was_cached in pool.map(_generate_slot, slots, chunksize=1):
            cached += bool(was_cached)
            by_slot[slot] = signals
            totals["slots"] += 1
            for k in ("requested", "scanned", "missing_history", "survivors",
                      "signals", "unusable_geometry"):
                totals[k] += last_run.get(k, 0)
            done += 1
            if done % 20 == 0 or done == len(slots):
                el = time.time() - t0
                fresh = max(done - cached, 1)
                rate = el / fresh
                print(
                    f"[generate {done}/{len(slots)}] {slot}  cached={cached}  "
                    f"{rate:.2f}s/fresh-slot  elapsed={el / 60:.1f}m  "
                    f"eta={rate * (len(slots) - done) / 60:.1f}m",
                    flush=True,
                )
    proto = _load(args.strategy)
    return _Replay(proto.name, getattr(proto, "conviction_source", ""), by_slot, totals)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy", default="v1_technical", choices=sorted(STRATEGIES))
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--root", default="data")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--limit-slots", type=int, default=None, help="smoke-test a prefix of the run")
    p.add_argument("--cache-dir", default=DEFAULT_SIGNAL_CACHE)
    p.add_argument(
        "--workers", type=int, default=1,
        help="processes for signal generation (resolution is always sequential)",
    )
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
    strategy = _load(args.strategy)

    strategy = _pregenerate(args, slots, universe, t0)

    results = run_backtest(
        strategy,
        store=store,
        slots=slots,
        universe=universe,
        progress=None,
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
