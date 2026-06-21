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


def test_missing_tx_hash_is_broadcast_unknown(tmp_path):
    twak = FakeTwak(payload={"data": {}})
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
