# src/magic_agent/log.py
"""Append-only JSONL decision/audit log — the backtest substrate for the agent
layer (mirrors the scanner's Phase-5 alert log). Every decision + outcome is
recorded so confirmation quality / gate behaviour can be ranked later from data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from magic_agent.models import AgentDecision, ContextSnapshot, Outcome


def decision_record(
    decision: AgentDecision,
    context: ContextSnapshot,
    outcome: Outcome,
    *,
    now: str,
    baseline_qty: float | None = None,
    llm_size_factor: float | None = None,
    llm_action_hint: str | None = None,
) -> dict[str, Any]:
    """Records the FINAL (clamped) decision plus the deterministic baseline and the
    LLM's pre-clamp recommendation, so the AI's marginal effect (baseline vs final)
    is measurable later. ``baseline_qty``/``llm_*`` are None on the pure-deterministic
    path (no advisor configured)."""
    intent = decision.intent
    return {
        "ts": now,
        "setup_ref": decision.setup_ref,
        "action": decision.action.value,
        "allow": decision.gate.allow,
        "size_multiplier": decision.gate.size_multiplier,
        "gate_reason": decision.gate.reason,
        "regime": context.regime,
        "risk_flag": context.risk_flag,
        "context_status": context.status,
        "qty": intent.qty if intent else 0.0,            # clamped FINAL size
        "baseline_qty": baseline_qty,                    # deterministic pre-LLM size
        "llm_size_factor": llm_size_factor,              # LLM recommendation (auditable)
        "llm_action_hint": llm_action_hint,
        "entry": intent.entry if intent else None,
        "stop_loss": intent.stop_loss if intent else None,
        "take_profit": intent.take_profit if intent else None,
        "leverage": intent.leverage if intent else None,
        "outcome": outcome.value,
        "reasoning": decision.reasoning,
    }


class AgentLog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, record: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
