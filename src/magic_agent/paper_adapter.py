"""Paper execution adapter — the execution port for paper mode (simulated fills).

``PaperExecutionAdapter`` is the paper-mode stand-in for
:class:`magic_agent.execution_coordinator.ExecutionCoordinator`. It exposes the same
``submit(intent, *, quote, policy)`` entry point ``runner.run_cycle`` calls, simulates
a fill, and books the position through the **same** reconcile path the live coordinator
uses — :meth:`PositionManager.open_from_reconciliation` — so paper and live never diverge
on the no-optimistic-booking invariant. The booked quantity is the simulated realized
fill (``quote["quantity"]``), standing in for the on-chain token delta; it is never the
raw intent size assumed-filled outside the reconcile path.

The adapter touches no funds: it has no wallet / RPC / TWAK / transfer port. It records a
confirmed (``RECONCILED``) execution record so the compliance ledger counts the simulated
swap exactly as it would a live reconciled buy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class PaperExecutionRecord:
    """A simulated-but-confirmed execution record for compliance accounting."""

    intent: object
    quote: dict
    state: str = "RECONCILED"
    evidence: dict = field(default_factory=dict)


class PaperExecutionAdapter:
    """Simulate fills and book them via the shared reconcile path (no real funds)."""

    def __init__(self, *, positions) -> None:
        """Initialize the adapter.

        Args:
            positions: The :class:`PositionManager` that books reconciled positions.
                The adapter books through its ``open_from_reconciliation`` so paper
                booking is identical to the live coordinator's reconcile path.
        """
        self.positions = positions
        self.records: list[PaperExecutionRecord] = []

    def submit(self, intent, *, quote: dict, policy) -> str:
        """Simulate a fill for ``intent`` and book it through reconciliation.

        Args:
            intent: The authorized :class:`SpotIntent` to fill.
            quote: The fresh exact-size quote (carries the simulated realized
                ``quantity``).
            policy: The RiskPolicy decision that sized the order (recorded as
                evidence; never re-evaluated here).

        Returns:
            ``"RECONCILED"`` — the simulated terminal state.
        """
        # Realized fill quantity. The production paper quote provider
        # (`quotes.PaperQuoteProvider`) emits this under `output_qty`; a bare
        # `quantity` key is also accepted for simpler quote stubs.
        if "output_qty" in quote:
            position_qty = Decimal(str(quote["output_qty"]))
        else:
            position_qty = Decimal(str(quote["quantity"]))
        self.positions.open_from_reconciliation(intent, position_qty)
        self.records.append(PaperExecutionRecord(
            intent=intent, quote=quote, evidence={"simulated": True},
        ))
        return "RECONCILED"

    def confirmed_records(self) -> list[PaperExecutionRecord]:
        """Return the confirmed (``RECONCILED``) simulated swaps for compliance."""
        return [record for record in self.records if record.state == "RECONCILED"]
