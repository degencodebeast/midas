# src/magic_agent/models.py
"""Shared vocabulary for the agent layer — enums + frozen dataclasses.

Frozen dataclasses (not pydantic) keep us aligned with the scanner's idiom and add
no dependency. These types are the interfaces between decision / execution / runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    HOLD = "hold"
    CLOSE = "close"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"


class Side(str, Enum):
    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


class Outcome(str, Enum):
    NOOP = "noop"
    OPENED = "opened"
    CLOSED = "closed"
    SKIPPED_VETO = "skipped_veto"
    SKIPPED_ZERO_SIZE = "skipped_zero_size"
    SKIPPED_IN_POSITION = "skipped_in_position"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Candle:
    """OHLC for stop checks. No timestamp — the runner owns time, not the model."""
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ContextSnapshot:
    """CMC-derived market context. ``status='unavailable'`` => no veto, no boost."""
    regime: str        # "risk_on" | "risk_off" | "neutral"
    risk_flag: str     # "low" | "elevated" | "high"
    status: str = "ok"  # "ok" | "unavailable"


@dataclass(frozen=True)
class Setup:
    """Agent-side view of one scanner setup (the SOLE signal source)."""
    symbol: str
    direction: Side    # LONG or SHORT
    rating: str        # "A" / "B" / ...
    regime: str
    entry: float
    stop_loss: float
    take_profit: float
    confirmation_kind: str


@dataclass(frozen=True)
class GateVerdict:
    allow: bool
    size_multiplier: float
    reason: str


@dataclass(frozen=True)
class ExecutionIntent:
    symbol: str
    action: Action          # ENTER_LONG / ENTER_SHORT / CLOSE
    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    leverage: float = 1.0


@dataclass(frozen=True)
class AgentDecision:
    action: Action
    intent: ExecutionIntent | None
    gate: GateVerdict
    setup_ref: str
    reasoning: str = ""


@dataclass(frozen=True)
class PositionState:
    side: Side = Side.FLAT
    size: float = 0.0
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass(frozen=True)
class AccountState:
    equity: float
    available: float
    currency: str = "USDT"
