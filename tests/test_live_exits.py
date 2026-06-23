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


class HashTwak:
    """Execute returns a REAL-shaped sell response with a top-level ``hash``."""

    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        if "--quote-only" in args:
            return _sell_quote_payload()
        # Real executed sell: tx hash is the TOP-LEVEL "hash" field.
        return {
            "input": "1 APE",
            "output": "0.138 USDC",
            "minReceived": "0.137 USDC",
            "provider": "LiquidMesh",
            "priceImpact": "0",
            "hash": "0x88b0deadbeef",
            "fromChain": "bsc",
            "toChain": "bsc",
            "explorer": "https://bscscan.com/tx/0x88b0deadbeef",
        }


def test_sell_execute_returns_real_top_level_hash():
    twak = HashTwak()
    position = _position()
    book = [position]
    ports = _ports(twak, book)
    decision = SimpleNamespace(exit_quantity=Decimal("2"))

    tx_hash = ports.execute(position, decision, quote=None)

    # The real sell tx hash is read from the top-level "hash" field, NOT lost
    # to the "SUBMITTED" fallback.
    assert tx_hash == "0x88b0deadbeef"


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


def test_sell_probe_returns_cost_viability_normalized_quote():
    # The sell quote must be normalized to the SAME live shape cost_viability's
    # LIVE branch consumes: output_qty (USDC out), minimum_output (min USDC),
    # price_impact. For a SELL, output/minReceived are USDC -> the spread is the
    # sell-leg slippage.
    twak = FakeTwak()
    ports = _ports(twak, [])

    quote = ports.sell_probe(_position(), Decimal("1"))

    assert quote.approved is True
    assert quote.quote is not None
    assert Decimal(str(quote.quote["output_qty"])) == Decimal("0.138910921981003559")
    assert Decimal(str(quote.quote["minimum_output"])) == Decimal("0.137521812761193523")
    assert Decimal(str(quote.quote["price_impact"])) == Decimal("0")


def test_sell_probe_fails_closed_on_nonpositive_quantity():
    twak = FakeTwak()
    ports = _ports(twak, [])

    quote = ports.sell_probe(_position(), Decimal("0"))

    assert quote.approved is False
    assert "nonpositive_exit_quantity" in quote.reasons
    assert twak.calls == []


# ----------------------------------------------------------------------------
# Idempotent protective sells via the ExecutionCoordinator (Part A).
# ----------------------------------------------------------------------------
from magic_agent.execution_coordinator import ExecutionCoordinator
from magic_agent.execution_journal import ExecutionJournal, ExecutionState
from magic_agent.live_exits import sell_intent_id_for


class SellTwak:
    """Execute returns a real top-level hash; counts the broadcast swaps."""

    def __init__(self, hash_value="0xsellhash"):
        self.calls = []
        self._hash = hash_value

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        if "--quote-only" in args:
            return _sell_quote_payload()
        return {
            "output": "1 USDC", "minReceived": "0.99 USDC",
            "priceImpact": "0", "hash": self._hash,
        }

    @property
    def execute_calls(self):
        return [a for a in self.calls if "--quote-only" not in a]


class SellRpc:
    def __init__(self, *, receipt, confirmations):
        self._receipt = receipt
        self._confirmations = confirmations

    def wallet_nonce(self):
        return 1

    def wait_receipt(self, tx_hash):
        return self._receipt

    def confirmations(self, receipt):
        return self._confirmations


class SellBalances:
    """Token down, stable up across the sell (a reconcilable sell)."""

    def __init__(self):
        self._snaps = [
            {"stable": Decimal("0"), "token": Decimal("2")},   # pre
            {"stable": Decimal("5"), "token": Decimal("0")},   # post
        ]
        self._i = 0

    def snapshot(self, identity_key):
        snap = self._snaps[min(self._i, len(self._snaps) - 1)]
        self._i += 1
        return dict(snap)


def _sell_coordinator(tmp_path, *, twak, rpc=None, balances=None):
    journal = ExecutionJournal(tmp_path / "exec.json")
    rpc = rpc or SellRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    coord = ExecutionCoordinator(
        twak=twak, rpc=rpc, balances=balances or SellBalances(),
        journal=journal, positions=SimpleNamespace(), registry=None,
        required_confirmations=2,
    )
    return coord, journal


