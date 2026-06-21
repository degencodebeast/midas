from dataclasses import dataclass

from decimal import Decimal
from types import SimpleNamespace

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.lifecycle import LifecycleEvaluator, LifecycleObservation, PositionView
from magic_agent.position_manager import PositionManager
from magic_agent.risk_policy import PortfolioRiskState


@dataclass(frozen=True)
class _Position:
    position_id: str = "p-1"
    quantity: Decimal = Decimal("2")
    stressed_loss_per_unit: Decimal = Decimal("10")
    stop: float = 90.0
    campaign_dol: float = 120.0


@dataclass(frozen=True)
class _Quote:
    approved: bool


def test_entry_halts_do_not_block_stop_exit():
    executed = []
    position = _Position()
    manager = PositionManager(
        positions=lambda: [position],
        observe=lambda p, now, reduction: LifecycleObservation(
            None, None, PositionView(p.position_id, p.quantity, Decimal("90"), Decimal("120")),
            Decimal("89"), Decimal("101"), False, reduction.reduction_qty, True,
        ),
        evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=SimpleNamespace(reduction_for=lambda p, state: SimpleNamespace(reduction_qty=Decimal("0"))),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda p, quantity: _Quote(True),
        execute=lambda p, decision, quote: executed.append((p, decision, quote)),
    )
    manager.process_exits("2026-06-21T00:00:00Z")
    assert executed[0][1].reason == "stop"
    assert executed[0][1].exit_quantity == Decimal("2")


def test_failed_sell_keeps_position_managed():
    alerts = []
    position = _Position()
    manager = PositionManager(
        positions=lambda: [position], observe=lambda p, now, reduction: LifecycleObservation(
            None, None, PositionView(p.position_id, p.quantity, Decimal("90"), Decimal("120")),
            Decimal("89"), Decimal("101"), False, reduction.reduction_qty, False,
        ), evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=SimpleNamespace(reduction_for=lambda p, state: SimpleNamespace(reduction_qty=Decimal("0"))),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda p, quantity: _Quote(False), execute=lambda *args: None,
        alert=lambda code, p: alerts.append(code),
    )
    manager.process_exits("2026-06-21T00:00:00Z")
    assert alerts == ["protective_exit_unquotable"]


def test_failed_exit_retains_position_for_retry():
    """A failed protective exit must not drop or close the position.

    The position stays in the iteration source and the exit is never executed,
    so a later cycle can retry it. No position is silently assumed-closed.
    """
    executed = []
    position = _Position()
    manager = PositionManager(
        positions=lambda: [position],
        observe=lambda p, now, reduction: LifecycleObservation(
            None, None, PositionView(p.position_id, p.quantity, Decimal("90"), Decimal("120")),
            Decimal("89"), Decimal("101"), False, reduction.reduction_qty, True,
        ),
        evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=SimpleNamespace(reduction_for=lambda p, state: SimpleNamespace(reduction_qty=Decimal("0"))),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda p, quantity: _Quote(False),
        execute=lambda p, decision, quote: executed.append(p),
    )
    manager.process_exits("2026-06-21T00:00:00Z")
    # Exit never executed: position is retained, not assumed-closed.
    assert executed == []
    # The same position is still surfaced by the source for a retry cycle.
    assert manager.positions() == [position]


def test_protective_exit_fires_while_entries_halted():
    """A campaign-DOL exit must fire even when entries are fully halted."""
    executed = []
    position = _Position()
    manager = PositionManager(
        positions=lambda: [position],
        observe=lambda p, now, reduction: LifecycleObservation(
            None, None, PositionView(p.position_id, p.quantity, Decimal("90"), Decimal("120")),
            Decimal("100"), Decimal("120"), False, reduction.reduction_qty, True,
        ),
        evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=SimpleNamespace(reduction_for=lambda p, state: SimpleNamespace(reduction_qty=Decimal("0"))),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda p, quantity: _Quote(True),
        execute=lambda p, decision, quote: executed.append((p, decision, quote)),
    )
    manager.process_exits("2026-06-21T00:00:00Z")
    assert executed[0][1].reason == "campaign_dol"
    assert executed[0][1].exit_quantity == Decimal("2")


def test_open_from_reconciliation_books_reconciled_quantity():
    """Positions open only from reconciliation, using the reconciled on-chain qty."""
    manager = PositionManager(
        positions=lambda: [], observe=lambda p, now, reduction: None,
        evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=SimpleNamespace(reduction_for=lambda p, state: SimpleNamespace(reduction_qty=Decimal("0"))),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda p, quantity: _Quote(True), execute=lambda *args: None,
    )
    intent = SimpleNamespace(intent_id="intent:x")
    booked = manager.open_from_reconciliation(intent, Decimal("3"))
    assert booked.quantity == Decimal("3")
    assert booked in manager.book
