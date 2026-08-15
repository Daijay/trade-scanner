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
    if closed:
        return min(closed, key=lambda a: (a["resolved_at"] is None, a["resolved_at"] or "", a["timestamp"]))
    return min(group, key=lambda a: a["timestamp"])


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
