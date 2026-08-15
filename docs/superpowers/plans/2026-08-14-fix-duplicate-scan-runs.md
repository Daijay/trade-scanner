# Fix Duplicate Scan Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the same premarket/preclose scan slot from executing the full pipeline more than once per day, and stop `journal.py` from silently accepting duplicate alert records if it happens anyway.

**Architecture:** Root cause (confirmed in this session's diagnosis, see conversation / commit `03897d2`): `scan.yml`'s two cron lines per slot both fire every weekday regardless of DST, and `config.SCAN_TOLERANCE_MINUTES = 90` is wide enough that both firings land inside `within_scan_tolerance()`'s window on days when GitHub Actions' queuing delay doesn't happen to push one of them out. `journal.log_alerts()` then appends unconditionally with no dedup, and `digest.build_daily_report()` renders every raw entry. Fix in two layers: (1) a state-based idempotency guard in `main.py::run_scan()` that skips the entire pipeline if this scan slot already has a journal entry for today (stops wasted API cost and prevents the duplicate entirely), and (2) a defense-in-depth dedup inside `journal.py::log_alerts()` itself (catches any other duplicate-invocation path, e.g. manual `--force-run` reruns). A third task cleans up the duplicate records already sitting in the live `journal.json` (Aug 7-13) so `compute_stats()` isn't permanently skewed by double-counted resolutions.

**Tech Stack:** Python 3.11, pytest, existing `journal.py`/`main.py`/`digest.py` modules — no new dependencies.

## Global Constraints

- Do not touch `scan.yml`'s cron lines or `config.SCAN_TOLERANCE_MINUTES` — the wide tolerance and dual cron offsets exist to solve a real, separate problem (GH Actions delaying scheduled runs 50min-2h+, see `03897d2`'s commit message); narrowing them risks reintroducing "scheduled scan never lands." Fix duplication at the idempotency layer, not the scheduling layer.
- Match existing test conventions in `tests/test_main.py` and `tests/test_journal.py` exactly (fixture style, `monkeypatch.setattr` patterns, `_FakeDatetime` pattern for freezing `main.datetime.datetime.now`).
- `journal.json` is git-tracked, CI-pushed shared state. Task 3's final step (running the cleanup script against the real file and committing/pushing the result) must NOT be auto-executed — stop and get explicit confirmation before running it for real or pushing.

---

### Task 1: Idempotency guard in `main.py::run_scan()`

**Files:**
- Modify: `main.py:102-135` (add a new function after `_current_scan_label`, call it inside `run_scan`)
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `main._scan_already_ran_today(scan: str, now: datetime.datetime) -> bool`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main.py`:

```python
def test_run_scan_skips_when_scan_already_logged_today(monkeypatch):
    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))  # Wednesday, premarket time

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(main.datetime, "datetime", _FakeDatetime)

    existing = [{
        "id": "2026-07-22T05:15-NVDA",
        "timestamp": "2026-07-22T05:15:00-07:00",
        "scan": "premarket",
        "ticker": "NVDA",
        "status": "open",
    }]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": existing)

    def _fail(*a, **k):
        raise AssertionError("should not reach universe build when this scan already ran today")

    monkeypatch.setattr(main.data, "build_universe", _fail)

    result = main.run_scan(dry_run=False)
    assert result == {"setups": [], "digest": None}


