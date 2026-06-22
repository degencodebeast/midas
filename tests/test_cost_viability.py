from decimal import Decimal

from magic_agent.cost_viability import CostViabilityConfig, evaluate_cost_viability


def _quote(**changes):
    quote = {
        "output_qty": "10",
        "provider": "twak",
        "minimum_output": "9.95",
        "impact_bps": "12",
        "slippage_bps": "14",
        "expires_at": "2026-06-22T12:05:00Z",
        "gas_usd": "0.04",
        "fee_usd": "0.02",
        "notional_usd": "100",
    }
    quote.update(changes)
    return quote


def test_cost_viability_approves_reasonable_round_trip_costs():
    decision = evaluate_cost_viability(
        buy_quote=_quote(),
        sell_quote=_quote(impact_bps="10", slippage_bps="11"),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("150")),
    )

    assert decision.approved is True
    assert decision.denied_by == ()
    assert decision.evidence["estimated_round_trip_cost_bps"] == "59.00"


def test_cost_viability_fails_closed_on_missing_quote():
    decision = evaluate_cost_viability(
        buy_quote=None,
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert decision.approved is False
    assert "missing_buy_quote" in decision.denied_by


def test_cost_viability_fails_closed_on_malformed_quote():
    bad = _quote()
    bad.pop("notional_usd")

    decision = evaluate_cost_viability(
        buy_quote=bad,
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert decision.approved is False
    assert "buy_missing_notional_usd" in decision.denied_by


def test_cost_viability_fails_closed_on_expired_quote():
    decision = evaluate_cost_viability(
        buy_quote=_quote(expires_at="2026-06-22T11:59:59Z"),
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert decision.approved is False
    assert "buy_quote_expired" in decision.denied_by


def test_cost_viability_denies_costs_that_dominate_tiny_trade():
    decision = evaluate_cost_viability(
        buy_quote=_quote(gas_usd="2.50", fee_usd="2.50", notional_usd="20"),
        sell_quote=_quote(gas_usd="2.50", fee_usd="2.50", notional_usd="20"),
        intended_risk_fraction=Decimal("0.0025"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("150")),
    )

    assert decision.approved is False
    assert decision.denied_by == ("round_trip_cost_too_high",)
