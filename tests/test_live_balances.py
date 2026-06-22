from decimal import Decimal
from types import SimpleNamespace

import pytest

from magic_agent.live_balances import TwakBalanceReader, StaticRpcClient


_APE = "0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79"


class FakeRegistry:
    def __init__(self, mapping):
        self._mapping = mapping

    def by_contract_key(self, identity_key):
        return SimpleNamespace(contract_address=self._mapping[identity_key])


# REAL twak 0.19.1 `wallet balance --chain bsc --json` with NO tokens (gas only).
def _empty_tokens_payload():
    return {
        "chain": "bsc",
        "address": "0xFC30",
        "symbol": "BNB",
        "available": "0.0343629434",
        "total": "0.0343629434",
        "totalUsd": 20.52,
        "tokens": [],
    }


class FakeTwak:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        return self.payload


def _reader(twak):
    return TwakBalanceReader(
        twak=twak,
        registry=FakeRegistry({"zec-bsc": _APE}),
        stable_symbol="USDC",
        chain="bsc",
    )


def test_balance_command_drops_token_flag():
    twak = FakeTwak(_empty_tokens_payload())
    reader = _reader(twak)

    reader.snapshot("zec-bsc")

    assert twak.calls == [["wallet", "balance", "--chain", "bsc", "--json"]]
    assert "--token" not in twak.calls[0]


def test_empty_tokens_returns_zero_stable_and_token_safely():
    twak = FakeTwak(_empty_tokens_payload())
    reader = _reader(twak)

    snapshot = reader.snapshot("zec-bsc")

    # No USDC and no target token held -> zero, fail-safe (no crash).
    assert snapshot["stable"] == Decimal("0")
    assert snapshot["token"] == Decimal("0")
    # Native BNB gas exposed for gas checks.
    assert snapshot["native"] == Decimal("0.0343629434")
    assert snapshot["native_symbol"] == "BNB"


@pytest.mark.skip(reason="tokens[] entry schema UNVERIFIED pending a real funded balance JSON")
def test_populated_tokens_extracts_stable_and_target():
    # PENDING: the exact field names of a populated tokens[] entry are unverified.
    # When a funded balance JSON is captured, confirm symbol/contract + amount fields
    # and enable this. The parser is best-effort against obvious field names.
    payload = _empty_tokens_payload()
    payload["tokens"] = [
        {"symbol": "USDC", "balance": "1000"},
        {"symbol": "APE", "contractAddress": _APE, "balance": "3.5"},
    ]
    twak = FakeTwak(payload)
    reader = _reader(twak)

    snapshot = reader.snapshot("zec-bsc")

    assert snapshot["stable"] == Decimal("1000")
    assert snapshot["token"] == Decimal("3.5")


def test_static_rpc_client_waits_and_counts_confirmations():
    rpc = StaticRpcClient(
        wallet_nonce_value=7,
        receipt={"status": "0x1", "blockNumber": "0x10"},
        confirmation_count=3,
    )

    assert rpc.wallet_nonce() == 7
    assert rpc.wait_receipt("0xabc") == {"status": "0x1", "blockNumber": "0x10"}
    assert rpc.confirmations({"blockNumber": "0x10"}) == 3
