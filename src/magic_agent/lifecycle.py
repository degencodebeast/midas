"""Shared lifecycle evaluator: the single source of decision inputs.

``LifecycleEvaluator.evaluate`` is the *only* producer of :class:`DecisionInputs`.
Live, paper, and replay callers construct a :class:`LifecycleObservation` from
their own data sources and funnel it through the evaluator; they never hand-build
``DecisionInputs`` or pre-compute exit reasons. This keeps protective-exit logic
identical across all three runtime modes.
"""

from dataclasses import dataclass
from decimal import Decimal

from magic_agent.risk_policy import RiskDecision
from magic_agent.spot_models import AuthorizedSetup


@dataclass(frozen=True)
class PositionView:
    """Immutable view of an open spot-long position for lifecycle evaluation."""

    position_id: str
    quantity: Decimal
    stop: Decimal
    campaign_dol: Decimal


@dataclass(frozen=True)
class LifecycleObservation:
    """Raw observation assembled by a runtime caller (live/paper/replay)."""

    setup: AuthorizedSetup | None
    risk: RiskDecision | None
    position: PositionView | None
    low: Decimal | None
    high: Decimal | None
    opposing_htf_invalidated: bool
    risk_reduction_qty: Decimal
    entries_halted: bool


@dataclass(frozen=True)
class DecisionInputs:
    """Decision inputs produced exclusively by :class:`LifecycleEvaluator`."""

    setup: AuthorizedSetup | None
    risk: RiskDecision | None
    position: PositionView | None
    exit_reason: str | None
    exit_quantity: Decimal
    entries_halted: bool
    source: str


class LifecycleEvaluator:
    """Resolve a :class:`LifecycleObservation` into deterministic decision inputs."""

    VERSION = "lifecycle_evaluator_v1"

    def evaluate(self, observation: LifecycleObservation) -> DecisionInputs:
        """Compute the prioritized protective-exit reason and decision inputs.

        Exit precedence (highest first): stop, campaign DOL, opposing-HTF
        invalidation, risk reduction. This precedence is independent of entry
        gating so protective exits remain reachable even when entries are halted.

        Args:
            observation: The raw runtime observation to evaluate.

        Returns:
            DecisionInputs stamped with ``source = VERSION`` so the pipeline can
            verify the single-source invariant.
        """
        position = observation.position
        reason = None
        quantity = Decimal("0")
        if position is not None:
            if observation.low is not None and observation.low <= position.stop:
                reason, quantity = "stop", position.quantity
            elif observation.high is not None and observation.high >= position.campaign_dol:
                reason, quantity = "campaign_dol", position.quantity
            elif observation.opposing_htf_invalidated:
                reason, quantity = "opposing_htf", position.quantity
            elif observation.risk_reduction_qty > 0:
                reason = "risk_reduction"
                quantity = min(position.quantity, observation.risk_reduction_qty)
        return DecisionInputs(
            observation.setup, observation.risk, position, reason, quantity,
            observation.entries_halted, self.VERSION,
        )
