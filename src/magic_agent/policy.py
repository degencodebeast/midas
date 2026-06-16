# src/magic_agent/policy.py
"""Fail-closed policy engine — the only path to funds (modelled on aegis
engine/policies/engine.mjs). AND semantics, short-circuit on first denial, and
``run_policies`` THROWS on an empty config (no policies => no trade, never a silent
allow). The runner calls this before any open_position; the no-bypass test proves it."""
from __future__ import annotations

from dataclasses import dataclass

from magic_agent.models import ExecutionIntent


class MissingPolicyConfigError(ValueError):
    """Raised when no policies are active — fail closed, never allow."""


@dataclass(frozen=True)
class PolicyConfig:
    max_notional: float | None = None
    max_leverage: float | None = None
    max_daily_loss: float | None = None   # positive; kill-switch when pnl_today <= -this
    max_concurrent: int | None = None
    require_stop: bool = False
    cooldown_seconds: float | None = None

    def active(self) -> list[str]:
        names = []
        if self.max_daily_loss is not None: names.append("daily_loss")
        if self.max_concurrent is not None: names.append("max_concurrent")
        if self.require_stop: names.append("stop_required")
        if self.max_leverage is not None: names.append("max_leverage")
        if self.max_notional is not None: names.append("max_notional")
        if self.cooldown_seconds is not None: names.append("cooldown")
        return names


@dataclass(frozen=True)
class PolicyState:
    equity: float
    realized_pnl_today: float = 0.0
    open_positions: int = 0
    seconds_since_last_trade: float | None = None


@dataclass(frozen=True)
class PolicyResult:
    approved: bool
    denied_by: str | None = None
    reason: str = ""


def run_policies(
    intent: ExecutionIntent, state: PolicyState, config: PolicyConfig
) -> PolicyResult:
    if not config.active():
        raise MissingPolicyConfigError("no policies configured — refusing (fail-closed)")
    # AND semantics; order = most protective first; first denial short-circuits.
    if config.max_daily_loss is not None and state.realized_pnl_today <= -abs(config.max_daily_loss):
        return PolicyResult(False, "daily_loss", "daily-loss kill-switch tripped")
    if config.max_concurrent is not None and state.open_positions >= config.max_concurrent:
        return PolicyResult(False, "max_concurrent", "max concurrent positions reached")
    if config.require_stop and (intent.stop_loss is None or intent.stop_loss <= 0):
        return PolicyResult(False, "stop_required", "intent has no stop")
    if config.max_leverage is not None and intent.leverage > config.max_leverage:
        return PolicyResult(False, "max_leverage", f"leverage {intent.leverage} > {config.max_leverage}")
    if config.max_notional is not None and (intent.qty * intent.entry) > config.max_notional:
        return PolicyResult(False, "max_notional", "notional over cap")
    if (config.cooldown_seconds is not None and state.seconds_since_last_trade is not None
            and state.seconds_since_last_trade < config.cooldown_seconds):
        return PolicyResult(False, "cooldown", "within cooldown window")
    return PolicyResult(True, None, "all policies passed")
