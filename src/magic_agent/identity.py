# src/magic_agent/identity.py
"""ERC-8004 on-chain agent identity via bnbagent-sdk.

``registrar`` is injected (the bnbagent-sdk ``Erc8004Contract`` / agent client in the
CLI, a stub in tests) and must expose ``register(agent_uri, metadata) -> agent_id``.
Registration is idempotent — once we hold an ``agent_id`` we never re-register.
"""
from __future__ import annotations

from typing import Any


class Erc8004Identity:
    def __init__(self, registrar: Any, *, agent_uri: str, metadata: dict[str, Any]) -> None:
        self._registrar = registrar
        self._agent_uri = agent_uri
        self._metadata = metadata
        self.agent_id: int | None = None

    def register(self) -> int:
        if self.agent_id is not None:
            return self.agent_id
        self.agent_id = int(self._registrar.register(self._agent_uri, self._metadata))
        return self.agent_id
