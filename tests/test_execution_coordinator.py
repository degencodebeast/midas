"""Coordinator tests: a position is booked only after on-chain reconciliation.

Every non-reconciled outcome (revert, insufficient confirmations,
confirmed-but-not-reconciled, broadcast-unknown) must book nothing and must not
double-submit on idempotent replay. The reconciled quantity booked is the
realized on-chain token delta, never the quoted or intended size.
"""

from decimal import Decimal

import pytest

from magic_agent.execution_coordinator import ExecutionCoordinator
from magic_agent.execution_journal import ExecutionJournal, ExecutionState
from magic_agent.identity_registry import IdentityRecord, IdentityRegistry
from magic_agent.policy import PolicyConfig
from magic_agent.position_manager import PositionManager, ReconciledPosition
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent
from magic_agent.twak import TwakError


def _intent(intent_id: str = "intent-1") -> SpotIntent:
    setup = AuthorizedSetup.example()
    return SpotIntent(
        intent_id=intent_id,
        setup=setup,
        quantity=Decimal("10"),
        side="buy",
        purpose=ActionPurpose.STRATEGY,
    )


def _registry() -> IdentityRegistry:
    record = IdentityRecord(
        competition_symbol="ZEC",
        cmc_id=1,
        chain_id=56,
        contract_address="0xCONTRACT",
        decimals=18,
        onchain_symbol="ZEC",
        market_data_source="cmc",
        market_data_symbol="ZEC",
        coverage_status="scannable",
        verification_status="gold",
        verified_at="2026-06-21T00:00:00Z",
        sources=("cmc",),
    )
    return IdentityRegistry([record])


class FakeTwak:
    """Fake TWAK runner returning a canned swap payload or raising."""

    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else {"data": {"tx_hash": "0xdead"}}
        self.error = error
        self.calls: list[list[str]] = []

    def json(self, args: list[str]) -> dict:
        self.calls.append(list(args))
        if self.error is not None:
            raise self.error
        return self.payload


class FakeRpc:
    """Fake RPC providing nonce, receipt, and confirmation count deterministically."""

    def __init__(self, *, receipt: dict, confirmations: int, nonce: int = 7) -> None:
        self._receipt = receipt
        self._confirmations = confirmations
        self._nonce = nonce

    def wallet_nonce(self) -> int:
        return self._nonce

    def wait_receipt(self, tx_hash: str) -> dict:
        return self._receipt

    def confirmations(self, receipt: dict) -> int:
        return self._confirmations


class FakeBalances:
    """Fake balance provider returning the same pre/post snapshot each call."""

    def __init__(self, snapshots: list[dict]) -> None:
        self._snapshots = snapshots
        self._index = 0

    def snapshot(self, identity_key: str) -> dict:
        snap = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return dict(snap)


