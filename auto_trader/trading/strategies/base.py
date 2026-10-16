"""Inputs and results shared by signal strategies and the trading engine."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Literal, Protocol, Sequence


@dataclass(frozen=True)
class SignalContext:
    history: Sequence[Decimal]
    price: Decimal
    short_period: int
    long_period: int
    previous_trend: str | None
    entry_armed: bool
    confirmation_count: int
    new_bar: bool
    # Lazy account access preserves the original short-circuit evaluation order.
    position_quantity: Callable[[], int]


@dataclass(frozen=True)
class StrategySignal:
    short_average: Decimal | None
    long_average: Decimal | None
    trend: str
    entry_armed: bool
    confirmation_count: int
    decision: str
    action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    rising: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ExitSignalContext:
    history: Sequence[Decimal]
    short_period: int
    long_period: int


class TradingStrategy(Protocol):
    def generate_signal(self, context: SignalContext) -> StrategySignal:
        """Calculate an intent; execution and ML recording belong to the engine."""
        ...

    def generate_exit_signal(self, context: ExitSignalContext) -> str | None:
        """Return a strategy exit reason after the common protective exits."""
        ...
