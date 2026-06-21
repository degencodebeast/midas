"""Halt-safe position management for spot longs.

``PositionManager`` drives protective exits (stop, campaign DOL, opposing-HTF
invalidation, partial risk reduction) through the *same* shared evaluator and
pipeline as entries, so the protective-exit precedence is identical across all
runtime modes. Two safety invariants are load-bearing:

* **Entry halts never block protective exits.** ``process_exits`` ignores entry
  gating entirely; a stop/DOL/risk-reduction exit is selected and executed even
  when entries are halted (drawdown halt, consecutive-stop halt, concurrency cap).
* **Failed exits retain the position.** If the sell probe cannot quote a
  protective exit, the manager raises an alert and leaves the position untouched
  in the source so a later cycle can retry it. No position is ever dropped or
  assumed-closed on a failed exit.

Positions are opened only from reconciliation: ``open_from_reconciliation`` books
the realized on-chain token quantity, never an optimistic or quoted size.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReconciledPosition:
    """A spot-long position booked from a reconciled on-chain buy.

    Attributes:
        intent_id: The intent that produced the reconciled buy.
        quantity: Realized token quantity from the reconciled balance delta.
        stressed_loss_per_unit: Per-unit stressed loss used to order protective
            exits and size risk reductions. Defaults to ``0`` when the reconcile
            caller does not carry setup geometry (it is not load-bearing for the
            concurrency count, which keys only on the open-position *count*).
    """

    intent_id: str
    quantity: Decimal
    stressed_loss_per_unit: Decimal = Decimal("0")


class PositionManager:
    """Drive protective exits and book reconciled positions for spot longs."""

    def __init__(self, *, positions, observe, evaluator, pipeline, risk_policy, risk_state,
                 sell_probe, execute,
                 alert=lambda code, position: None) -> None:
        self.positions = positions
        self.observe = observe
        self.evaluator = evaluator
        self.pipeline = pipeline
        self.risk_policy = risk_policy
        self.risk_state = risk_state
        self.sell_probe = sell_probe
        self.execute = execute
        self.alert = alert
        self.book: list[ReconciledPosition] = []

    def process_exits(self, now) -> None:
        ordered = sorted(
            self.positions(), key=lambda p: p.quantity * p.stressed_loss_per_unit, reverse=True,
        )
        for position in ordered:
            reduction = self.risk_policy.reduction_for(position, self.risk_state())
            observation = self.observe(position, now, reduction)
            if observation is None:
                # No protective-exit observation this cycle (the paper observe port
                # is an inert no-op — paper has no live price frame to evaluate an
                # exit against). The position is retained for a later cycle; it is
                # never dropped or assumed-closed.
                continue
            inputs = self.evaluator.evaluate(observation)
            decision = self.pipeline.decide(inputs)
            if decision.action != "risk_exit":
                continue
            quote = self.sell_probe(position, decision.exit_quantity)
            if not quote.approved:
                self.alert("protective_exit_unquotable", position)
                continue
            self.execute(position, decision, quote)

    def open_from_reconciliation(self, intent, position_qty: Decimal) -> ReconciledPosition:
        """Book a position from a reconciled on-chain buy.

        Positions open only here: ``position_qty`` is the realized token delta
        produced by reconciliation, never an optimistic or quoted size. The
        execution coordinator calls this after a buy transitions to RECONCILED.

        Args:
            intent: The reconciled buy intent (carries ``intent_id``).
            position_qty: The realized on-chain token quantity to book.

        Returns:
            The booked :class:`ReconciledPosition`.
        """
        setup = getattr(intent, "setup", None)
        if setup is not None:
            stressed_loss_per_unit = setup.entry - setup.structural_stop
        else:
            stressed_loss_per_unit = Decimal("0")
        position = ReconciledPosition(intent.intent_id, position_qty, stressed_loss_per_unit)
        self.book.append(position)
        return position