def test_run_scan_runs_when_scan_not_yet_logged_today(monkeypatch):
    import filter as filter_mod
    import news
    import analyst
    import digest

    tz = pytz.timezone("America/Vancouver")
    now = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))  # Wednesday, premarket time

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(main.datetime, "datetime", _FakeDatetime)

    # A preclose entry from the SAME day must not block today's premarket run,
    # and a premarket entry from a DIFFERENT day must not either.
    existing = [
        {"id": "a1", "timestamp": "2026-07-22T12:30:00-07:00", "scan": "preclose",
         "ticker": "NVDA", "status": "open"},
        {"id": "a2", "timestamp": "2026-07-21T06:00:00-07:00", "scan": "premarket",
         "ticker": "AMD", "status": "open"},
    ]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": list(existing))
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": None)
    monkeypatch.setattr(journal, "log_alerts", lambda setups, scan, now: [])
    monkeypatch.setattr(journal, "compute_stats", lambda alerts: {})
    monkeypatch.setattr(journal, "should_compute_stats", lambda alerts: False)

    monkeypatch.setattr(main.data, "build_universe", lambda: [])
    monkeypatch.setattr(main.data, "fetch_all_timeframes", lambda symbols: {})
    monkeypatch.setattr(filter_mod, "run_filter", lambda frames: ([], []))
    monkeypatch.setattr(main.display, "flash_ticker", lambda *a, **k: None)
    monkeypatch.setattr(main.display, "print_survivor_table", lambda *a, **k: None)
    monkeypatch.setattr(main.data, "fetch_market_context", lambda: "")
    monkeypatch.setattr(digest, "build_no_setup_digest", lambda *a, **k: "NO SETUP DIGEST")

    result = main.run_scan(dry_run=False)
    assert result["digest"] == "NO SETUP DIGEST"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main.py -k already_logged_today or not_yet_logged_today -v`
Expected: `test_run_scan_skips_when_scan_already_logged_today` FAILS (assertion error — `build_universe` gets called because there's no guard yet; the `_fail` helper raises).

- [ ] **Step 3: Implement the guard**

In `main.py`, add this function right after `_current_scan_label` (after line 112):

```python
def _scan_already_ran_today(scan: str, now: datetime.datetime) -> bool:
    """True if the journal already has an entry logged for this scan label
    on today's date. Guards against GitHub Actions firing the same scan
    slot's cron trigger more than once within config.SCAN_TOLERANCE_MINUTES
    (both DST-offset cron lines can land inside the tolerance window on the
    same day -- see the Aug 7 duplicate-alert incident)."""
    today = now.date()
    for alert in journal.load_journal():
        if alert["scan"] != scan:
            continue
        if datetime.datetime.fromisoformat(alert["timestamp"]).date() == today:
            return True
    return False
```

Then in `run_scan`, right after `scan = _current_scan_label(now)` (line 132) and before the `logger.info("Resolving outcomes...")` line, add:

```python
    scan = _current_scan_label(now)

    if _scan_already_ran_today(scan, now):
        logger.info("%s scan already logged today (%s); skipping duplicate run.", scan, now.date())
        return {"setups": [], "digest": None}

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main.py -v`
Expected: PASS — including the two new tests and every pre-existing `test_run_scan_*` test (`test_run_scan_exits_when_outside_tolerance`, `test_run_scan_force_run_bypasses_tolerance_but_not_weekday`, `test_run_scan_force_run_reaches_pipeline_outside_tolerance`, `test_run_scan_wires_market_context_into_build_digest`) unchanged, since those already patch `journal.load_journal` to return `[]` or exit before the guard is reached.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS, no regressions elsewhere.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "Fix duplicate scan runs: skip pipeline if this scan slot already logged today"
```

---

### Task 2: Defense-in-depth dedup in `journal.py::log_alerts()`

**Files:**
- Modify: `journal.py:40-73`
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `journal.log_alerts` unchanged signature (`setups: list[dict], scan: str, now: datetime.datetime -> list[dict]`), but now skips setups whose `(ticker, scan)` already has a journal record dated `now.date()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_journal.py`, in the `-- log_alerts --` section:

