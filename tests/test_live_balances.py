from decimal import Decimal

import pytest

from magic_agent.live_balances import TwakBalanceReader, StaticRpcClient
from magic_agent.twak import TwakError


class FakeTwak:
    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        return {
            "success": True,
            "data": {
                "stable": "1000",
                "token": "3.5",
            },
        }


def test_twak_balance_reader_returns_decimal_snapshot():
    twak = FakeTwak()
    reader = TwakBalanceReader(twak=twak, stable_symbol="USDC", chain="bsc")

    snapshot = reader.snapshot("0xtoken")

    assert snapshot == {"stable": Decimal("1000"), "token": Decimal("3.5")}
    assert twak.calls == [["wallet", "balance", "--chain", "bsc", "--token", "0xtoken", "--json"]]


class MissingTokenTwak:
    def json(self, args, *, timeout=60):
        return {"success": True, "data": {"stable": "1000"}}


class NonNumericTwak:
    def json(self, args, *, timeout=60):
        return {"success": True, "data": {"stable": "abc", "token": "1"}}


def test_twak_balance_reader_fails_closed_on_missing_field():
    reader = TwakBalanceReader(twak=MissingTokenTwak(), stable_symbol="USDC", chain="bsc")

    with pytest.raises(TwakError) as excinfo:
        reader.snapshot("0xtoken")

    assert "token" in str(excinfo.value)


def test_twak_balance_reader_fails_closed_on_non_numeric_field():
    reader = TwakBalanceReader(twak=NonNumericTwak(), stable_symbol="USDC", chain="bsc")

    with pytest.raises(TwakError) as excinfo:
        reader.snapshot("0xtoken")

    assert "stable" in str(excinfo.value)


def test_static_rpc_client_waits_and_counts_confirmations():
    rpc = StaticRpcClient(wallet_nonce_value=7, receipt={"status": "0x1", "blockNumber": "0x10"}, confirmation_count=3)

    assert rpc.wallet_nonce() == 7
    assert rpc.wait_receipt("0xabc") == {"status": "0x1", "blockNumber": "0x10"}
    assert rpc.confirmations({"blockNumber": "0x10"}) == 3
