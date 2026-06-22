from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


LiveMode = Literal["canary", "normal_scoring", "halted_review"]


@dataclass(frozen=True)
class LiveModeState:
    mode: LiveMode
    canary_required: bool
    canary_completed: bool
    canary_intent_id: str | None
    canary_reconciled_at: str | None
    promotion_reason: str | None
    normal_scoring_started_at: str | None
    cost_viability_evidence: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "canary_required": self.canary_required,
            "canary_completed": self.canary_completed,
            "canary_intent_id": self.canary_intent_id,
            "canary_reconciled_at": self.canary_reconciled_at,
            "promotion_reason": self.promotion_reason,
            "normal_scoring_started_at": self.normal_scoring_started_at,
            "cost_viability_evidence": self.cost_viability_evidence,
        }