```python
def test_log_alerts_skips_ticker_already_logged_same_scan_same_day(monkeypatch):
    existing = [_alert(
        id="2026-07-22T06:00-NVDA", timestamp="2026-07-22T06:00:00",
        scan="premarket", ticker="NVDA",
    )]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": list(existing))
    saved = {}
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": saved.setdefault("alerts", alerts))

    setups = [
        {"ticker": "NVDA", "bias": "short", "conviction": 6, "entry": 90.0,
         "stop": 95.0, "target": 80.0, "rr": 2.0, "horizon": "intraday"},
        {"ticker": "AMD", "bias": "long", "conviction": 7, "entry": 50.0,
         "stop": 47.0, "target": 56.0, "rr": 2.0, "horizon": "swing"},
    ]
    now = datetime.datetime(2026, 7, 22, 6, 38)  # later, same day, same scan slot

    result = journal.log_alerts(setups, "premarket", now)

    assert [r["ticker"] for r in result] == ["AMD"]
    assert len(saved["alerts"]) == 2  # original NVDA record + new AMD, no duplicate NVDA


def test_log_alerts_allows_same_ticker_different_scan_same_day(monkeypatch):
    existing = [_alert(
        id="2026-07-22T06:00-NVDA", timestamp="2026-07-22T06:00:00",
        scan="premarket", ticker="NVDA",
    )]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": list(existing))
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": None)

    setups = [{"ticker": "NVDA", "bias": "long", "conviction": 8, "entry": 100.0,
               "stop": 95.0, "target": 110.0, "rr": 2.0, "horizon": "swing"}]
    now = datetime.datetime(2026, 7, 22, 12, 30)  # same day, preclose slot

    result = journal.log_alerts(setups, "preclose", now)

    assert [r["ticker"] for r in result] == ["NVDA"]


def test_log_alerts_allows_same_ticker_scan_different_day(monkeypatch):
    existing = [_alert(
        id="2026-07-21T06:00-NVDA", timestamp="2026-07-21T06:00:00",
        scan="premarket", ticker="NVDA",
    )]
    monkeypatch.setattr(journal, "load_journal", lambda path="journal.json": list(existing))
    monkeypatch.setattr(journal, "save_journal", lambda alerts, path="journal.json": None)

    setups = [{"ticker": "NVDA", "bias": "long", "conviction": 8, "entry": 100.0,
               "stop": 95.0, "target": 110.0, "rr": 2.0, "horizon": "swing"}]
    now = datetime.datetime(2026, 7, 22, 6, 0)  # next day, same scan slot

    result = journal.log_alerts(setups, "premarket", now)

    assert [r["ticker"] for r in result] == ["NVDA"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_journal.py -k skips_ticker_already_logged -v`
Expected: FAIL — `result` currently contains both NVDA and AMD (no dedup exists yet), so `[r["ticker"] for r in result] == ["AMD"]` fails.

- [ ] **Step 3: Implement the dedup**

Replace `log_alerts` in `journal.py` (lines 40-73) with:

```python
def log_alerts(setups: list[dict], scan: str, now: datetime.datetime) -> list[dict]:
    """Build one alert record per setup, append to the persisted journal,
    and return the newly created records. Caller is responsible for
    filtering setups to conviction >= config.MIN_CONVICTION and capping at
    config.MAX_ALERTS before calling this.

    Skips any setup whose (ticker, scan) combination is already logged for
    today's date -- defense-in-depth against duplicate pipeline runs (the
    primary guard lives in main.py::run_scan)."""
    existing = load_journal()
    today = now.date()
    already_logged = {
        (a["ticker"], a["scan"])
        for a in existing
        if datetime.datetime.fromisoformat(a["timestamp"]).date() == today
    }

    new_records = []
    for setup in setups:
        ticker = setup["ticker"]
        if (ticker, scan) in already_logged:
            logger.info("Skipping duplicate log_alerts entry for %s/%s on %s", ticker, scan, today)
            continue

        record = {
            "id": _alert_id(now, ticker),
            "timestamp": now.isoformat(),
            "scan": scan,
            "ticker": ticker,
            "bias": setup["bias"],
            "conviction": setup["conviction"],
            "entry": setup["entry"],
            "stop": setup["stop"],
            "target": setup["target"],
            "rr": setup["rr"],
            "horizon": setup["horizon"],
            "alerted": True,
            "status": "open",
            "position": None,
            "resolved_at": None,
            "outcome": None,
            "bars_open": 0,
        }
        new_records.append(record)

    existing.extend(new_records)
    save_journal(existing)

    return new_records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_journal.py -v`
