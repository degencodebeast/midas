from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.cmc_selector import CandidateSnapshot
import pytest

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.executability import PreparedOrder
from magic_agent.lifecycle import LifecycleEvaluator, LifecycleObservation
from magic_agent.position_manager import PositionManager
from magic_agent.risk_policy import MarketRiskContext, PortfolioRiskState, QuantityCaps, RiskConfig, RiskPolicy
from magic_agent.runner import run_cycle
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


def _app(*, authorized: bool, policy_present: bool = True, execution_eligible: bool = True):
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    candidate = CandidateSnapshot(
        "zec-bsc", now, now + timedelta(minutes=15),
        Decimal("0.1"), Decimal("0.4"), Decimal("0.10"), Decimal("0.20"),
        False, (), Decimal("1"),
    )
    envelope = SimpleNamespace(
        identity_key="zec-bsc", symbol="ZEC", snapshot=candidate,
        execution_eligible=execution_eligible, pinned=True,
    )
    setup = AuthorizedSetup.example() if authorized else None
    executions = []
    alerts = []
    state = SimpleNamespace(
        blocks_new_exposure=False,
        canary_mode=False,
        risk_state=lambda: PortfolioRiskState.example(),
        as_dict=lambda: {},
    )
    app = SimpleNamespace(
        reconcile_unfinished=lambda: None,
        position_manager=PositionManager(
            positions=lambda: [], observe=lambda position, observed_at, reduction: None,
            evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
            risk_policy=RiskPolicy(RiskConfig.defaults() if policy_present else None),
            risk_state=lambda: PortfolioRiskState.example(),
            sell_probe=lambda position, quantity: None, execute=lambda *args: None,
        ),
        state=state,
        cmc_source=SimpleNamespace(snapshot=lambda observed_at: SimpleNamespace(
            snapshots={"ZEC": candidate}, exclusions=(),
        )),
        candidate_source=SimpleNamespace(enumerate=lambda **kwargs: SimpleNamespace(
            monitoring=(envelope,), discovery=(), exclusions=(),
        )),
        watchlist=SimpleNamespace(
            state=SimpleNamespace(discovery_due=lambda observed_at: False),
            promote=lambda symbol: None, mark_discovery=lambda observed_at: None,
        ),
        exclusion_journal=SimpleNamespace(
            append_many=lambda rows, observed_at: None,
            append_code=lambda symbol, reason, observed_at: None,
        ),
        scanner_gateway=SimpleNamespace(scan=lambda selected: setup),
        executability=SimpleNamespace(prepare_order=lambda **kwargs: PreparedOrder(
            approved=True, reasons=(),
            risk=kwargs["risk_policy"].evaluate(
                kwargs["setup"], kwargs["risk_state"], ActionPurpose.STRATEGY,
                QuantityCaps.unbounded(), kwargs["market"],
            ),
            quote={"id": "q", "quantity": "0.25"},
        )),
        risk_policy=RiskPolicy(RiskConfig.defaults()) if policy_present else None,
        pipeline=DecisionPipeline(),
        lifecycle_evaluator=LifecycleEvaluator(),
        observe_entry=lambda selected, risk, observed_at: LifecycleObservation(
            selected, risk, None, None, None, False, Decimal("0"), False,
        ),
        execution_coordinator=SimpleNamespace(submit=lambda intent, **evidence: executions.append(SimpleNamespace(state="RECONCILED", intent=intent))),
        after_entry_submission=lambda **kwargs: None,
        decision_journal=SimpleNamespace(append=lambda decision, observed_at: None),
        compliance=SimpleNamespace(observe=lambda records, observed_at: alerts.append(SimpleNamespace(code="daily_qualification_at_risk")) if not records else None),
        execution_journal=SimpleNamespace(confirmed_records=lambda: executions),
        state_journal=SimpleNamespace(save=lambda payload: None),
        position_store=SimpleNamespace(save=lambda positions: None),
        # run_cycle recomputes advisory qualification pace before each publish; the
        # fake app has no qualification_config, so the real method would no-op anyway.
        update_qualification_pace=lambda observed_at: None,
        # run_cycle publishes a live status snapshot at each end-of-cycle save point;
        # the fake app stubs it out (no dashboard file in these pipeline-shape tests).
        publish_status=lambda: None,
    )
    return app, executions, alerts, now


def test_paper_spot_cycle_uses_shared_pipeline_and_reconciles():
    app, executions, alerts, now = _app(authorized=True)
    run_cycle(app, now)
    assert executions[-1].state == "RECONCILED"
    assert executions[-1].intent.setup.structural_stop == AuthorizedSetup.example().structural_stop


def test_compliance_ledger_cannot_authorize_trade():
    app, executions, alerts, now = _app(authorized=False)
    run_cycle(app, now)
    assert executions == []
    assert alerts[-1].code == "daily_qualification_at_risk"


def test_registry_resolved_watchlist_name_is_monitored_but_not_executed():
    app, executions, alerts, now = _app(authorized=True, execution_eligible=False)
    run_cycle(app, now)
    assert executions == []


def test_missing_policy_processes_protective_exits_but_blocks_entry_path():
    app, executions, alerts, now = _app(authorized=True, policy_present=False)
    with pytest.raises(RuntimeError, match="mandatory RiskPolicy"):
        run_cycle(app, now)
    assert executions == []


def test_pipeline_rejects_hand_built_decision_inputs():
    # No-bypass invariant: a caller cannot hand-build DecisionInputs and route them
    # through the pipeline. run_cycle therefore MUST go through LifecycleEvaluator,
    # which is the only producer of the required source stamp.
    from magic_agent.lifecycle import DecisionInputs

    foreign = DecisionInputs(
        setup=AuthorizedSetup.example(), risk=None, position=None,
        exit_reason=None, exit_quantity=Decimal("0"), entries_halted=False,
        source="hand_built",
    )
    with pytest.raises(ValueError, match="must come from LifecycleEvaluator"):
        DecisionPipeline().decide(foreign)


def test_kill_switch_blocks_entries_but_not_exits(tmp_path):
    app, executions, alerts, now = _app(authorized=True)
    exit_calls = []

    def process_exits(observed_at):
        exit_calls.append(observed_at)
        return 0

    app.position_manager.process_exits = process_exits
    kill_switch = tmp_path / "HALT_NEW_ENTRIES"
    kill_switch.write_text("halt", encoding="utf-8")
    app.kill_switch_path = kill_switch

    from magic_agent.runner import run_cycle
    run_cycle(app, now)

    assert exit_calls == [now]
    assert executions == []
