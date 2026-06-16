# src/magic_agent/executor.py
"""Execution boundary. ``PerpExecutor`` is the one interface; ``PaperExecutor`` is
the in-memory sim used as the always-demoable fallback and in every test."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from magic_agent.models import (
    Action, AccountState, ExecutionIntent, Outcome, PositionState, Side,
)


@runtime_checkable
class PerpExecutor(Protocol):
    def get_position(self) -> PositionState: ...

    def get_account(self, *, mark_price: float) -> AccountState:
        """Account snapshot at ``mark_price``.

        Contract: ``AccountState.available`` is REALIZED cash only — it must NOT move on
        unrealized/mark-to-market PnL. The live daily-loss kill-switch in ``run_live``
        derives ``realized_pnl_today`` from the session delta of ``available``; if a real
        venue executor reports free margin that fluctuates with mark price, the switch
        would trip on transient drawdown. Keep ``available`` = realized cash.
        """
        ...

    def open_position(self, intent: ExecutionIntent) -> Outcome: ...
    def close_position(self, *, mark_price: float) -> Outcome: ...
    def sync(self) -> None: ...


class PaperExecutor:
    """One-position-at-a-time in-memory perp sim. PnL is mark-to-entry * size,
    sign-aware for long/short. No leverage effect on PnL in v1 (size is units)."""

    def __init__(self, starting_equity: float = 1000.0) -> None:
        self._realized = starting_equity
        self._pos = PositionState()

    def get_position(self) -> PositionState:
        return self._pos

    def _unrealized(self, mark_price: float) -> float:
        p = self._pos
        if p.side is Side.FLAT or p.entry_price is None:
            return 0.0
        sign = 1.0 if p.side is Side.LONG else -1.0
        return sign * (mark_price - p.entry_price) * p.size

    def get_account(self, *, mark_price: float) -> AccountState:
        equity = self._realized + self._unrealized(mark_price)
        return AccountState(equity=equity, available=self._realized)

    def open_position(self, intent: ExecutionIntent) -> Outcome:
        if intent.qty <= 0:
            return Outcome.SKIPPED_ZERO_SIZE
        if self._pos.side is not Side.FLAT:
            return Outcome.SKIPPED_IN_POSITION
        side = Side.LONG if intent.action is Action.ENTER_LONG else Side.SHORT
        self._pos = PositionState(
            side=side, size=intent.qty, entry_price=intent.entry,
            stop_loss=intent.stop_loss, take_profit=intent.take_profit,
        )
        return Outcome.OPENED

    def close_position(self, *, mark_price: float) -> Outcome:
        if self._pos.side is Side.FLAT:
            return Outcome.NOOP
        self._realized += self._unrealized(mark_price)
        self._pos = PositionState()
        return Outcome.CLOSED

    def sync(self) -> None:
        return None
