# backtest/strategies/v2_dipbuy.py
"""v2 dip-buy: **interface stub only**. ``generate`` raises ``NotImplementedError``.

This file exists to fix the shape of the second strategy before its rules are
written, so adding v2 later is an edit to one module rather than a reshaping of
the engine. It deliberately contains no logic: v2's rules do not exist yet, and
building a data layer for rules nobody has decided on would be speculative work
that later has to be undone.

Intended thesis
---------------
v1 buys strength — it wants three timeframes agreeing and rewards names sitting
at the edge of their 20-day range. v2 is the complement: buy a *pullback within
an intact uptrend* in a business whose fundamentals are still improving, and
stand aside when the pullback is really the market front-running an earnings
print.

Three inputs, and where each comes from
---------------------------------------
1. **Price** — via ``PointInTimeView`` exactly as v1 does, no other source::

       frames = view.frames_for(ticker)   # {'30m', '4h', 'daily'}

   Intended criteria, all on the daily frame:

   - trend intact: ``close > ema200`` and ``ema50 > ema200``;
   - actually a dip: ``rsi14`` below a threshold (~40) while the trend filter
     above still holds, and ``close`` pulled back into the lower half of the
     20-day range rather than sitting at the high — the inverse of
     ``score_survivor``'s range-edge term;
   - the pullback is not a breakdown: ``close`` still above ``ema50``, or above
     a measured retracement of the prior swing.

2. **Earnings blackout** — via ``EarningsCalendar.in_blackout``
   (:mod:`backtest.earnings`), already built (Task 3)::

       if self.earnings.in_blackout(ticker, view.as_of.date(),
                                    days_before=DAYS_BEFORE_EARNINGS,
                                    days_after=DAYS_AFTER_EARNINGS):
           continue

   A dip into a print is a different trade with a different distribution, and
   mixing the two would make the strategy's hit rate uninterpretable. Note the
   documented limit: the earnings cache holds the schedule *as known today*,
   not as known at ``view.as_of``.

3. **Fundamentals trend** — ``FundamentalsView.revenue_trend``, which **does
   not exist yet**::

       if self.fundamentals.revenue_trend(ticker, view.as_of) <= 0:
           continue

   Intended contract: point-in-time quarterly revenue and earnings, truncated
   at ``as_of`` the same way price is, returning a growth slope over the last
   N reported quarters. It must respect *report* dates, not *period* dates —
   Q1 revenue is not knowable on Mar 31, only on the day it is filed — which is
   why it needs its own module rather than a column bolted onto the bar cache.

Sizing, once the rules exist
----------------------------
Stops from the daily ``atr14`` and targets from ``config.MIN_RR``, matching
:mod:`backtest.strategies.v1_technical`, so v1 and v2 hit rates are comparable
rather than reflecting two different stop conventions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backtest.strategy import Signal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backtest.earnings import EarningsCalendar
    from backtest.pit import PointInTimeView

#: Blackout window intended for :meth:`backtest.earnings.EarningsCalendar.in_blackout`.
DAYS_BEFORE_EARNINGS = 3
DAYS_AFTER_EARNINGS = 1

#: The three inputs ``generate`` will consume. Named in the ``NotImplementedError``
#: so the message says what is missing, not merely that something is.
REQUIRED_INPUTS: tuple[str, ...] = (
    "price (PointInTimeView.frames_for)",
    "earnings blackout (EarningsCalendar.in_blackout)",
    "fundamentals trend (FundamentalsView.revenue_trend, not yet built)",
)


class V2DipBuy:
    """Interface stub. Satisfies :class:`~backtest.strategy.Strategy`; generates nothing."""

    name = "v2_dipbuy"

    def __init__(self, earnings: "EarningsCalendar | None" = None, fundamentals=None):
        #: Already available (Task 3). Wired in now so the constructor does not
        #: change shape when the rules land.
        self.earnings = earnings
        #: Reserved for ``FundamentalsView``. There is no implementation to
        #: pass yet; the parameter exists so the call site is fixed.
        self.fundamentals = fundamentals

    def generate(self, view: "PointInTimeView", universe: list[str]) -> list[Signal]:
        """Not implemented. Raises, naming the three inputs it will need.

        Raising is deliberate: returning ``[]`` would let v2 be run by mistake
        and report a clean zero-signal backtest, which reads as "the strategy
        found nothing" rather than "the strategy does not exist".
        """
        raise NotImplementedError(
            f"{self.name} is an interface stub with no rules yet. It will consume "
            + "; ".join(REQUIRED_INPUTS)
            + ". See the module docstring for the intended criteria and call sites."
        )


__all__ = ["V2DipBuy", "REQUIRED_INPUTS", "DAYS_BEFORE_EARNINGS", "DAYS_AFTER_EARNINGS"]
