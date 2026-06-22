from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.app import build_app
from magic_agent.runner import run_cycle
from magic_agent.spot_models import AuthorizedSetup


class _CostGate:
    def __init__(self, approved=True):
        self.approved = approved
        self.calls = 0

    def evaluate(self, *, buy_quote, sell_quote, intended_risk_fraction, now):
        self.calls += 1
        return SimpleNamespace(
            approved=self.approved,
            denied_by=() if self.approved else ("round_trip_cost_too_high",),
            evidence={
                "approved": self.approved,
                "estimated_round_trip_cost_bps": "76",
                "intended_risk_fraction": str(intended_risk_fraction),
            },
        )


def _authorizing_app(tmp_path, cost_gate):
    setup = AuthorizedSetup.example(grade="A", raw_grade="A")
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=SimpleNamespace(scan=lambda candidate: setup),
        gold_candidate_symbol="ZEC",
    )
    app.cost_viability = cost_gate
    return app


def test_reconciled_canary_promotes_to_normal_scoring(tmp_path):
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app = _authorizing_app(tmp_path, _CostGate(approved=True))

    run_cycle(app, now)

    assert app.state.canary_completed is True
    assert app.state.canary_mode is False
    assert app.state.canary_intent_id is not None
    assert app.state.promotion_reason == "canary_reconciled_cost_viable"
    assert app.state.cost_viability_evidence["estimated_round_trip_cost_bps"] == "76"
    assert app.state.live_mode_state()["mode"] == "normal_scoring"


def test_failed_cost_viability_blocks_promotion(tmp_path):
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app = _authorizing_app(tmp_path, _CostGate(approved=False))

    run_cycle(app, now)

    assert app.state.canary_completed is True
    assert app.state.canary_mode is True
    assert app.state.promotion_reason == "cost_viability_failed"
    assert app.state.cost_viability_evidence["approved"] is False


def test_normal_scoring_uses_grade_sizing_after_promotion(tmp_path):
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app = _authorizing_app(tmp_path, _CostGate(approved=True))

    run_cycle(app, now)
    first_fraction = app.execution_coordinator.confirmed_records()[0].evidence["policy"].risk_fraction

    app.position_manager.book.clear()
    run_cycle(app, now)
    second_fraction = app.execution_coordinator.confirmed_records()[1].evidence["policy"].risk_fraction

    assert first_fraction == Decimal("0.0025")
    assert second_fraction == Decimal("0.005")
