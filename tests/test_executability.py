from decimal import Decimal

from magic_agent.executability import (
    ExecutabilityProbe,
    prepare_exact_order,
    validate_round_trip,
)
from magic_agent.risk_policy import (
    MarketRiskContext,
    PortfolioRiskState,
    QuantityCaps,
    RiskConfig,
    RiskPolicy,
)
from magic_agent.spot_models import AuthorizedSetup


def _quote(**overrides):
    base = {
        "output_qty": "10",
        "provider": "rango",
        "minimum_output": "9.8",
        "impact_bps": "12",
        "slippage_bps": "20",
        "expires_at": "2026-06-21T00:01:00Z",
    }
    base.update(overrides)
    return base


def test_complete_round_trip_is_approved():
    result = validate_round_trip(_quote(), _quote(), now="2026-06-21T00:00:00Z")
    assert result.approved is True
    assert result.reasons == ()


def test_missing_buy_quote_fails_closed():
    result = validate_round_trip(None, _quote(), now="2026-06-21T00:00:00Z")
    assert result.approved is False
    assert "missing_buy_quote" in result.reasons


def test_expired_quote_fails_closed():
    stale = _quote(expires_at="2026-06-20T23:59:00Z")
    result = validate_round_trip(stale, _quote(), now="2026-06-21T00:00:00Z")
    assert result.approved is False
    assert "buy_quote_expired" in result.reasons


def test_missing_impact_field_fails_closed():
    incomplete = _quote()
    del incomplete["impact_bps"]
    result = validate_round_trip(incomplete, _quote(), now="2026-06-21T00:00:00Z")
    assert result.approved is False
    assert "buy_missing_impact_bps" in result.reasons


def test_nonpositive_output_fails_closed():
    bad = _quote(output_qty="0")
    result = validate_round_trip(bad, _quote(), now="2026-06-21T00:00:00Z")
    assert result.approved is False
    assert "buy_nonpositive_output" in result.reasons


def test_prepared_order_quote_quantity_matches_final_qty():
    def quote_provider(setup, quantity):
        return type(
            "Quote",
            (),
            {
                "approved": True,
                "reasons": (),
                "quantity_caps": QuantityCaps.unbounded(),
                "quote": {"quantity": None if quantity is None else str(quantity)},
            },
        )()

    probe = ExecutabilityProbe(quote_provider)
    prepared = probe.prepare_order(
        setup=AuthorizedSetup.example(),
        market=MarketRiskContext.aligned(),
        risk_state=PortfolioRiskState.example(),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
    )
    assert prepared.approved
    assert Decimal(prepared.quote["quantity"]) == prepared.risk.final_qty


def test_quote_size_non_convergence_fails_closed():
    # An exact-size refresh whose caps keep clamping to a different,
    # ever-shrinking quantity must never converge: a stale/size-mismatched
    # quote is denied (fail closed) rather than submitted.
    calls = {"n": 0}

    def quote_provider(setup, quantity):
        # First call is unbounded capacity discovery; subsequent exact-size
        # refreshes clamp to a strictly smaller liquidity cap each round so
        # revised.final_qty never equals the prior risk-sized quantity.
        if quantity is None:
            caps = QuantityCaps.unbounded()
        else:
            calls["n"] += 1
            shrink = Decimal("0.1") / (Decimal(10) ** calls["n"])
            maximum = Decimal("Infinity")
            caps = QuantityCaps(shrink, maximum, maximum, maximum, Decimal("0"))
        return type(
            "Quote",
            (),
            {
                "approved": True,
                "reasons": (),
                "quantity_caps": caps,
                "quote": {"quantity": None if quantity is None else str(quantity)},
            },
        )()

    prepared = prepare_exact_order(
        setup=AuthorizedSetup.example(),
        market=MarketRiskContext.aligned(),
        risk_state=PortfolioRiskState.example(),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
        quote_provider=quote_provider,
    )
    assert prepared.approved is False
    assert "quote_size_did_not_converge" in prepared.reasons


def test_capacity_probe_denied_fails_closed():
    def quote_provider(setup, quantity):
        return type(
            "Quote",
            (),
            {
                "approved": False,
                "reasons": ("no_route",),
                "quantity_caps": QuantityCaps.unbounded(),
                "quote": None,
            },
        )()

    prepared = prepare_exact_order(
        setup=AuthorizedSetup.example(),
        market=MarketRiskContext.aligned(),
        risk_state=PortfolioRiskState.example(),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
        quote_provider=quote_provider,
    )
    assert prepared.approved is False
    assert "no_route" in prepared.reasons
