"""Tests for the v2 dip-buy interface stub.

The stub's whole job is to hold a shape and refuse to run, so that is what is
tested: it satisfies the Strategy protocol, its constructor already accepts the
earnings calendar that exists, and ``generate`` raises an error that names all
three inputs rather than quietly returning nothing.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.earnings import EarningsCalendar
from backtest.strategies.v2_dipbuy import (
    DAYS_AFTER_EARNINGS,
    DAYS_BEFORE_EARNINGS,
    REQUIRED_INPUTS,
    V2DipBuy,
)
from backtest.strategy import Strategy


def test_satisfies_strategy_protocol():
    assert isinstance(V2DipBuy(), Strategy)
    assert V2DipBuy().name == "v2_dipbuy"


def test_generate_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        V2DipBuy().generate(view=None, universe=["AAPL"])


def test_error_names_all_three_future_inputs():
    with pytest.raises(NotImplementedError) as exc:
        V2DipBuy().generate(view=None, universe=["AAPL"])
    msg = str(exc.value)
    assert "price" in msg
    assert "in_blackout" in msg
    assert "revenue_trend" in msg
    assert len(REQUIRED_INPUTS) == 3


def test_accepts_the_earnings_calendar_that_already_exists():
    cal = EarningsCalendar({"AAPL": [date(2026, 4, 30)]})
    strat = V2DipBuy(earnings=cal)
    assert strat.earnings is cal
    assert strat.fundamentals is None
    # The intended call site must actually work against today's EarningsCalendar.
    assert cal.in_blackout(
        "AAPL", date(2026, 4, 28),
        days_before=DAYS_BEFORE_EARNINGS, days_after=DAYS_AFTER_EARNINGS,
    )


def test_docstring_records_the_intended_call_sites():
    from backtest.strategies import v2_dipbuy

    doc = v2_dipbuy.__doc__
    assert "EarningsCalendar.in_blackout" in doc
    assert "FundamentalsView.revenue_trend" in doc
    assert "frames_for" in doc
