"""Tests for the paper execution adapter (Task 14 — TDD).

The paper adapter is the execution port for paper mode: it simulates a fill and
books the position through the SAME reconcile path the live coordinator uses
(``PositionManager.open_from_reconciliation``) — never an optimistic or quoted
size that bypasses booking. It records a confirmed (``RECONCILED``) execution
record so the compliance ledger counts the simulated swap. It touches no funds.
"""
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.lifecycle import LifecycleEvaluator
from magic_agent.position_manager import PositionManager
from magic_agent.risk_policy import PortfolioRiskState, RiskConfig, RiskPolicy
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent
from magic_agent.paper_adapter import PaperExecutionAdapter


def _position_manager():
    return PositionManager(
        positions=lambda: [], observe=lambda position, observed_at, reduction: None,
        evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda position, quantity: None, execute=lambda *args: None,
    )


def _intent():
    return SpotIntent(
        "intent:setup-1", AuthorizedSetup.example(), Decimal("0.25"), "buy",
        ActionPurpose.STRATEGY,
    )


def test_submit_books_position_via_reconciliation_path():
    pm = _position_manager()
    adapter = PaperExecutionAdapter(positions=pm)
    intent = _intent()

    adapter.submit(intent, quote={"id": "q", "quantity": "0.25"}, policy=object())

    # Booked through the reconcile path, with the simulated realized quantity.
    assert len(pm.book) == 1
    assert pm.book[0].intent_id == "intent:setup-1"
    assert pm.book[0].quantity == Decimal("0.25")


def test_submit_records_a_confirmed_swap_for_compliance():
    pm = _position_manager()
    adapter = PaperExecutionAdapter(positions=pm)
    adapter.submit(_intent(), quote={"id": "q", "quantity": "0.25"}, policy=object())

    records = adapter.confirmed_records()
    assert len(records) == 1
    assert records[0].state == "RECONCILED"
    assert records[0].intent.intent_id == "intent:setup-1"


def test_adapter_does_not_touch_real_funds():
    # The adapter has no transfer/wallet/rpc/twak port — it cannot move funds.
    adapter = PaperExecutionAdapter(positions=_position_manager())
    for forbidden in ("transfer", "wallet", "rpc", "twak", "broadcast"):
        assert not hasattr(adapter, forbidden)


def test_submit_returns_reconciled_state():
    adapter = PaperExecutionAdapter(positions=_position_manager())
    result = adapter.submit(_intent(), quote={"id": "q", "quantity": "0.25"}, policy=object())
    assert result == "RECONCILED"
