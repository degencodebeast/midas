# src/magic_agent/context.py
"""CMC Agent Hub context adapter.

``client`` is an injected callable ``client(symbol) -> {"regime","risk_flag"}`` (a
thin wrapper over the CMC MCP/REST call, wired in the CLI). Kept injectable so the
loop is testable with no network. Any failure (or no client) degrades to
``status='unavailable'`` so the runner never blocks on CMC.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from magic_agent.models import ContextSnapshot

_UNAVAILABLE = ContextSnapshot(regime="neutral", risk_flag="low", status="unavailable")


class CmcContextAdapter:
    def __init__(self, client: Callable[[str], dict[str, Any]] | None = None) -> None:
        self._client = client

    def get_context(self, symbol: str) -> ContextSnapshot:
        if self._client is None:
            return _UNAVAILABLE
        try:
            raw = self._client(symbol)
            return ContextSnapshot(
                regime=str(raw["regime"]),
                risk_flag=str(raw["risk_flag"]),
                status="ok",
            )
        except Exception:
            return _UNAVAILABLE
