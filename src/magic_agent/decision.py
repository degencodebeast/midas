"""Bounded, deterministic decision layer.

The scanner produces the Setup; this layer only decides take/skip (``gate``) and
position size (``size``). The take/skip and size are pure functions of (grade,
context, risk) — an LLM may later rank/explain, but never controls these.
"""
from __future__ import annotations

from magic_agent.models import (
    Action, AccountState, AgentDecision, ContextSnapshot, ExecutionIntent,
    GateVerdict, Setup, Side,
)

RISK_PCT_DEFAULT = 0.01

# Scanner rating FAMILY — source of truth is engine.py:15
# RATING = ["N/A", "C", "B-", "B", "B+", "A-", "A", "A+", "A++"].
# We trade the A-family and B-family; "C"/"N/A"/"—" are below threshold. Match by
# family, NOT exact "A"/"B" (which would wrongly veto A++, A+, A-, B+, B-).
_A_GRADES = {"A++", "A+", "A", "A-"}
_B_GRADES = {"B+", "B", "B-"}


def _grade_family(rating: str) -> str | None:
    if rating in _A_GRADES:
        return "A"
    if rating in _B_GRADES:
        return "B"
    return None


def _against_regime(direction: Side, regime: str) -> bool:
    """A long fights a risk_off tape; a short fights a risk_on tape."""
    return (regime == "risk_off" and direction is Side.LONG) or (
        regime == "risk_on" and direction is Side.SHORT
    )


def gate(setup: Setup, context: ContextSnapshot) -> GateVerdict:
    family = _grade_family(setup.rating)
    if family is None:
        return GateVerdict(False, 0.0, f"rating {setup.rating} below threshold")

    # Context unavailable => the setup stands on its own grade, full size.
    if context.status != "ok":
        return GateVerdict(True, 1.0, "context unavailable; setup stands")

    if context.risk_flag == "high":
        return GateVerdict(False, 0.0, "context risk_flag=high")

    against = _against_regime(setup.direction, context.regime)
    if family == "B" and against:
        return GateVerdict(False, 0.0, "B-grade against regime")

    mult = 1.0
    if against:
        mult = 0.5  # A-grade against regime: allowed at half size
    elif context.risk_flag == "elevated":
        mult = 0.5
    return GateVerdict(True, mult, f"allowed (mult={mult})")


def size(*, equity: float, risk_pct: float, entry: float, stop_loss: float) -> float:
    distance = abs(entry - stop_loss)
    if distance <= 0:
        return 0.0
    return (equity * risk_pct) / distance


def build_decision(
    setup: Setup,
    context: ContextSnapshot,
    account: AccountState,
    *,
    risk_pct: float = RISK_PCT_DEFAULT,
    leverage: float = 1.0,
) -> AgentDecision:
    verdict = gate(setup, context)
    ref = f"{setup.symbol}:{setup.direction.value}:{setup.confirmation_kind}"
    if not verdict.allow:
        return AgentDecision(Action.HOLD, None, verdict, ref, verdict.reason)

    qty = size(equity=account.equity, risk_pct=risk_pct,
               entry=setup.entry, stop_loss=setup.stop_loss) * verdict.size_multiplier
    action = Action.ENTER_LONG if setup.direction is Side.LONG else Action.ENTER_SHORT
    intent = ExecutionIntent(
        symbol=setup.symbol, action=action, qty=qty, entry=setup.entry,
        stop_loss=setup.stop_loss, take_profit=setup.take_profit, leverage=leverage,
    )
    return AgentDecision(action, intent, verdict, ref, verdict.reason)
