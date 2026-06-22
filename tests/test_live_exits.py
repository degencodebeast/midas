from decimal import Decimal
from types import SimpleNamespace

import pytest

from magic_agent.live_exits import TwakSellPorts
from magic_agent.position_manager import ReconciledPosition
from magic_agent.twak import TwakError


_APE = "0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79"


class FakeRegistry:
    def __init__(self, mapping):
        self._mapping = mapping

    def by_contract_key(self, identity_key):
        return SimpleNamespace(contract_address=self._mapping[identity_key])


# REAL twak 0.19.1 SELL quote-only JSON (token -> USDC).
def _sell_quote_payload(**changes):
    data = {
        "input": "1 APE",
        "output": "0.138910921981003559 USDC",
        "minReceived": "0.137521812761193523 USDC",
        "provider": "LiquidMesh",
        "priceImpact": "0",
    }
    data.update(changes)
    return data


class FakeTwak:
    """Quote-only returns the real sell quote; execute returns a tx_hash."""

    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        if "--quote-only" in args:
            return _sell_quote_payload()
        return {"tx_hash": "0xsell"}


class RaisingTwak:
    """Quotes succeed; the non-quote-only (execute) sell raises TwakError."""

    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        if "--quote-only" not in args:
            raise TwakError("twak reported failure")
        return _sell_quote_payload()


def _ports(twak, book):
    return TwakSellPorts(
        twak=twak,
        book=book,
        registry=FakeRegistry({"zec-bsc": _APE}),
        chain="bsc",
    )


def _position():
    return ReconciledPosition(
        intent_id="intent-1",
        quantity=Decimal("2"),
        identity_key="zec-bsc",
        symbol="ZEC/USDT",
    )


def test_sell_probe_and_execute_emit_real_commands_without_sell_flag():
    twak = FakeTwak()
    position = _position()
    book = [position]
    ports = _ports(twak, book)
    decision = SimpleNamespace(exit_quantity=Decimal("1.5"))

    quote = ports.sell_probe(position, Decimal("1.5"))
    ports.execute(position, decision, quote)

    assert quote.approved is True
    # token (CONTRACT) -> USDC, NO --sell.
    assert twak.calls[0] == [
        "swap", "1.5", _APE, "USDC", "--chain", "bsc", "--quote-only", "--json",
    ]
    assert twak.calls[1] == [
        "swap", "1.5", _APE, "USDC", "--chain", "bsc", "--json",
    ]
    for args in twak.calls:
        assert "--sell" not in args
        assert "zec-bsc" not in args  # token arg is the contract, not identity_key
    # PARTIAL reduction (1.5 < 2): position shrinks in place, fields preserved.
    assert len(book) == 1
    assert book[0].quantity == Decimal("0.5")
    assert book[0].identity_key == "zec-bsc"


def test_sell_full_close_removes_position_from_book():
    twak = FakeTwak()
    position = _position()
    book = [position]
    ports = _ports(twak, book)
    decision = SimpleNamespace(exit_quantity=Decimal("2"))

    quote = ports.sell_probe(position, Decimal("2"))
    ports.execute(position, decision, quote)

    assert book == []


def test_sell_execute_leaves_book_untouched_if_sell_raises():
    twak = RaisingTwak()
    position = _position()
    book = [position]
    ports = _ports(twak, book)
    decision = SimpleNamespace(exit_quantity=Decimal("2"))

    with pytest.raises(TwakError):
        ports.execute(position, decision, quote=None)

    assert book == [position]


def test_sell_probe_fails_closed_on_nonpositive_quantity():
    twak = FakeTwak()
    ports = _ports(twak, [])

    quote = ports.sell_probe(_position(), Decimal("0"))

    assert quote.approved is False
    assert "nonpositive_exit_quantity" in quote.reasons
    assert twak.calls == []
