from decimal import Decimal

from magic_agent.reconcile import ReconcileResult, reconcile_buy


def test_receipt_success_without_balance_delta_is_not_position():
    result = reconcile_buy(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2, required_confirmations=2, stable_before=100, stable_after=100, token_before=0, token_after=0)
    assert result.state == "CONFIRMED_NOT_RECONCILED"
    assert result.position_qty == 0


def test_timeout_after_possible_broadcast_is_unknown():
    result = ReconcileResult.broadcast_unknown("wallet nonce changed")
    assert result.state == "BROADCAST_UNKNOWN"
    assert result.blocks_new_exposure is True


def test_reverted_receipt_yields_no_position():
    result = reconcile_buy(
        receipt={"status": "0x0", "blockNumber": "0x10"},
        confirmations=2,
        required_confirmations=2,
        stable_before=100,
        stable_after=90,
        token_before=0,
        token_after=5,
    )
    assert result.state == "REVERTED"
    assert result.position_qty == 0


def test_insufficient_confirmations_yields_no_position():
    result = reconcile_buy(
        receipt={"status": "0x1", "blockNumber": "0x10"},
        confirmations=1,
        required_confirmations=2,
        stable_before=100,
        stable_after=90,
        token_before=0,
        token_after=5,
    )
    assert result.state == "MINED"
    assert result.position_qty == 0
    assert result.blocks_new_exposure is True


def test_balance_delta_mismatch_yields_no_position():
    result = reconcile_buy(
        receipt={"status": "0x1", "blockNumber": "0x10"},
        confirmations=2,
        required_confirmations=2,
        stable_before=100,
        stable_after=110,
        token_before=0,
        token_after=5,
    )
    assert result.state == "CONFIRMED_NOT_RECONCILED"
    assert result.position_qty == 0


def test_reconciled_position_qty_comes_from_token_balance_delta():
    result = reconcile_buy(
        receipt={"status": "0x1", "blockNumber": "0x10"},
        confirmations=2,
        required_confirmations=2,
        stable_before=100,
        stable_after=90,
        token_before=0,
        token_after=7,
    )
    assert result.state == "RECONCILED"
    assert result.position_qty == Decimal("7")
    assert result.blocks_new_exposure is False