Expected: PASS — all new tests plus `test_log_alerts_builds_records` (existing test, still passes since it patches `load_journal` to return `[]`, so `already_logged` is empty and both setups still log).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add journal.py tests/test_journal.py
git commit -m "Fix duplicate journal entries: dedup log_alerts by (ticker, scan, date)"
```

---

### Task 3: Clean up existing duplicate records in `journal.json`

**Context:** Tasks 1-2 stop new duplicates. They don't fix the ~360 duplicate records already sitting in the live, CI-pushed `journal.json` from Aug 7 - Aug 13 (confirmed during diagnosis: 90/90/60/60/90 alerts logged those days vs. the normal 30/day). Those duplicates permanently skew `compute_stats()` (double-counted wins/losses/scratches) until removed.

**Files:**
- Create: `scripts/dedupe_journal.py`
- Test: `tests/test_dedupe_journal.py`

**Interfaces:**
- Produces: `scripts.dedupe_journal.dedupe_alerts(alerts: list[dict]) -> tuple[list[dict], list[dict]]` — returns `(kept, dropped)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dedupe_journal.py`:

```python
import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dedupe_journal


def _alert(**overrides):
    base = {
        "id": "x", "timestamp": "2026-08-07T06:38:18-07:00", "scan": "premarket",
        "ticker": "VEEV", "bias": "long", "conviction": 7, "entry": 100.0,
        "stop": 95.0, "target": 110.0, "rr": 2.0, "horizon": "swing",
        "alerted": True, "status": "open", "position": None,
        "resolved_at": None, "outcome": None, "bars_open": 0,
    }
    base.update(overrides)
    return base


def test_dedupe_keeps_single_record_untouched():
    alerts = [_alert(id="a1")]
    kept, dropped = dedupe_journal.dedupe_alerts(alerts)
    assert kept == alerts
    assert dropped == []


def test_dedupe_drops_duplicate_open_records_keeps_first():
    first = _alert(id="a1", timestamp="2026-08-07T06:38:18-07:00", conviction=6)
    second = _alert(id="a2", timestamp="2026-08-07T13:23:25-07:00", conviction=8)
    kept, dropped = dedupe_journal.dedupe_alerts([first, second])
    assert kept == [first]
    assert dropped == [second]


def test_dedupe_prefers_closed_record_over_open_duplicate():
    open_record = _alert(id="a1", timestamp="2026-08-07T06:38:18-07:00", status="open")
    closed_record = _alert(id="a2", timestamp="2026-08-07T13:23:25-07:00",
                            status="closed", outcome="win", resolved_at="2026-08-07T14:00:00-07:00")
    kept, dropped = dedupe_journal.dedupe_alerts([open_record, closed_record])
    assert kept == [closed_record]
    assert dropped == [open_record]


def test_dedupe_does_not_touch_different_ticker_scan_or_day():
    a = _alert(id="a1", ticker="VEEV", scan="premarket", timestamp="2026-08-07T06:38:18-07:00")
    b = _alert(id="a2", ticker="CRWD", scan="premarket", timestamp="2026-08-07T06:38:18-07:00")
    c = _alert(id="a3", ticker="VEEV", scan="preclose", timestamp="2026-08-07T12:39:59-07:00")
    d = _alert(id="a4", ticker="VEEV", scan="premarket", timestamp="2026-08-10T06:43:20-07:00")
    kept, dropped = dedupe_journal.dedupe_alerts([a, b, c, d])
    assert kept == [a, b, c, d]
    assert dropped == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dedupe_journal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dedupe_journal'` (script doesn't exist yet).

- [ ] **Step 3: Write the script**

Create `scripts/dedupe_journal.py`:

