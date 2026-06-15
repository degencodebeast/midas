"""Loop body — one newly-closed candle at a time, stops checked FIRST.

Pure orchestration: scanning, context, executor are all injected, so the whole loop
runs in tests with no network. The live driver (cli.py) supplies a real fetch +
new-candle gate (drop the forming bar, dedupe on timestamp) around ``on_candle``.
"""
from __future__ import annotations

from collections.abc import Callable

from magic_agent.context import CmcContextAdapter
from magic_agent.decision import build_decision, RISK_PCT_DEFAULT
from magic_agent.executor import PerpExecutor
from magic_agent.log import AgentLog, decision_record
from magic_agent.models import (
    Action, AgentDecision, Candle, GateVerdict, Outcome, PositionState, Setup, Side,
)


def check_stops(position: PositionState, candle: Candle) -> bool:
    """True if this candle's range touched the position's stop or target."""
    if position.side is Side.FLAT:
        return False
    if position.side is Side.LONG:
        return (position.stop_loss is not None and candle.low <= position.stop_loss) or (
            position.take_profit is not None and candle.high >= position.take_profit
        )
    return (position.stop_loss is not None and candle.high >= position.stop_loss) or (
        position.take_profit is not None and candle.low <= position.take_profit
    )


def on_candle(
    executor: PerpExecutor,
    *,
    setup_fn: Callable[[], Setup | None],
    context: CmcContextAdapter,
    candle: Candle,
    risk_pct: float = RISK_PCT_DEFAULT,
    leverage: float = 1.0,
    log: AgentLog | None = None,
    now: str = "",
) -> tuple[AgentDecision, Outcome]:
    # 1. Stops first — deterministic risk before anything else.
    pos = executor.get_position()
    if pos.side is not Side.FLAT and check_stops(pos, candle):
        outcome = executor.close_position(mark_price=candle.close)
        decision = AgentDecision(Action.CLOSE, None, GateVerdict(True, 0.0, "stop/target hit"),
                                 "stop", "stop or target hit")
        if log is not None:
            from magic_agent.models import ContextSnapshot
            log(decision_record(decision, ContextSnapshot("neutral", "low", "ok"), outcome, now=now))
        return decision, outcome

    # 2. Already in a position -> hold (one position at a time).
    if pos.side is not Side.FLAT:
        return AgentDecision(Action.HOLD, None, GateVerdict(False, 0.0, "in position"),
                             "hold", "in position"), Outcome.SKIPPED_IN_POSITION

    # 3. Signal -> context -> decision.
    setup = setup_fn()
    if setup is None:
        return AgentDecision(Action.HOLD, None, GateVerdict(False, 0.0, "no setup"),
                             "none", "no setup"), Outcome.NOOP

    ctx = context.get_context(setup.symbol)
    account = executor.get_account(mark_price=candle.close)
    decision = build_decision(setup, ctx, account, risk_pct=risk_pct, leverage=leverage)

    if not decision.gate.allow:
        outcome = Outcome.SKIPPED_VETO
    elif decision.intent is None or decision.intent.qty <= 0:
        outcome = Outcome.SKIPPED_ZERO_SIZE
    else:
        outcome = executor.open_position(decision.intent)

    if log is not None:
        log(decision_record(decision, ctx, outcome, now=now))
    return decision, outcome
