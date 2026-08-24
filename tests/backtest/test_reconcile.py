"""Tests for backtest.reconcile — cross-frame price reconciliation.

The 30m cache (hfdatalibrary) is *not* back-adjusted for corporate actions; the
daily cache (yfinance) *is*. These tests pin the behaviour that puts both frames
on one price scale.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.reconcile import daily_ratio, detect_segments, reconcile_30m, reconcile_all


# --------------------------------------------------------------------------
# synthetic frame builders
# --------------------------------------------------------------------------

BARS_PER_DAY = 13  # 09:30 .. 15:30 inclusive, 30-minute steps


def _dates(n: int, start: str = "2026-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def _make_daily(closes: pd.Series) -> pd.DataFrame:
    """A daily frame whose Close is *closes* and whose OHLC bracket it."""
    return pd.DataFrame(
        {
            "Open": closes * 0.99,
            "High": closes * 1.01,
            "Low": closes * 0.98,
            "Close": closes,
            "Volume": np.full(len(closes), 1_000_000, dtype="int64"),
        },
        index=pd.DatetimeIndex(closes.index, name="timestamp"),
    )


def _make_30m(daily_closes: pd.Series, scale: pd.Series) -> pd.DataFrame:
    """A 30m frame that aggregates to ``daily_closes * scale`` per day.

    ``scale`` is the per-date price-scale error we want the reconciler to undo.
    Each day gets ``BARS_PER_DAY`` bars; the last bar's Close is the day's close.
    """
    rows = []
    index = []
    for date, close in daily_closes.items():
        factor = float(scale.loc[date])
        day_close = close * factor
        for i in range(BARS_PER_DAY):
            # intraday drift so the bars are not degenerate; last bar hits day_close
            wobble = 1.0 + 0.001 * (BARS_PER_DAY - 1 - i)
            c = day_close * wobble
            rows.append(
                {
                    "Open": c * 0.999,
                    "High": c * 1.002,
                    "Low": c * 0.997,
                    "Close": c,
                    # raw (unadjusted) volume moves inversely with the price scale
                    "Volume": float(round(10_000 / factor)),
                    "source": "iex",
                }
            )
            index.append(date + pd.Timedelta(minutes=30 * i) + pd.Timedelta(hours=9, minutes=30))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


def _flat_scale(dates: pd.DatetimeIndex, value: float = 1.0) -> pd.Series:
    return pd.Series(value, index=dates)


def _split_scale(dates: pd.DatetimeIndex, cut: int, before: float, after: float = 1.0) -> pd.Series:
    s = pd.Series(after, index=dates)
    s.iloc[:cut] = before
    return s


def _pair(scale: pd.Series, base: float = 100.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a (30m, daily) pair whose ratio follows *scale*."""
    dates = pd.DatetimeIndex(scale.index)
    # a mildly trending daily close so we are not testing on a constant
    closes = pd.Series(base * (1 + 0.002 * np.arange(len(dates))), index=dates)
    return _make_30m(closes, scale), _make_daily(closes)


# --------------------------------------------------------------------------
# daily_ratio
# --------------------------------------------------------------------------


def test_daily_ratio_recovers_the_scale_error():
    dates = _dates(40)
    scale = _split_scale(dates, cut=15, before=24.94)
    h30, daily = _pair(scale)

    ratio = daily_ratio(h30, daily)

    assert len(ratio) == 40
    assert ratio.iloc[0] == pytest.approx(24.94, rel=1e-6)
    assert ratio.iloc[-1] == pytest.approx(1.0, rel=1e-6)


# --------------------------------------------------------------------------
# detect_segments
# --------------------------------------------------------------------------


def test_detect_segments_finds_the_split_boundary():
    dates = _dates(60)
    ratio = pd.Series(_split_scale(dates, cut=25, before=24.94).values, index=dates)

    segments = detect_segments(ratio)

    assert len(segments) == 2
    (s0, e0, f0), (s1, e1, f1) = segments
    assert s0 == dates[0]
    assert e0 == dates[24]
    assert f0 == pytest.approx(24.94)
    assert s1 == dates[25]
    assert e1 == dates[-1]
    assert f1 == pytest.approx(1.0)


