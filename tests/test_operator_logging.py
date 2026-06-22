"""Operator console logging tests.

These assert the VISIBLE, real-time narrative an operator sees while watching a
live run: the per-cycle header, the universe/eligibility line, the per-token scan
result, the decision (ENTER / NO_TRADE), the BUY line with the execution state, and
the cycle footer. The logs MIRROR the existing JSONL journals to the console; they
are additive and must never carry a secret.

The fake App mirrors ``tests/test_spot_runtime.py`` (the runner harness) so the logs
are exercised against the same data flow ``run_cycle`` actually sees.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.cmc_selector import CandidateSnapshot
from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.executability import PreparedOrder
from magic_agent.lifecycle import LifecycleEvaluator, LifecycleObservation
from magic_agent.position_manager import PositionManager
from magic_agent.risk_policy import (
    PortfolioRiskState,
    QuantityCaps,
    RiskConfig,
    RiskPolicy,
)
from magic_agent.runner import run_cycle
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


# A placeholder secret value: it is wired onto the fake App as if it were a wallet
# password / api key, so the "no secrets logged" test can prove it never reaches a
# log call. The real runtime never passes secrets into a log call either.
_SECRET = "s3cr3t-wallet-password-DO-NOT-LOG"


def _app(*, authorized: bool, execution_eligible: bool = True):
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
    state = SimpleNamespace(
        blocks_new_exposure=False,
        canary_mode=False,
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        risk_state=lambda: PortfolioRiskState.example(),
        as_dict=lambda: {},
        # A secret deliberately hung on the state; must never appear in a log record.
        wallet_password=_SECRET,
    )

    def _submit(intent, **evidence):
        executions.append(SimpleNamespace(state="RECONCILED", intent=intent))
        return "RECONCILED"

    app = SimpleNamespace(
        reconcile_unfinished=lambda: None,
        position_manager=PositionManager(
            positions=lambda: [], observe=lambda position, observed_at, reduction: None,
            evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
            risk_policy=RiskPolicy(RiskConfig.defaults()),
            risk_state=lambda: PortfolioRiskState.example(),
            sell_probe=lambda position, quantity: None, execute=lambda *args: None,
        ),
        state=state,
        mode="paper",
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
        risk_policy=RiskPolicy(RiskConfig.defaults()),
        pipeline=DecisionPipeline(),
        lifecycle_evaluator=LifecycleEvaluator(),
        observe_entry=lambda selected, risk, observed_at: LifecycleObservation(
            selected, risk, None, None, None, False, Decimal("0"), False,
        ),
        execution_coordinator=SimpleNamespace(submit=_submit),
        after_entry_submission=lambda **kwargs: None,
        decision_journal=SimpleNamespace(append=lambda decision, observed_at: None),
        compliance=SimpleNamespace(observe=lambda records, observed_at: None),
        execution_journal=SimpleNamespace(confirmed_records=lambda: executions),
        state_journal=SimpleNamespace(save=lambda payload: None),
        position_store=SimpleNamespace(save=lambda positions: None),
        update_qualification_pace=lambda observed_at: None,
        publish_status=lambda: None,
    )
    return app, executions, now


def _messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith("magic_agent")]


def test_cycle_emits_header_universe_and_decision_info(caplog):
    app, executions, now = _app(authorized=True)
    with caplog.at_level(logging.INFO, logger="magic_agent"):
        run_cycle(app, now)
    msgs = _messages(caplog)
    blob = "\n".join(msgs)
    # Cycle header — proves the operator sees the loop is alive.
    assert any(m.startswith("=== cycle") and "mode=paper" in m for m in msgs), blob
    # Universe / eligibility line.
    assert any("universe:" in m and "eligible" in m for m in msgs), blob
    # Per-token scan result.
    assert any(m.startswith("scan ZEC") for m in msgs), blob
    # Decision line.
    assert any(m.startswith("decision ZEC") for m in msgs), blob
    # Cycle footer.
    assert any(m.startswith("cycle done") for m in msgs), blob


def test_trade_path_logs_buy_line_with_tx_state(caplog):
    app, executions, now = _app(authorized=True)
    with caplog.at_level(logging.INFO, logger="magic_agent"):
        run_cycle(app, now)
    assert executions and executions[-1].state == "RECONCILED"
    msgs = _messages(caplog)
    blob = "\n".join(msgs)
    assert any(m.startswith("decision ZEC: ENTER") for m in msgs), blob
    buy_lines = [m for m in msgs if m.startswith("BUY ZEC")]
    assert buy_lines, blob
    assert "RECONCILED" in buy_lines[0]


def test_no_trade_path_logs_no_trade(caplog):
    app, executions, now = _app(authorized=False)
    with caplog.at_level(logging.INFO, logger="magic_agent"):
        run_cycle(app, now)
    assert executions == []
    msgs = _messages(caplog)
    blob = "\n".join(msgs)
    # The operator must still see the cycle is alive and that nothing fired.
    assert any(m.startswith("=== cycle") for m in msgs), blob
    assert any("NO_TRADE" in m for m in msgs), blob
    # No BUY line on the no-trade path.
    assert not any(m.startswith("BUY ") for m in msgs), blob


def test_no_secret_value_is_ever_logged(caplog):
    app, executions, now = _app(authorized=True)
    with caplog.at_level(logging.DEBUG, logger="magic_agent"):
        run_cycle(app, now)
    for record in caplog.records:
        assert _SECRET not in record.getMessage()
        # Also scan the raw args (a %-format secret would hide from getMessage only
        # if mis-asserted; scan both to be airtight).
        for arg in (record.args or ()):
            assert _SECRET != arg
