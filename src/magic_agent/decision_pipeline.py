"""Pure entry/exit selector.

``DecisionPipeline.decide`` is a deterministic function of its injected
:class:`DecisionInputs`: no network, no persistence, no clock or RNG. It consumes
the RiskPolicy decision carried on the inputs rather than re-deriving sizing or
scanner authorization, and it routes protective exits before any entry gating so
a halted entry can never block a stop/DOL/risk-reduction exit.
"""

from dataclasses import dataclass
from decimal import Decimal

from magic_agent.lifecycle import DecisionInputs, LifecycleEvaluator
from magic_agent.spot_models import ActionPurpose, SpotIntent


@dataclass(frozen=True)
class PipelineDecision:
    """The resolved action for a single decision cycle."""

    action: str
    intent: SpotIntent | None
    reason: str
    reason_codes: tuple[str, ...]
    exit_quantity: Decimal = Decimal("0")


class DecisionPipeline:
    """Select an entry/exit/hold action from evaluator-produced inputs."""

    def decide(self, data: DecisionInputs) -> PipelineDecision:
        """Return the deterministic decision for ``data``.

        Args:
            data: Inputs produced by :class:`LifecycleEvaluator`.

        Returns:
            The resolved :class:`PipelineDecision`.

        Raises:
            ValueError: If ``data`` did not originate from the lifecycle
                evaluator (enforces the single-source invariant).
        """
        if data.source != LifecycleEvaluator.VERSION:
            raise ValueError("DecisionInputs must come from LifecycleEvaluator")
        if data.position is not None and data.exit_reason is not None:
            return PipelineDecision(
                "risk_exit", None, data.exit_reason, (data.exit_reason,), data.exit_quantity,
            )
        if data.position is not None:
            return PipelineDecision("hold", None, "position_open", ("position_open",))
        if data.entries_halted:
            return PipelineDecision("hold", None, "entries_halted", ("entries_halted",))
        if data.setup is None:
            return PipelineDecision(
                "hold", None, "no_scanner_authorization", ("no_scanner_authorization",),
            )
        if data.risk is None or not data.risk.approved or data.risk.final_qty <= 0:
            detail = data.risk.denied_by if data.risk is not None else "risk_unavailable"
            reasons = data.risk.reasons if data.risk is not None else ()
            return PipelineDecision(
                "hold", None, "risk_denied",
                tuple(dict.fromkeys(("risk_denied", detail, *reasons))),
            )
        intent = SpotIntent(
            f"intent:{data.setup.setup_id}", data.setup,
            data.risk.final_qty, "buy", ActionPurpose.STRATEGY,
        )
        return PipelineDecision("enter", intent, "authorized", ("authorized",))
