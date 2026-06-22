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


# REAL twak 0.19.1 populated `wallet balance --chain bsc --json`: each tokens[]
# entry is {"symbol","contract","balance"}. BSC USDC contract verified below.
_BSC_USDC = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"


def _populated_payload():
    return {
        "chain": "bsc",
        "address": "0xFC30FA0956e26deBB273eBCb5b74bcDB21e138C6",
        "symbol": "BNB",
        "available": "0.0176629434",
        "total": "0.0176629434",
        "totalUsd": 10.62,
        "tokens": [
            {
                "symbol": "USDC",
                "contract": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                "balance": "10.034925928487288539",
            }
        ],
    }


def test_populated_tokens_extracts_stable_and_target():
    # REAL captured funded balance: stable (USDC) matched by SYMBOL, target matched
    # by CONTRACT address, quantity read from "balance".
    payload = _populated_payload()
    # Add the target token matched by its contract (registry maps "zec-bsc" -> _APE).
    payload["tokens"].append(
        {"symbol": "APE", "contract": _APE, "balance": "3.5"}
    )
    twak = FakeTwak(payload)
    reader = _reader(twak)

    snapshot = reader.snapshot("zec-bsc")

    assert snapshot["stable"] == Decimal("10.034925928487288539")
    assert snapshot["token"] == Decimal("3.5")


def test_target_token_matched_by_contract_case_insensitive():
    payload = _populated_payload()
    payload["tokens"].append(
        {"symbol": "APE", "contract": _APE.lower(), "balance": "7.25"}
    )
    twak = FakeTwak(payload)
    reader = _reader(twak)

    snapshot = reader.snapshot("zec-bsc")

    assert snapshot["token"] == Decimal("7.25")


def test_token_absent_from_tokens_returns_zero_for_that_side():
    # USDC held but the target token is NOT held -> token side is zero (not-held safe).
    payload = _populated_payload()
    twak = FakeTwak(payload)
    reader = _reader(twak)

    snapshot = reader.snapshot("zec-bsc")

    assert snapshot["stable"] == Decimal("10.034925928487288539")
    assert snapshot["token"] == Decimal("0")


def test_malformed_balance_fails_closed():
    payload = _populated_payload()
    payload["tokens"] = [
        {"symbol": "USDC", "contract": _BSC_USDC, "balance": "not-a-number"}
    ]
    twak = FakeTwak(payload)
    reader = _reader(twak)

    from magic_agent.twak import TwakError

    with pytest.raises(TwakError):
        reader.snapshot("zec-bsc")


def test_wallet_equity_sums_native_usd_and_usdc_balance():
    # REAL captured BNB+USDC wallet: equity = native totalUsd (10.62) + USDC balance;
    # cash = the deployable USDC balance only.
    twak = FakeTwak(_populated_payload())
    reader = _reader(twak)

    equity = reader.wallet_equity()

    assert equity["equity_usd"] == Decimal("10.62") + Decimal("10.034925928487288539")
    assert equity["cash_usd"] == Decimal("10.034925928487288539")


def test_wallet_equity_empty_tokens_cash_zero_equity_native():
    twak = FakeTwak(_empty_tokens_payload())
    reader = _reader(twak)

    equity = reader.wallet_equity()

    # No USDC held -> cash 0; equity is just the native USD.
    assert equity["cash_usd"] == Decimal("0")
    assert equity["equity_usd"] == Decimal("20.52")


def test_wallet_equity_malformed_total_usd_fails_closed():
    payload = _populated_payload()
    payload["totalUsd"] = "not-a-number"
    twak = FakeTwak(payload)
    reader = _reader(twak)

    from magic_agent.twak import TwakError

    with pytest.raises(TwakError):
        reader.wallet_equity()


def test_wallet_equity_missing_total_usd_fails_closed():
    payload = _populated_payload()
    del payload["totalUsd"]
    twak = FakeTwak(payload)
    reader = _reader(twak)

    from magic_agent.twak import TwakError

    with pytest.raises(TwakError):
        reader.wallet_equity()


def test_wallet_equity_malformed_usdc_balance_fails_closed():
    payload = _populated_payload()
    payload["tokens"] = [
        {"symbol": "USDC", "contract": _BSC_USDC, "balance": "not-a-number"}
    ]
    twak = FakeTwak(payload)
    reader = _reader(twak)

    from magic_agent.twak import TwakError

    with pytest.raises(TwakError):
        reader.wallet_equity()


def test_static_rpc_client_waits_and_counts_confirmations():
    rpc = StaticRpcClient(
        wallet_nonce_value=7,
        receipt={"status": "0x1", "blockNumber": "0x10"},
        confirmation_count=3,
    )

    assert rpc.wallet_nonce() == 7
    assert rpc.wait_receipt("0xabc") == {"status": "0x1", "blockNumber": "0x10"}
    assert rpc.confirmations({"blockNumber": "0x10"}) == 3
