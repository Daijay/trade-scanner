# tests/backtest/test_download_resume.py
"""Resume must compare the requested start against the file's *actual* first bar.

Task 2b widened the cache window backwards to give the indicators their warm-up
history (200 daily periods for ``ema200``, ~200 derived 4h buckets). Every
parquet already on disk covered only the old, narrower window. A resume
predicate that treated "the file exists" — or "some previous run recorded this
ticker" — as "already fetched" would have skipped the entire universe and left
the cache exactly as short as it was.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import download


@pytest.fixture()
def narrow_file(tmp_path):
    """A cached file covering 2026-01-02 .. 2026-08-24 only."""
    path = tmp_path / "AAPL.parquet"
    idx = pd.date_range("2026-01-02", "2026-08-24", freq="D", name="timestamp")
    pd.DataFrame({"Close": 1.0}, index=idx).to_parquet(path)
    return path


NARROW = {
    "tickers": {
        "AAPL": {
            "daily": {"requested_start": "2026-01-01", "requested_end": "2026-08-25"}
        }
    }
}


def test_narrow_cache_is_not_complete_for_a_wider_request(narrow_file):
    """The core Task 2b bug: the file exists but starts two years too late."""
    assert not download._is_complete(
        narrow_file,
        "AAPL",
        "daily",
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2026-08-25"),
        {},
    )


def test_narrow_manifest_entry_does_not_satisfy_a_wider_request(narrow_file):
    """A previously *recorded* narrow range must not short-circuit a wider one."""
    assert not download._is_complete(
        narrow_file,
        "AAPL",
        "daily",
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2026-08-25"),
        NARROW,
    )


def test_same_request_still_resumes(narrow_file):
    """Resumability is not sacrificed: an unchanged request still skips."""
    assert download._is_complete(
        narrow_file,
        "AAPL",
        "daily",
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-08-25"),
        {},
    )


def test_wider_recorded_range_satisfies_a_narrower_request(narrow_file):
    """Containment, not equality: a recorded 2024 fetch covers a 2026 request.

    This is the escape hatch for tickers whose history legitimately begins
    after the requested start (recent IPOs, renamed symbols) — the vendor has
    nothing earlier, so the first-bar test can never pass and refetching every
    run would be pure waste.
    """
    wide = {
        "tickers": {
            "AAPL": {
                "daily": {"requested_start": "2024-01-01", "requested_end": "2026-08-25"}
            }
        }
    }
    assert download._is_complete(
        narrow_file,
        "AAPL",
        "daily",
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-08-25"),
        wide,
    )


def test_missing_file_is_never_complete(tmp_path):
    assert not download._is_complete(
        tmp_path / "NOPE.parquet",
        "NOPE",
        "daily",
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2026-08-25"),
        NARROW,
    )
