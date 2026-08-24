# backtest/strategy.py
"""The strategy plug-in interface: :class:`Signal` and the :class:`Strategy` protocol.

The engine's only contract with a strategy is one method::

    signals = strategy.generate(view, universe)

``view`` is a :class:`~backtest.pit.PointInTimeView` frozen at the simulated
scan instant, and it is the *only* way a strategy is given prices. A strategy
that reaches around it — reading parquet directly, importing ``yfinance``,
holding state from a later slot — breaks the point-in-time guarantee that the
whole engine exists to provide.

Why ``Signal``'s fields are what they are
-----------------------------------------
They are exactly the setup keys ``journal.log_alerts`` reads, verified against
``journal.py``: ``ticker``, ``bias``, ``conviction``, ``entry``, ``stop``,
``target``, ``rr``, ``horizon``. :meth:`Signal.as_setup` therefore produces a
dict ``journal.log_alerts`` accepts unchanged, and the records it writes are
resolved by ``journal.resolve_alert`` and scored by ``journal.compute_stats``
through the identical code path the live scanner uses. That identity is the
entire reason the backtest's numbers are comparable to the live journal's, so
these names are not free to drift.

``reason`` is the one extra field. It carries the strategy's own explanation of
the signal and is ignored by ``journal.py``; it exists for the report and for
anyone auditing why a given name fired.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backtest.pit import PointInTimeView

#: The setup keys ``journal.log_alerts`` reads out of each dict it is handed.
#: Asserted against in ``tests/backtest/test_strategy.py`` so a change in
#: ``journal.py`` fails loudly here rather than silently producing alerts with
#: missing fields.
JOURNAL_SETUP_KEYS: tuple[str, ...] = (
    "ticker",
    "bias",
    "conviction",
    "entry",
    "stop",
    "target",
    "rr",
    "horizon",
)

#: Accepted values for ``Signal.bias``. ``journal.resolve_alert`` branches on
#: ``bias == "long"`` and treats everything else as a short, so a typo would
#: silently resolve a long as a short.
BIASES: tuple[str, ...] = ("long", "short")


@dataclass(frozen=True)
class Signal:
    """One setup proposed by a strategy at one simulated scan instant.

    Frozen: a signal is a record of what was decided with the information
    available at ``view.as_of``. Nothing downstream — resolution least of all —
    is allowed to edit it after the fact.
    """

    ticker: str
    bias: str
    entry: float
    stop: float
    target: float
    rr: float
    horizon: str
    conviction: int
    reason: str

    def __post_init__(self) -> None:
        if self.bias not in BIASES:
            raise ValueError(f"bias must be one of {BIASES}, got {self.bias!r}")
        if self.bias == "long":
            ok = self.stop < self.entry < self.target
        else:
            ok = self.target < self.entry < self.stop
        if not ok:
            raise ValueError(
                f"{self.ticker}: {self.bias} geometry requires "
                + ("stop < entry < target" if self.bias == "long" else "target < entry < stop")
                + f", got stop={self.stop!r} entry={self.entry!r} target={self.target!r}"
            )

    def as_setup(self) -> dict:
        """This signal as a setup dict ``journal.log_alerts`` accepts unchanged.

        Every key in :data:`JOURNAL_SETUP_KEYS` is present; ``reason`` rides
        along and is ignored by ``journal.py``.
        """
        return asdict(self)


@runtime_checkable
class Strategy(Protocol):
    """What the engine requires of a strategy.

    ``runtime_checkable`` so ``isinstance(obj, Strategy)`` is available to
    tests. Note that this only checks the *presence* of the members, not their
    signatures — it is a smoke check, not a proof.
    """

    name: str

    def generate(self, view: "PointInTimeView", universe: list[str]) -> list[Signal]:
        """Signals for one scan slot.

        Must read prices only through *view*, must not raise because a single
        ticker in *universe* is missing or malformed (one bad name must never
        cost a whole scan slot), and must return an empty list rather than
        ``None`` when nothing qualifies.
        """
        ...


__all__ = ["Signal", "Strategy", "JOURNAL_SETUP_KEYS", "BIASES"]