```python
"""One-off cleanup for the Aug 7-13 duplicate-scan-run incident (see
docs/superpowers/plans/2026-08-14-fix-duplicate-scan-runs.md). Groups
journal records by (ticker, scan, calendar date of timestamp) and keeps a
single canonical record per group:

  1. If any record in the group is closed, keep the earliest-resolved
     closed one (preserves real resolution data instead of discarding it).
  2. Otherwise keep the earliest-logged open one.

Run with no flags for a dry-run report. Pass --write to actually rewrite
the journal file (defaults to journal.json in the current directory).
"""
import argparse
import datetime
import json
import sys


def _group_key(alert: dict) -> tuple[str, str, str]:
    date = datetime.datetime.fromisoformat(alert["timestamp"]).date().isoformat()
    return (alert["ticker"], alert["scan"], date)


def _pick_canonical(group: list[dict]) -> dict:
    closed = [a for a in group if a["status"] == "closed"]
    pool = closed if closed else group
    return min(pool, key=lambda a: a["timestamp"])


def dedupe_alerts(alerts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (kept, dropped), each a list of the original dict objects,
    order-preserving relative to the input list."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for alert in alerts:
        groups.setdefault(_group_key(alert), []).append(alert)

    canonical_ids = set()
    for group in groups.values():
        canonical = _pick_canonical(group)
        canonical_ids.add(id(canonical))

    kept = [a for a in alerts if id(a) in canonical_ids]
    dropped = [a for a in alerts if id(a) not in canonical_ids]
    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="journal.json")
    parser.add_argument("--write", action="store_true", help="Rewrite the file (default: dry-run report only)")
    args = parser.parse_args()

    with open(args.path) as f:
        alerts = json.load(f)

    kept, dropped = dedupe_alerts(alerts)

    print(f"Total records: {len(alerts)}")
    print(f"Kept: {len(kept)}")
    print(f"Dropped: {len(dropped)}")
    for a in dropped:
        print(f"  DROP {a['id']} ({a['ticker']}/{a['scan']}, {a['timestamp']}, status={a['status']})")

    if args.write:
        with open(args.path, "w") as f:
            json.dump(kept, f, indent=2)
        print(f"Wrote {len(kept)} records to {args.path}")
    else:
        print("Dry run only -- pass --write to rewrite the file.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dedupe_journal.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit the script and test (NOT a journal.json change yet)**

```bash
git add scripts/dedupe_journal.py tests/test_dedupe_journal.py
git commit -m "Add dedupe_journal.py: one-off cleanup for Aug 7-13 duplicate scan records"
```

- [ ] **Step 7: STOP — do not run this against the real `journal.json` or push automatically**

`journal.json` is shared, CI-managed state that the running production workflow also reads (`resolve_open_alerts` matches against `open` records every scan) and pushes to `origin/main` after every run. Rewriting it is a hard-to-reverse action against shared state.

Report back to the user with the dry-run output (`python scripts/dedupe_journal.py` with no `--write`, against the real `journal.json`) and get explicit confirmation before running `--write` and before committing/pushing the result. Do not commit or push the cleaned `journal.json` without that confirmation.

---

## Self-Review

- **Spec coverage:** Q1 (double-fire) → Task 1's guard. Q2 (`log_alerts` unconditional append) → Task 2. Q3 (`build_daily_report` no dedup) → not fixed directly: Tasks 1-2 prevent future duplicate journal entries, so `_fired_today()` never sees dupes going forward; no code change needed there once the journal stays clean. Q4 (what changed Aug 6-7) → already answered in diagnosis, informs Task 3's cleanup date range (Aug 7-13) and the "don't touch scan.yml/tolerance" constraint.
- **Placeholder scan:** none found — every step has runnable code.
- **Type consistency:** `_scan_already_ran_today(scan: str, now: datetime.datetime) -> bool` matches its one call site in `run_scan`. `dedupe_alerts(alerts: list[dict]) -> tuple[list[dict], list[dict]]` matches its use in `main()` and in all four tests.
