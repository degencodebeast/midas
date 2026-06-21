"""Receipt and balance reconciliation for spot buys.

A position is booked only when the on-chain receipt is confirmed and the
realized balance deltas agree with the intended trade. Any missing, reverted,
under-confirmed, or mismatched outcome blocks new exposure and books nothing
(fail closed). The reconciled position quantity is taken from the realized
on-chain token delta, not from the intended or quoted size.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of reconciling a buy against its receipt and balance deltas.

    Attributes:
        state: Reconciliation state, one of ``REVERTED``, ``MINED``,
            ``CONFIRMED_NOT_RECONCILED``, ``RECONCILED``, or ``BROADCAST_UNKNOWN``.
        position_qty: Realized token quantity to book; zero unless reconciled.
        blocks_new_exposure: Whether this outcome must block opening new exposure.
        reason: Human-readable explanation of the outcome.
    """

    state: str
    position_qty: Decimal
    blocks_new_exposure: bool
    reason: str

    @classmethod
    def broadcast_unknown(cls, reason: str) -> "ReconcileResult":
        """Build a fail-closed result for an unknown broadcast outcome.

        Args:
            reason: Why the broadcast outcome is unknown.

        Returns:
            A ``BROADCAST_UNKNOWN`` result that blocks new exposure.
        """
        return cls("BROADCAST_UNKNOWN", Decimal("0"), True, reason)


def reconcile_buy(*, receipt: dict, confirmations: int, required_confirmations: int, stable_before, stable_after, token_before, token_after) -> ReconcileResult:
    """Reconcile a buy receipt against realized stable and token balance deltas.

    Args:
        receipt: On-chain receipt; its ``status`` must decode to ``1`` for success.
        confirmations: Confirmations observed for the receipt's block.
        required_confirmations: Confirmations required before booking.
        stable_before: Stable-coin balance before the trade.
        stable_after: Stable-coin balance after the trade.
        token_before: Token balance before the trade.
        token_after: Token balance after the trade.

    Returns:
        A ``ReconcileResult``. Only a ``RECONCILED`` result carries a non-zero
        position quantity, derived from the realized token delta.
    """
    if int(str(receipt.get("status", "0")), 0) != 1:
        return ReconcileResult("REVERTED", Decimal("0"), False, "receipt reverted")
    if confirmations < required_confirmations:
        return ReconcileResult("MINED", Decimal("0"), True, "awaiting confirmations")
    stable_delta = Decimal(str(stable_after)) - Decimal(str(stable_before))
    token_delta = Decimal(str(token_after)) - Decimal(str(token_before))
    if stable_delta >= 0 or token_delta <= 0:
        return ReconcileResult("CONFIRMED_NOT_RECONCILED", Decimal("0"), True, "balance delta mismatch")
    return ReconcileResult("RECONCILED", token_delta, False, "receipt and balances agree")