def test_single_day_outlier_does_not_create_a_segment():
    """One bad last-trade print must not be mistaken for a corporate action."""
    dates = _dates(60)
    ratio = pd.Series(1.0, index=dates)
    ratio.iloc[30] = 1.35  # isolated spike, well beyond jump_tol

    segments = detect_segments(ratio)

    assert len(segments) == 1, f"spurious segmentation: {segments}"
    assert segments[0][2] == pytest.approx(1.0)


def test_clean_series_is_one_segment():
    dates = _dates(60)
    rng = np.random.default_rng(0)
    ratio = pd.Series(1.0 + rng.normal(0, 0.001, len(dates)), index=dates)

    segments = detect_segments(ratio)

    assert len(segments) == 1
    assert segments[0][2] == pytest.approx(1.0, abs=0.005)


# --------------------------------------------------------------------------
# reconcile_30m
# --------------------------------------------------------------------------


def test_bkng_shape_25_to_1_split_is_corrected_flat():
    dates = _dates(80)
    scale = _split_scale(dates, cut=30, before=24.94)
    h30, daily = _pair(scale)

    # precondition: the defect is present
    before = daily_ratio(h30, daily)
    assert before.max() / before.min() > 20

    fixed, report = reconcile_30m(h30, daily)
    after = daily_ratio(fixed, daily)

    assert len(report["segments"]) == 2
    assert np.allclose(after.values, 1.0, atol=1e-6), after.describe()
    assert report["max_residual"] < 1e-6


def test_reverse_split_is_corrected():
    dates = _dates(80)
    scale = _split_scale(dates, cut=30, before=0.3333)
    h30, daily = _pair(scale)

    fixed, report = reconcile_30m(h30, daily)
    after = daily_ratio(fixed, daily)

    assert len(report["segments"]) == 2
    assert report["segments"][0][2] == pytest.approx(0.3333, rel=1e-6)
    assert np.allclose(after.values, 1.0, atol=1e-6)


def test_clean_ticker_is_left_materially_unchanged():
    dates = _dates(80)
    h30, daily = _pair(_flat_scale(dates, 1.0))

    fixed, report = reconcile_30m(h30, daily)

    assert len(report["segments"]) == 1
    assert report["segments"][0][2] == pytest.approx(1.0, rel=1e-9)
    pd.testing.assert_frame_equal(fixed, h30, check_exact=False, rtol=1e-9)


def test_single_day_outlier_does_not_split_the_correction():
    dates = _dates(80)
    scale = _flat_scale(dates, 1.0)
    scale.iloc[40] = 1.4
    h30, daily = _pair(scale)

    fixed, report = reconcile_30m(h30, daily)

    assert len(report["segments"]) == 1
    after = daily_ratio(fixed, daily)
    # the outlier day itself stays an outlier — it is bad data, not a scale error
    clean_days = after.drop(after.index[40])
    assert np.allclose(clean_days.values, 1.0, atol=1e-6)


def test_volume_is_scaled_inversely_so_notional_is_preserved():
    dates = _dates(80)
    scale = _split_scale(dates, cut=30, before=25.0)
    h30, daily = _pair(scale)

    notional_before = (h30["Close"] * h30["Volume"]).sum()
    fixed, _ = reconcile_30m(h30, daily)
    notional_after = (fixed["Close"] * fixed["Volume"]).sum()

    assert notional_after == pytest.approx(notional_before, rel=1e-9)

    # and the volume level is continuous across the boundary
    pre = fixed.loc[fixed.index.normalize() == dates[29], "Volume"].mean()
    post = fixed.loc[fixed.index.normalize() == dates[30], "Volume"].mean()
    assert post == pytest.approx(pre, rel=0.01)


def test_input_frame_is_not_mutated():
    dates = _dates(60)
    scale = _split_scale(dates, cut=20, before=25.0)
    h30, daily = _pair(scale)
    snapshot = h30.copy(deep=True)

    fixed, _ = reconcile_30m(h30, daily)

    pd.testing.assert_frame_equal(h30, snapshot)
    assert fixed is not h30
    assert not np.shares_memory(fixed["Close"].to_numpy(), h30["Close"].to_numpy())


