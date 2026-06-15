# src/magic_agent/executors/aster.py
"""Aster perps via a ccxt-shaped exchange (REST, hedge mode long+short).

The ``exchange`` is injected (``ccxt.aster({...})`` in the CLI, a stub in tests) so
this is testable with no network. We POLL (no websocket ``watch*`` needed). Maps the
agent's ``ExecutionIntent`` to ccxt ``create_order`` with ``positionSide`` per the
Binance-futures hedge-mode convention confirmed in the Task 2 spike.
"""
from __future__ import annotations

from typing import Any

from magic_agent.models import (
    Action, AccountState, ExecutionIntent, Outcome, PositionState, Side,
)


class AsterRestExecutor:
    def __init__(self, exchange: Any, symbol: str) -> None:
        self._exchange = exchange
        self._symbol = symbol

    def get_position(self) -> PositionState:
        positions = self._exchange.fetch_positions([self._symbol]) or []
        for p in positions:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0.0:
                continue
            side = Side.LONG if p.get("side") == "long" else Side.SHORT
            entry = p.get("entryPrice")
            return PositionState(side=side, size=contracts,
                                 entry_price=float(entry) if entry is not None else None)
        return PositionState()

    def get_account(self, *, mark_price: float) -> AccountState:
        bal = self._exchange.fetch_balance() or {}
        usdt = bal.get("USDT", {})
        total = float(usdt.get("total") or 0.0)
        free = float(usdt.get("free") or total)
        return AccountState(equity=total, available=free)

    def open_position(self, intent: ExecutionIntent) -> Outcome:
        if intent.qty <= 0:
            return Outcome.SKIPPED_ZERO_SIZE
        is_long = intent.action is Action.ENTER_LONG
        try:
            self._exchange.set_leverage(intent.leverage, intent.symbol)
            self._exchange.create_order(
                intent.symbol, "market", "buy" if is_long else "sell", intent.qty,
                None, {"positionSide": "LONG" if is_long else "SHORT"},
            )
        except Exception:
            return Outcome.REJECTED
        return Outcome.OPENED

    def close_position(self, *, mark_price: float) -> Outcome:
        pos = self.get_position()
        if pos.side is Side.FLAT:
            return Outcome.NOOP
        is_long = pos.side is Side.LONG
        try:
            self._exchange.create_order(
                self._symbol, "market", "sell" if is_long else "buy", pos.size,
                None, {"positionSide": "LONG" if is_long else "SHORT", "reduceOnly": True},
            )
        except Exception:
            return Outcome.REJECTED
        return Outcome.CLOSED

    def sync(self) -> None:
        return None
