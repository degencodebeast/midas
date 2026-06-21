from decimal import Decimal

from magic_agent.executability import PreparedOrder, validate_round_trip
from magic_agent.quotes import ExecutabilityAdapter, PaperQuoteProvider
from magic_agent.risk_policy import (
    MarketRiskContext,
    PortfolioRiskState,
    RiskConfig,
    RiskPolicy,
)
from magic_agent.spot_models import AuthorizedSetup

# Deterministic timestamps only — no wall-clock reads inside the provider.
_NOW = "2026-06-21T00:00:00Z"


def test_paper_provider_yields_fresh_two_sided_round_trip():
    provider = PaperQuoteProvider(now=_NOW)
    setup = AuthorizedSetup.example()
    result = provider(setup, Decimal("5"))
    # Exact-size probe must carry a single bound quote plus the contract surface.
    assert result.approved is True
    assert result.reasons == ()
    assert result.quote is not None
    # The bound quote must itself be a fresh, unexpired, complete round-trip leg.
    decision = validate_round_trip(result.quote, result.quote, now=_NOW)
    assert decision.approved is True
    assert decision.reasons == ()


def test_paper_provider_probe_size_is_two_sided_and_unexpired():
    provider = PaperQuoteProvider(now=_NOW)
    setup = AuthorizedSetup.example()
    # qty None is the capacity-discovery probe; it must still be approved.
    probe = provider(setup, None)
    assert probe.approved is True
    assert probe.reasons == ()


def test_paper_quote_is_priced_off_entry_with_decimal_money():
    provider = PaperQuoteProvider(now=_NOW)
    setup = AuthorizedSetup.example()  # entry == Decimal("100")
    qty = Decimal("3")
    result = provider(setup, qty)
    quote = result.quote
    assert quote["provider"] == "paper"
    # output_qty is the requested quantity; minimum_output is derived but positive.
    assert Decimal(str(quote["output_qty"])) == qty
    assert Decimal(str(quote["minimum_output"])) > 0
    assert Decimal(str(quote["minimum_output"])) <= qty
    assert Decimal(str(quote["impact_bps"])) >= 0
    assert Decimal(str(quote["slippage_bps"])) >= 0


def test_adapter_prepare_order_approves_healthy_setup_with_bound_quote():
    adapter = ExecutabilityAdapter(PaperQuoteProvider(now=_NOW))
    prepared = adapter.prepare_order(
        setup=AuthorizedSetup.example(),
        market=MarketRiskContext.aligned(),
        risk_state=PortfolioRiskState.example(),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
    )
    assert isinstance(prepared, PreparedOrder)
    assert prepared.approved is True
    assert prepared.quote is not None
    # The bound quote's quantity must equal the risk-sized final quantity.
    assert Decimal(str(prepared.quote["output_qty"])) == prepared.risk.final_qty


def test_adapter_with_expired_clock_fails_closed():
    # A provider whose quotes already expired (clock far in the past relative to
    # the TTL window) must produce a denied prepared order — fail closed.
    expired = "2020-01-01T00:00:00Z"
    adapter = ExecutabilityAdapter(PaperQuoteProvider(now=expired, valid_at=_NOW))
    prepared = adapter.prepare_order(
        setup=AuthorizedSetup.example(),
        market=MarketRiskContext.aligned(),
        risk_state=PortfolioRiskState.example(),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
    )
    assert prepared.approved is False
    assert prepared.quote is None