def test_non_price_columns_survive():
    dates = _dates(40)
    h30, daily = _pair(_split_scale(dates, cut=20, before=4.0))

    fixed, _ = reconcile_30m(h30, daily)

    assert list(fixed.columns) == list(h30.columns)
    assert (fixed["source"] == "iex").all()
    assert fixed.index.equals(h30.index)


def test_report_shape():
    dates = _dates(60)
    h30, daily = _pair(_split_scale(dates, cut=20, before=5.0))

    _, report = reconcile_30m(h30, daily)

    assert set(report) >= {"segments", "factors", "max_residual", "n_segments"}
    assert report["n_segments"] == 2
    assert report["factors"] == pytest.approx([5.0, 1.0])


# --------------------------------------------------------------------------
# reconcile_all
# --------------------------------------------------------------------------


def _build_cache(root, spec: dict[str, pd.Series]) -> None:
    (root / "ohlcv_30m").mkdir(parents=True)
    (root / "ohlcv_daily").mkdir(parents=True)
    for ticker, scale in spec.items():
        h30, daily = _pair(scale)
        h30.to_parquet(root / "ohlcv_30m" / f"{ticker}.parquet")
        daily.to_parquet(root / "ohlcv_daily" / f"{ticker}.parquet")


def test_reconcile_all_writes_adjusted_copies_and_preserves_raw(tmp_path):
    dates = _dates(60)
    _build_cache(
        tmp_path,
        {
            "SPLT": _split_scale(dates, cut=25, before=25.0),
            "CLEAN": _flat_scale(dates, 1.0),
        },
    )
    raw_before = {
        t: pd.read_parquet(tmp_path / "ohlcv_30m" / f"{t}.parquet") for t in ("SPLT", "CLEAN")
    }

    results = reconcile_all(tmp_path)

    assert set(results) == {"SPLT", "CLEAN"}
    assert results["SPLT"]["n_segments"] == 2
    assert results["CLEAN"]["n_segments"] == 1

    # the raw download is preserved byte-for-byte in value terms
    for ticker, before in raw_before.items():
        pd.testing.assert_frame_equal(
            pd.read_parquet(tmp_path / "ohlcv_30m" / f"{ticker}.parquet"), before
        )

    # corrected copies land in the sibling directory and are on the daily scale
    adj = pd.read_parquet(tmp_path / "ohlcv_30m_adj" / "SPLT.parquet")
    daily = pd.read_parquet(tmp_path / "ohlcv_daily" / "SPLT.parquet")
    assert np.allclose(daily_ratio(adj, daily).values, 1.0, atol=1e-6)


def test_reconcile_all_writes_a_report_csv(tmp_path):
    dates = _dates(60)
    _build_cache(tmp_path, {"SPLT": _split_scale(dates, cut=25, before=25.0)})

    reconcile_all(tmp_path)

    report = pd.read_csv(tmp_path / "_reconcile_report.csv")
    assert list(report["ticker"]) == ["SPLT"]
    row = report.iloc[0]
    assert row["status"] == "reconciled"
    assert row["n_segments"] == 2
    assert row["boundaries"] == str(dates[25].date())
    assert row["pre_drift_pct"] > 100
    assert row["post_drift_pct"] < 0.01


def test_reconcile_all_flags_a_ticker_with_no_daily_counterpart(tmp_path):
    dates = _dates(60)
    _build_cache(tmp_path, {"SPLT": _split_scale(dates, cut=25, before=25.0)})
    (tmp_path / "ohlcv_daily" / "SPLT.parquet").unlink()

    results = reconcile_all(tmp_path)

    assert results["SPLT"]["status"] == "no_daily"
    # copied through untouched rather than silently trusted
    pd.testing.assert_frame_equal(
        pd.read_parquet(tmp_path / "ohlcv_30m_adj" / "SPLT.parquet"),
        pd.read_parquet(tmp_path / "ohlcv_30m" / "SPLT.parquet"),
    )
