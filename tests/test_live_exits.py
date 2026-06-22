from decimal import Decimal
from types import SimpleNamespace

import pytest

from magic_agent.live_exits import TwakSellPorts
from magic_agent.position_manager import ReconciledPosition
from magic_agent.twak import TwakError


class FakeTwak:
    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        return {"success": True, "data": {"tx_hash": "0xsell"}}


class RaisingTwak:
    """Quotes succeed; the non-quote-only (execute) sell raises TwakError."""

    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        if "--quote-only" not in args:
            raise TwakError("twak reported failure")
        return {"success": True, "data": {"tx_hash": "0xsell"}}


def test_twak_sell_ports_quote_and_execute_sell():
    twak = FakeTwak()
    position = ReconciledPosition(
        intent_id="intent-1",
        quantity=Decimal("2"),
        identity_key="0xtoken",
        symbol="ZEC/USDT",
    )
    book = [position]
    ports = TwakSellPorts(twak=twak, book=book, chain="bsc")
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
    # PARTIAL reduction (1.5 < 2): position shrinks in place, other fields preserved.
    assert len(book) == 1
    assert book[0].quantity == Decimal("0.5")
    assert book[0].identity_key == "0xtoken"


def test_twak_sell_full_close_removes_position_from_book():
    twak = FakeTwak()
    position = ReconciledPosition(
        intent_id="intent-1",
        quantity=Decimal("2"),
        identity_key="0xtoken",
        symbol="ZEC/USDT",
    )
    book = [position]
    ports = TwakSellPorts(twak=twak, book=book, chain="bsc")
    decision = SimpleNamespace(exit_quantity=Decimal("2"))

    quote = ports.sell_probe(position, Decimal("2"))
    ports.execute(position, decision, quote)

    # FULL close (2 >= 2): position removed from the book.
    assert book == []


def test_twak_sell_execute_leaves_book_untouched_if_sell_raises():
    twak = RaisingTwak()
    position = ReconciledPosition(
        intent_id="intent-1",
        quantity=Decimal("2"),
        identity_key="0xtoken",
        symbol="ZEC/USDT",
    )
    book = [position]
    ports = TwakSellPorts(twak=twak, book=book, chain="bsc")
    decision = SimpleNamespace(exit_quantity=Decimal("2"))

    # The real sell raises BEFORE any book mutation runs (sell-first-then-mutate).
    with pytest.raises(TwakError):
        ports.execute(position, decision, quote=None)

    assert book == [position]
