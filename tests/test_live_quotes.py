from decimal import Decimal
from pathlib import Path

import pytest

from magic_agent.live_quotes import TwakQuoteProvider
from magic_agent.risk_policy import QuantityCaps
from magic_agent.spot_models import AuthorizedSetup


_SRC = Path(__file__).resolve().parents[1] / "src" / "magic_agent"


def test_live_ports_contain_no_invalid_flags_or_hallucinated_required_fields():
    quotes = (_SRC / "live_quotes.py").read_text()
    exits = (_SRC / "live_exits.py").read_text()
    balances = (_SRC / "live_balances.py").read_text()
    for text in (quotes, exits, balances):
        assert "--sell" not in text
        assert "--token" not in text
    # identity_key must never be used directly as the swap token arg.
    assert "setup.identity_key" not in quotes or "by_contract_key" in quotes
    # The real quote response has none of these — they must not be REQUIRED.
    for field in ("gas_usd", "fee_usd", "expires_at"):
        assert f"missing_{field}" not in quotes
        assert f'"{field}"' not in quotes


class FakeTwak:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append((args, timeout))
        return self.payload


class FakeRegistry:
    """Resolves identity_key -> a record carrying the BSC contract address."""

    def __init__(self, mapping):
        self._mapping = mapping

    def by_contract_key(self, identity_key):
        from types import SimpleNamespace

        return SimpleNamespace(contract_address=self._mapping[identity_key])


_APE = "0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79"


def _registry(identity_key="zec-bsc", contract=_APE):
    return FakeRegistry({identity_key: contract})


# REAL twak 0.19.1 BUY quote-only JSON (whole response, no success/data wrapper).
def _buy_payload(**changes):
    data = {
        "input": "1 USDC",
        "output": "7.06622917239096244 APE",
        "minReceived": "6.995566880667052816 APE",
        "provider": "0x",
        "priceImpact": "0",
    }
    data.update(changes)
    return data


def _provider(twak, registry=None):
    return TwakQuoteProvider(
        twak=twak,
        registry=registry or _registry(),
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )


def test_buy_quote_emits_real_command_with_contract_address():
    twak = FakeTwak(_buy_payload())
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("1"))

    assert result.approved is True
    assert result.reasons == ()
    assert result.quantity_caps == QuantityCaps.unbounded()
    # SOURCE amount first, then USDC, then the resolved CONTRACT ADDRESS (not identity_key).
    assert twak.calls == [(
        ["swap", "1", "USDC", _APE, "--chain", "bsc", "--quote-only", "--json"],
        60,
    )]
    # No invalid flags anywhere.
    args = twak.calls[0][0]
    assert "--sell" not in args
    assert "--token" not in args
    assert "zec-bsc" not in args


def test_buy_quote_parses_real_schema_into_normalized_quote():
    twak = FakeTwak(_buy_payload())
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("1"))

    quote = result.quote
    assert quote is not None
    assert Decimal(str(quote["output_qty"])) == Decimal("7.06622917239096244")
    assert quote["output_symbol"] == "APE"
    assert Decimal(str(quote["minimum_output"])) == Decimal("6.995566880667052816")
    # price = usdc_in / output_qty
    assert Decimal(str(quote["price"])) == Decimal("1") / Decimal("7.06622917239096244")
    assert quote["provider"] == "0x"
    assert Decimal(str(quote["price_impact"])) == Decimal("0")
    assert quote["input"] == "1 USDC"
    assert quote["output"] == "7.06622917239096244 APE"
    assert quote["minReceived"] == "6.995566880667052816 APE"


def test_buy_quote_does_not_require_hallucinated_cost_fields():
    # The real response has none of gas_usd/fee_usd/expires_at/asset/network — yet
    # a valid real payload must still be APPROVED.
    twak = FakeTwak(_buy_payload())
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("1"))

    assert result.approved is True


def test_uses_probe_quantity_when_quantity_is_none():
    twak = FakeTwak(_buy_payload())
    provider = _provider(twak)

    provider(AuthorizedSetup.example(identity_key="zec-bsc"), None)

    assert twak.calls[0][0][1] == "1"


def test_buy_quote_fails_closed_on_nonpositive_quantity():
    twak = FakeTwak(_buy_payload())
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("0"))

    assert result.approved is False
    assert "nonpositive_quote_quantity" in result.reasons
    assert result.quote is None


def test_buy_quote_fails_closed_on_error_response():
    twak = FakeTwak({"error": "API error: 400 Bad Request", "errorCode": "NETWORK_ERROR"})
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("1"))

    assert result.approved is False
    assert "twak_error" in result.reasons
    assert result.quote is None


@pytest.mark.parametrize("missing", ["input", "output", "minReceived"])
def test_buy_quote_fails_closed_on_missing_required_field(missing):
    payload = _buy_payload()
    del payload[missing]
    twak = FakeTwak(payload)
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("1"))

    assert result.approved is False
    assert f"missing_{missing}" in result.reasons
    assert result.quote is None


@pytest.mark.parametrize("bad", ["notanumber APE", "APE", "", "7.0"])
def test_buy_quote_fails_closed_on_malformed_amount_string(bad):
    twak = FakeTwak(_buy_payload(output=bad))
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("1"))

    assert result.approved is False
    assert result.quote is None


def test_buy_quote_fails_closed_on_nonpositive_output():
    twak = FakeTwak(_buy_payload(output="0 APE"))
    provider = _provider(twak)

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("1"))

    assert result.approved is False
    assert "nonpositive_output" in result.reasons
    assert result.quote is None
