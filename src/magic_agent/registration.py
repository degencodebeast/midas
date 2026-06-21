"""Separate Track-1 competition registration from ERC-8004 identity registration.

These are TWO INDEPENDENT paths:
  - CompetitionRegistrar  → Track-1 competition entry via TWAK (swap/x402 signer).
  - Erc8004Registrar      → ERC-8004 agent identity via the bnbagent-sdk adapter.

TWAK is the sole signer for swaps/x402.  It is NEVER used for ERC-8004 identity.
The bnbagent-sdk is IDENTITY-ONLY; no swap or x402 payment routes through it.

UNVALIDATED: confirm against bnbagent-sdk docs before deploy.
The ``sdk_agent`` parameter is an injectable adapter (port/adapter pattern) that
wraps the real ``bnbagent`` SDK's ``ERC8004Agent``.  The real contract address, ABI,
and chain calls are NOT hardcoded here — they must be confirmed against the bnbagent-sdk
documentation and injected from the CLI layer, not from this module.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistrationResult:
    agent_id: int | None
    transaction_hash: str


class CompetitionRegistrar:
    """Track-1 competition registration via TWAK (the sole swap/x402 signer).

    TWAK is injected and must expose ``.json(args: list[str]) -> dict``.
    No ERC-8004 or bnbagent-sdk interaction occurs here.
    """

    def __init__(self, twak) -> None:
        self.twak = twak

    def register(self) -> dict:
        return self.twak.json(["compete", "register", "--json"])

    def status(self) -> dict:
        return self.twak.json(["compete", "status", "--json"])


class Erc8004Registrar:
    """ERC-8004 agent identity registration via an injectable bnbagent-sdk adapter.

    ``sdk_agent`` is an adapter (port) over the bnbagent SDK's ``ERC8004Agent``.
    It must expose ``.register_agent(*, agent_uri: str) -> dict`` with keys
    ``"agentId"`` and ``"transactionHash"``.

    UNVALIDATED: The real bnbagent-sdk / ERC-8004 contract surface has NOT been
    doc-validated via Context7 (unreachable at build time).  Confirm the method
    name, parameter names, and return shape against the actual bnbagent-sdk docs
    before deploying a real adapter.  This class assumes the adapter hides all
    chain/ABI details; nothing is hardcoded here.

    TWAK is NEVER used here.  This path is identity-only.
    """

    def __init__(self, sdk_agent) -> None:
        self.sdk_agent = sdk_agent

    def register(self, agent_uri: str) -> RegistrationResult:
        result = self.sdk_agent.register_agent(agent_uri=agent_uri)
        return RegistrationResult(int(result["agentId"]), str(result["transactionHash"]))
