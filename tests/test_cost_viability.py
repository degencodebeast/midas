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


def test_cost_viability_fails_closed_on_nonfinite_numeric_field():
    nan_decision = evaluate_cost_viability(
        buy_quote=_quote(impact_bps="NaN"),
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert nan_decision.approved is False
    assert "buy_malformed_impact_bps" in nan_decision.denied_by

    inf_decision = evaluate_cost_viability(
        buy_quote=_quote(slippage_bps="Infinity"),
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert inf_decision.approved is False
    assert "buy_malformed_slippage_bps" in inf_decision.denied_by


def _live_quote(**changes):
    # REAL twak live quote normalized shape: priceImpact + output/minReceived spread.
    # NO gas_usd/fee_usd/impact_bps/slippage_bps/expires_at/notional_usd.
    quote = {
        "output_qty": "7.06622917239096244",
        "minimum_output": "6.995566880667052816",
        "price_impact": "0",
        "provider": "0x",
    }
    quote.update(changes)
    return quote


def test_cost_viability_computes_live_round_trip_from_spread():
    # Per leg: spread bps = (out - min)/out * 10000 ~= 100 bps; priceImpact "0" -> 0.
    # Round trip (buy + sell) ~= 200 bps. With a generous cap it must be APPROVED and
    # the evidence must report the spread-derived round-trip cost.
    decision = evaluate_cost_viability(
        buy_quote=_live_quote(),
        sell_quote=_live_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("500")),
    )

    assert decision.approved is True, decision.denied_by
    rt = Decimal(decision.evidence["estimated_round_trip_cost_bps"])
    # ~200 bps from two ~100 bps spreads, priceImpact 0.
    assert Decimal("180") < rt < Decimal("220")


def test_cost_viability_denies_too_wide_live_spread():
    # A very wide spread (min much smaller than out) blows past the cap.
    wide = _live_quote(output_qty="100", minimum_output="80")  # 2000 bps spread/leg
    decision = evaluate_cost_viability(
        buy_quote=wide,
        sell_quote=wide,
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("150")),
    )

    assert decision.approved is False
    assert decision.denied_by == ("round_trip_cost_too_high",)


def test_cost_viability_live_quote_nonzero_priceimpact_fails_closed():
    # The live twak quote's priceImpact UNIT is unconfirmed (only a "0" sample seen).
    # Guessing it is percent could understate impact 100x if it is a fraction. Until a
    # real non-zero-impact quote pins the unit, ANY nonzero priceImpact must FAIL CLOSED
    # regardless of how generous the cap or how tight the spread is.
    decision = evaluate_cost_viability(
        buy_quote=_live_quote(price_impact="0.5"),
        sell_quote=_live_quote(price_impact="0.5"),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("100000")),
    )

    assert decision.approved is False
    assert any("price_impact_unit_unconfirmed" in d for d in decision.denied_by), decision.denied_by


def test_cost_viability_live_quote_not_denied_for_missing_expires_at():
    # The real CLI omits expires_at; a live quote must NOT be denied for expiry.
    decision = evaluate_cost_viability(
        buy_quote=_live_quote(),
        sell_quote=_live_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("500")),
    )

    assert not any("expired" in d or "expires_at" in d for d in decision.denied_by)


def test_cost_viability_fails_closed_when_neither_schema_present():
    # A quote with neither the paper fields nor the live fields must fail closed.
    decision = evaluate_cost_viability(
        buy_quote={"provider": "0x", "price": "1"},
        sell_quote={"provider": "0x", "price": "1"},
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert decision.approved is False
    assert decision.denied_by != ()


def test_cost_viability_handles_naive_now_without_crash():
    decision = evaluate_cost_viability(
        buy_quote=_quote(),
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00",
    )

    assert decision.approved is True
    assert decision.denied_by == ()
