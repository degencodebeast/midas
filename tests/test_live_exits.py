from decimal import Decimal
from types import SimpleNamespace

from magic_agent.live_exits import TwakSellPorts
from magic_agent.position_manager import ReconciledPosition


class FakeTwak:
    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        return {"success": True, "data": {"tx_hash": "0xsell"}}


def test_twak_sell_ports_quote_and_execute_sell():
    twak = FakeTwak()
    ports = TwakSellPorts(twak=twak, chain="bsc")
    position = ReconciledPosition(
        intent_id="intent-1",
        quantity=Decimal("2"),
        identity_key="0xtoken",
        symbol="ZEC/USDT",
    )
    decision = SimpleNamespace(exit_quantity=Decimal("1.5"))

    quote = ports.sell_probe(position, Decimal("1.5"))
    ports.execute(position, decision, quote)

    assert quote.approved is True
    assert twak.calls[0] == [
        "swap", "1.5", "0xtoken", "USDC",
        "--chain", "bsc", "--quote-only", "--sell", "--json",
    ]
    assert twak.calls[1] == [
        "swap", "1.5", "0xtoken", "USDC",
        "--chain", "bsc", "--sell", "--json",
    ]