class SpyPositions:
    """Position manager spy recording every open_from_reconciliation call."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, Decimal]] = []

    def open_from_reconciliation(self, intent, position_qty: Decimal) -> ReconciledPosition:
        self.calls.append((intent, position_qty))
        return ReconciledPosition(intent.intent_id, position_qty)


def _coordinator(tmp_path, *, twak, rpc, balances, positions, required_confirmations: int = 2):
    journal = ExecutionJournal(tmp_path / "exec.json")
    return ExecutionCoordinator(
        twak=twak,
        rpc=rpc,
        balances=balances,
        journal=journal,
        positions=positions,
        registry=_registry(),
        required_confirmations=required_confirmations,
    ), journal


def test_buy_swap_spends_usdc_source_amount_not_token_qty(tmp_path):
    # The buy swap SOURCE amount is USDC = intent.quantity * setup.entry, NOT the
    # token qty. intent.quantity == 10, setup.entry == 100 -> 1000 USDC.
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("2000"), "token": Decimal("0")},
        {"stable": Decimal("1000"), "token": Decimal("95")},
    ])
    positions = SpyPositions()
    coord, _journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)
    intent = _intent()
    assert intent.setup.entry == Decimal("100")

    result = coord.submit(intent, quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "RECONCILED"
    # USDC source amount = 10 * 100 = 1000, NOT "10".
    assert twak.calls == [
        ["swap", "1000", "USDC", "0xCONTRACT", "--chain", "bsc", "--json"],
    ]
    # Booked qty still comes ONLY from the on-chain balance delta (95), not the quote.
    assert positions.calls[0][1] == Decimal("95")


def test_buy_swap_asserts_usdc_in_matches_quote(tmp_path):
    # When the prepared quote carries usdc_in, it must equal quantity * entry.
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("2000"), "token": Decimal("0")},
        {"stable": Decimal("1000"), "token": Decimal("95")},
    ])
    positions = SpyPositions()
    coord, _journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(
        _intent(), quote={"price": "1", "usdc_in": "1000"},
        policy=PolicyConfig(max_notional=1000.0),
    )

    assert result == "RECONCILED"
    assert twak.calls[0][1] == "1000"


def test_buy_swap_mismatched_usdc_in_is_broadcast_unknown_and_books_nothing(tmp_path):
    # When the prepared quote carries a usdc_in that does NOT equal quantity * entry,
    # the money-safety invariant must fail closed BEFORE any swap is broadcast: no twak
    # call, no booking, terminal BROADCAST_UNKNOWN. (This must hold even under python -O,
    # so it is an explicit raise, not an assert.)
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("2000"), "token": Decimal("0")},
        {"stable": Decimal("1000"), "token": Decimal("95")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(
        _intent(), quote={"price": "1", "usdc_in": "999"},
        policy=PolicyConfig(max_notional=1000.0),
    )

    assert result == "BROADCAST_UNKNOWN"
    assert twak.calls == []  # no swap broadcast
    assert positions.calls == []  # nothing booked
    assert journal.get("intent-1").state is ExecutionState.BROADCAST_UNKNOWN


def test_confirmed_and_reconciled_books_once_with_reconciled_qty(tmp_path):
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    # token delta = 95 - 0 = 95 reconciled; quoted size is irrelevant.
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
        {"stable": Decimal("5"), "token": Decimal("95")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "RECONCILED"
    assert len(positions.calls) == 1
    booked_intent, booked_qty = positions.calls[0]
    assert booked_intent.intent_id == "intent-1"
    assert booked_qty == Decimal("95")
    assert journal.get("intent-1").state is ExecutionState.RECONCILED


def test_reverted_receipt_books_nothing(tmp_path):
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x0", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
        {"stable": Decimal("100"), "token": Decimal("0")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "REVERTED"
    assert positions.calls == []
    assert journal.get("intent-1").state is ExecutionState.MINED


def test_insufficient_confirmations_books_nothing(tmp_path):
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=1)
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
        {"stable": Decimal("5"), "token": Decimal("95")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "MINED"
    assert positions.calls == []
    assert journal.get("intent-1").state is ExecutionState.MINED


def test_confirmed_not_reconciled_books_nothing(tmp_path):
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    # No token delta despite confirmed receipt => fail closed, no booking.
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
        {"stable": Decimal("100"), "token": Decimal("0")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "CONFIRMED_NOT_RECONCILED"
    assert positions.calls == []
    assert journal.get("intent-1").state is ExecutionState.MINED


def test_broadcast_unknown_on_twak_error_books_nothing_and_blocks(tmp_path):
    twak = FakeTwak(error=TwakError("twak timed out after possible broadcast"))
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "BROADCAST_UNKNOWN"
    assert positions.calls == []
    assert journal.get("intent-1").state is ExecutionState.BROADCAST_UNKNOWN


def test_real_execute_response_hash_field_is_extracted_and_reconciles(tmp_path):
    # A REAL executed twak swap (WITHOUT --quote-only) returns the tx hash in the
    # TOP-LEVEL "hash" field -- NOT tx_hash, NOT data.tx_hash, and with NO
    # success/data wrapper. The coordinator must read "hash" and proceed to the
    # receipt/reconcile path, never falsely flagging BROADCAST_UNKNOWN.
    real_hash = "0x88b0049f764321337ea7a85f94a440706ceea1ee1eee14058c6a6c520cbbaf5b"
    twak = FakeTwak(payload={
        "input": "0.0167 BNB",
        "output": "10.039254902866404567 USDC",
        "minReceived": "9.938862353837740521 USDC",
        "provider": "0x",
        "priceImpact": "0",
        "hash": real_hash,
        "fromChain": "bsc",
        "toChain": "bsc",
        "explorer": f"https://bscscan.com/tx/{real_hash}",
    })
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
        {"stable": Decimal("5"), "token": Decimal("95")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    # The real "hash" is extracted -> proceeds to receipt/reconcile, NOT BROADCAST_UNKNOWN.
    assert result == "RECONCILED"
    record = journal.get("intent-1")
    assert record.evidence.get("tx_hash") == real_hash
    assert record.state is ExecutionState.RECONCILED
    assert positions.calls[0][1] == Decimal("95")


def test_missing_tx_hash_is_broadcast_unknown(tmp_path):
    # A response with NEITHER "hash" NOR "tx_hash" must still fail closed.
    twak = FakeTwak(payload={"input": "0.0167 BNB", "output": "10 USDC"})
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "BROADCAST_UNKNOWN"
    assert positions.calls == []
    assert journal.get("intent-1").state is ExecutionState.BROADCAST_UNKNOWN


class RaisingReceiptRpc:
    """Fake RPC whose wait_receipt RAISES (e.g. real receipt-poll timeout)."""

    def __init__(self, *, nonce: int = 7) -> None:
        self._nonce = nonce

    def wallet_nonce(self) -> int:
        return self._nonce

    def wait_receipt(self, tx_hash: str) -> dict:
        from magic_agent.live_rpc import RpcError

        raise RpcError("receipt timeout")

    def confirmations(self, receipt: dict) -> int:  # pragma: no cover - never reached
        return 2


class RaisingPostSnapshotBalances:
    """Fake balances: pre snapshot succeeds, the post-swap snapshot RAISES."""

    def __init__(self, pre: dict) -> None:
        self._pre = pre
        self._index = 0

    def snapshot(self, identity_key: str) -> dict:
        self._index += 1
        if self._index == 1:
            return dict(self._pre)
        raise RuntimeError("balance read failed after swap")


def test_wait_receipt_failure_maps_to_broadcast_unknown_and_books_nothing(tmp_path):
    twak = FakeTwak()
    rpc = RaisingReceiptRpc()
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "BROADCAST_UNKNOWN"
    assert positions.calls == []
    # SUBMITTED -> BROADCAST_UNKNOWN is a valid transition; the record is non-terminal
    # so recovery.reconcile_unfinished blocks new exposure next cycle (fail closed).
    assert journal.get("intent-1").state is ExecutionState.BROADCAST_UNKNOWN


def test_post_swap_balance_read_failure_returns_mined_and_books_nothing(tmp_path):
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = RaisingPostSnapshotBalances({"stable": Decimal("100"), "token": Decimal("0")})
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)

    # The post-MINED read raising must NOT crash the cycle; it returns MINED and
    # leaves the record at the non-terminal MINED state (recovery blocks next cycle).
    result = coord.submit(_intent(), quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))

    assert result == "MINED"
    assert positions.calls == []
    assert journal.get("intent-1").state is ExecutionState.MINED


def test_idempotent_rerun_does_not_double_submit_or_double_book(tmp_path):
    twak = FakeTwak()
    rpc = FakeRpc(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2)
    balances = FakeBalances([
        {"stable": Decimal("100"), "token": Decimal("0")},
        {"stable": Decimal("5"), "token": Decimal("95")},
    ])
    positions = SpyPositions()
    coord, journal = _coordinator(tmp_path, twak=twak, rpc=rpc, balances=balances, positions=positions)
    intent = _intent()

    first = coord.submit(intent, quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))
    assert first == "RECONCILED"
    assert len(positions.calls) == 1
    assert len(twak.calls) == 1

    # Re-running the same intent must not create a duplicate journal record,
    # must not re-submit a swap, and must not book a second position.
    with pytest.raises(ValueError):
        coord.submit(intent, quote={"price": "1"}, policy=PolicyConfig(max_notional=1000.0))
    assert len(positions.calls) == 1
    assert len(twak.calls) == 1
