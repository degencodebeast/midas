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
        symbol: Market-data symbol of the open position. Carried so the paper-exit
            ``observe`` port can fetch the position's current price frame. ``None``
            when the reconcile caller does not carry setup geometry.
        identity_key: Identity key of the open position's instrument, used to resolve
            its price frame in the paper-exit ``observe`` port. ``None`` when absent.
        entry: Setup entry price. Carried so the dashboard status projection can
            surface the position's real entry level (not a hardcoded zero). ``None``
            when the reconcile caller does not carry setup geometry.
        stop: Structural stop price. A bar low at/below it triggers a protective stop
            exit (the highest-precedence exit). ``None`` when absent.
        campaign_dol: Campaign drawing-of-liquidity target. A bar high at/above it
            triggers a campaign-DOL exit. ``None`` when absent.
    """

    intent_id: str
    quantity: Decimal
    stressed_loss_per_unit: Decimal = Decimal("0")
    symbol: str | None = None
    identity_key: str | None = None
    entry: Decimal | None = None
    stop: Decimal | None = None
    campaign_dol: Decimal | None = None


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

    def process_exits(self, now) -> int:
        """Drive protective exits for every open position this cycle.

        Selects and executes any protective exit (full stop / campaign-DOL /
        opposing-HTF close, or a partial risk reduction) through the shared
        evaluator + pipeline, in stressed-loss precedence order. Entry halts are
        ignored entirely here: a protective exit fires regardless of entry gating.

        Returns:
            The number of protective actions executed this cycle (full closes and
            partial risk reductions). ``run_cycle`` reads this to enforce the
            no-churn invariant: a cycle that executed ANY protective action is an
            exit-only cycle and must NOT submit a new entry until the next cycle, so
            the freed concurrency slot can never be re-used same-cycle. ``0`` means
            no protective action ran (entries may proceed).
        """
        ordered = sorted(
            self.positions(), key=lambda p: p.quantity * p.stressed_loss_per_unit, reverse=True,
        )
        exits_executed = 0
        for position in ordered:
            # Per-position isolation: any error processing ONE position (e.g. a
            # registry miss raising KeyError while building the live sell command)
            # must never abort protective exits for the rest of the book. On error
            # we record a fail-closed alert and continue; the failed position is
            # left untouched in the source and retried next cycle.
            try:
                reduction = self.risk_policy.reduction_for(position, self.risk_state())
                observation = self.observe(position, now, reduction)
                if observation is None:
                    # No protective-exit observation this cycle (the observe port could
                    # not assemble one — e.g. a position with no exit geometry, or no
                    # current price frame for its symbol). The position is retained for a
                    # later cycle; it is never dropped or assumed-closed.
                    continue
                inputs = self.evaluator.evaluate(observation)
                decision = self.pipeline.decide(inputs)
                if decision.action != "risk_exit":
                    continue
                quote = self.sell_probe(position, decision.exit_quantity)
                if quote is None or not quote.approved:
                    self.alert("protective_exit_unquotable", position)
                    continue
                self.execute(position, decision, quote)
                exits_executed += 1
            except Exception:
                # Fail closed for this position only: record the error and move on.
                # Nothing is booked or mutated for the failed position — it stays in
                # the book so a later cycle can retry it. The rest of the book's
                # protective exits proceed unaffected.
                self.alert("protective_exit_error", position)
                continue
        return exits_executed

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
            symbol = setup.symbol
            identity_key = setup.identity_key
            entry = setup.entry
            stop = setup.structural_stop
            campaign_dol = setup.campaign_dol
        else:
            stressed_loss_per_unit = Decimal("0")
            symbol = identity_key = entry = stop = campaign_dol = None
        position = ReconciledPosition(
            intent.intent_id, position_qty, stressed_loss_per_unit,
            symbol=symbol, identity_key=identity_key,
            entry=entry, stop=stop, campaign_dol=campaign_dol,
        )
        self.book.append(position)
        return position