def _coord_ports(twak, book, coord):
    return TwakSellPorts(twak=twak, book=book, registry=FakeRegistry({"zec-bsc": _APE}),
                         coordinator=coord, chain="bsc")


def test_stable_sell_intent_id_is_deterministic_across_restart():
    position = _position()
    decision = SimpleNamespace(exit_quantity=Decimal("2"), reason="stop")
    a = sell_intent_id_for(position, decision)
    b = sell_intent_id_for(position, decision)
    assert a == b
    assert a == "sell:intent-1:stop:2"


def test_protective_sell_broadcasts_exactly_once_on_retry(tmp_path):
    # A sell that broadcasts, then is RE-attempted with the SAME position/intent
    # (lost response / same-cycle retry): the journal blocks the second broadcast.
    twak = SellTwak()
    coord, journal = _sell_coordinator(tmp_path, twak=twak)
    position = _position()
    book = [position]
    ports = _coord_ports(twak, book, coord)
    decision = SimpleNamespace(exit_quantity=Decimal("2"), reason="stop")

    out1 = ports.execute(position, decision, quote=None)
    assert out1 == "RECONCILED"
    assert len(twak.execute_calls) == 1
    assert book == []  # full close mutated on RECONCILED

    # Re-attempt the same exit (position re-detected on retry): blocked, no 2nd swap.
    sell_id = sell_intent_id_for(position, decision)
    out2 = ports.execute(position, decision, quote=None)
    assert out2 == "ALREADY_SUBMITTED"
    assert len(twak.execute_calls) == 1  # EXACTLY ONCE — no double-sell
    assert journal.get(sell_id).state is ExecutionState.RECONCILED


def test_restart_mid_exit_does_not_rebroadcast(tmp_path):
    # A sell intent journaled SUBMITTED (receipt not yet confirmed), then a restart
    # runs process_exits again: the stable id sees the existing record -> no re-broadcast.
    twak = SellTwak()
    # confirmations < required -> stays MINED (in flight, not reconciled).
    rpc = SellRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=0)
    coord, journal = _sell_coordinator(tmp_path, twak=twak, rpc=rpc)
    position = _position()
    book = [position]
    ports = _coord_ports(twak, book, coord)
    decision = SimpleNamespace(exit_quantity=Decimal("2"), reason="stop")

    out1 = ports.execute(position, decision, quote=None)
    assert out1 == "MINED"
    assert len(twak.execute_calls) == 1
    assert book == [position]  # NOT reconciled -> book untouched (no fabricated mutation)

    # Restart: the same exit is re-driven; the existing record blocks a 2nd broadcast.
    out2 = ports.execute(position, decision, quote=None)
    assert out2 == "ALREADY_SUBMITTED"
    assert len(twak.execute_calls) == 1
    assert book == [position]


def test_broadcast_unknown_sell_no_double_sell_no_fabricated_mutation(tmp_path):
    # A lost-response sell (no tx hash) -> BROADCAST_UNKNOWN recorded, book untouched,
    # and a next-cycle retry does NOT re-broadcast.
    class NoHashTwak(SellTwak):
        def json(self, args, *, timeout=60):
            self.calls.append(args)
            if "--quote-only" in args:
                return _sell_quote_payload()
            return {"output": "1 USDC"}  # no hash -> BROADCAST_UNKNOWN

    twak = NoHashTwak()
    coord, journal = _sell_coordinator(tmp_path, twak=twak)
    position = _position()
    book = [position]
    ports = _coord_ports(twak, book, coord)
    decision = SimpleNamespace(exit_quantity=Decimal("2"), reason="stop")

    out1 = ports.execute(position, decision, quote=None)
    assert out1 == "BROADCAST_UNKNOWN"
    assert book == [position]  # no fabricated book mutation
    sell_id = sell_intent_id_for(position, decision)
    assert journal.get(sell_id).state is ExecutionState.BROADCAST_UNKNOWN

    out2 = ports.execute(position, decision, quote=None)
    assert out2 == "ALREADY_SUBMITTED"
    assert len(twak.execute_calls) == 1  # no double-sell
    assert book == [position]
