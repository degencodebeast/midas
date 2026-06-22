from decimal import Decimal

import pytest

from magic_agent.live_quotes import TwakQuoteProvider
from magic_agent.risk_policy import QuantityCaps
from magic_agent.spot_models import AuthorizedSetup


class FakeTwak:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append((args, timeout))
        return self.payload


def _payload(**changes):
    data = {
        "output_qty": "10",
        "minimum_output": "9.95",
        "impact_bps": "12",
        "slippage_bps": "14",
        "expires_at": "2026-06-22T12:05:00Z",
        "gas_usd": "0.04",
        "fee_usd": "0.02",
        "notional_usd": "100",
        "asset": "USDC",
        "network": "bsc",
    }
    data.update(changes)
    return {"success": True, "data": data}


def test_twak_quote_provider_returns_prepare_exact_order_surface_for_buy():
    twak = FakeTwak(_payload())
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is True
    assert result.reasons == ()
    assert result.quantity_caps == QuantityCaps.unbounded()
    assert result.quote["output_qty"] == "10"
    assert result.quote["gas_usd"] == "0.04"
    assert twak.calls == [(
        [
            "swap", "10", "USDC", "zec-bsc",
            "--chain", "bsc", "--quote-only", "--json",
        ],
        60,
    )]


def test_twak_quote_provider_uses_probe_quantity_when_quantity_is_none():
    twak = FakeTwak(_payload())
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), None)

    assert result.approved is True
    assert twak.calls[0][0][1] == "1"


def test_twak_quote_provider_fails_closed_on_unexpected_network_or_asset():
    twak = FakeTwak(_payload(asset="USDT", network="ethereum"))
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is False
    assert "unexpected_asset" in result.reasons
    assert "unexpected_network" in result.reasons
    assert result.quote is None


def test_twak_quote_provider_fails_closed_on_missing_cost_fields():
    bad = _payload()
    del bad["data"]["gas_usd"]
    twak = FakeTwak(bad)
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is False
    assert result.reasons == ("missing_gas_usd",)
    assert result.quote is None


def test_twak_quote_provider_fails_closed_on_non_numeric_required_field():
    twak = FakeTwak(_payload(output_qty="abc"))
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is False
    assert "malformed_output_qty" in result.reasons
    assert result.quote is None


def test_twak_quote_provider_fails_closed_on_nonpositive_required_field():
    twak = FakeTwak(_payload(output_qty="0"))
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is False
    assert "nonpositive_output_qty" in result.reasons
    assert result.quote is None
