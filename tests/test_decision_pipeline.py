from decimal import Decimal

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.lifecycle import LifecycleEvaluator, LifecycleObservation, PositionView
from magic_agent.risk_policy import RiskDecision
from magic_agent.spot_models import AuthorizedSetup


def _risk(*, approved: bool = True, denied_by: str | None = None) -> RiskDecision:
    return RiskDecision(
        approved, denied_by, ((denied_by,) if denied_by else ()),
        Decimal("0.0025"), Decimal("2.5"), Decimal("0.25"),
        Decimal("0.25") if approved else Decimal("0"),
    )


def _observation(*, position: PositionView | None = None, low: Decimal = Decimal("96"),
                 high: Decimal = Decimal("101"), opposing_htf: bool = False,
                 reduction: Decimal = Decimal("0"), entries_halted: bool = False,
                 risk: RiskDecision | None = None) -> LifecycleObservation:
    return LifecycleObservation(
        setup=AuthorizedSetup.example(), risk=risk or _risk(), position=position,
        low=low, high=high, opposing_htf_invalidated=opposing_htf,
        risk_reduction_qty=reduction, entries_halted=entries_halted,
    )


def test_pipeline_preserves_specific_risk_denial_codes():
    inputs = LifecycleEvaluator().evaluate(_observation(risk=_risk(approved=False, denied_by="daily_loss_cap")))
    decision = DecisionPipeline().decide(inputs)
    assert decision.action == "hold"
    assert decision.reason_codes == ("risk_denied", "daily_loss_cap")
