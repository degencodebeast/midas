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


def test_evaluator_is_the_single_source_of_decision_inputs_and_prioritizes_stop():
    position = PositionView("p-1", Decimal("2"), Decimal("90"), Decimal("120"))
    inputs = LifecycleEvaluator().evaluate(
        _observation(position=position, low=Decimal("89"), high=Decimal("121"),
                     opposing_htf=True, reduction=Decimal("1"), entries_halted=True),
    )
    assert inputs.source == "lifecycle_evaluator_v1"
    assert inputs.exit_reason == "stop"
    assert inputs.exit_quantity == Decimal("2")
    assert DecisionPipeline().decide(inputs).action == "risk_exit"


def test_evaluator_supports_campaign_opposing_htf_and_risk_reduction_exits():
    position = PositionView("p-1", Decimal("2"), Decimal("90"), Decimal("120"))
    evaluator = LifecycleEvaluator()
    assert evaluator.evaluate(_observation(position=position, high=Decimal("120"))).exit_reason == "campaign_dol"
    assert evaluator.evaluate(_observation(position=position, opposing_htf=True)).exit_reason == "opposing_htf"
    reduced = evaluator.evaluate(_observation(position=position, reduction=Decimal("0.5")))
    assert reduced.exit_reason == "risk_reduction"
    assert reduced.exit_quantity == Decimal("0.5")
