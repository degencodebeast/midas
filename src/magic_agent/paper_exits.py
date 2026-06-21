"""Paper-mode protective-exit ports for :class:`PositionManager.process_exits`.

In paper mode a booked position must be a real ROUND-TRIP: an open position whose
structural stop or campaign-DOL is hit must CLOSE, free the concurrency slot, and
drop from the durable book. :class:`PaperExitPorts` provides the three callbacks the
manager drives — ``observe``, ``sell_probe``, ``execute`` — entirely off price (no
funds, no signing):

* ``observe`` resolves the open position's current price bar from the injected
  :class:`~magic_agent.frames.FrameSource` and assembles the
  :class:`~magic_agent.lifecycle.LifecycleObservation` the shared evaluator needs to
  detect a stop hit (``low <= stop``) or a campaign-DOL hit (``high >= campaign_dol``).
* ``sell_probe`` returns a deterministic, approved paper sell quote (the exit mirror
  of :class:`~magic_agent.quotes.PaperQuoteProvider`'s sell leg).
* ``execute`` simulates the exit fill by REMOVING the closed position from the
  manager's book, so ``positions()`` and the concurrency count drop and
  ``run_cycle``'s end-of-cycle ``position_store.save`` persists the close.

PAPER-MODE MECHANISM. This price-driven exit is the *paper* mechanism only: live
exits are driven by real fills / chain truth (the open book is the chain's truth,
rebuilt via reconcile/balances), a deliberate follow-on — NOT wired here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from magic_agent.lifecycle import LifecycleObservation, PositionView

# The closed-bar timeframe the paper exit reads the position's current price from.
# H1 is the orderflow frame the scanner doctrine drives on; the latest closed H1 bar
# is the position's "now" low/high for stop / campaign-DOL detection.
_EXIT_TIMEFRAME = "1h"

# Paper sell-leg friction (the exit mirror of quotes._SLIPPAGE_BPS); a quote is
# always approved in paper (no live route can reject it) — the close is price-driven.
_SLIPPAGE_BPS = Decimal("10")
_MINIMUM_OUTPUT_FRACTION = (Decimal("10000") - _SLIPPAGE_BPS) / Decimal("10000")


@dataclass(frozen=True)
class _PaperSellQuote:
    """The approved/rejected surface ``process_exits`` reads off a sell probe."""

    approved: bool
    quantity: Decimal
    minimum_output: Decimal


class PaperExitPorts:
    """Price-driven paper protective-exit ports (observe / sell_probe / execute).

    Attributes:
        frame_source: Read-only market-data source yielding the position's closed
            price frames (injected; offline fixtures or a controllable test source).
        book: The :class:`PositionManager` book a closed position is removed from.
    """

    def __init__(self, *, frame_source: Any, book: list) -> None:
        """Bind the exit ports to their frame source and the manager's open book.

        Args:
            frame_source: The market-data :class:`~magic_agent.frames.FrameSource`.
            book: The mutable open-position book the manager exposes; a closed
                position is removed from it in :meth:`execute`.
        """
        self._frame_source = frame_source
        self._book = book

    def observe(self, position, observed_at, reduction) -> LifecycleObservation | None:
        """Build the protective-exit observation for an open paper position.

        Resolves the position's latest closed price bar from the frame source and
        assembles a :class:`LifecycleObservation` carrying the bar low/high so the
        shared evaluator can detect a stop or campaign-DOL hit. Returns ``None`` only
        when the position lacks the exit geometry needed to evaluate an exit (e.g. a
        position booked without setup context) — the manager then retains it for a
        later cycle, never dropping it.

        Args:
            position: The open :class:`~magic_agent.position_manager.ReconciledPosition`.
            observed_at: The cycle timestamp (unused by the price-driven paper path;
                kept for the ``observe`` port signature).
            reduction: The RiskPolicy reduction decision for this position (its
                ``reduction_qty`` flows into the observation for risk-reduction sizing).

        Returns:
            The assembled :class:`LifecycleObservation`, or ``None`` when the position
            carries no exit geometry.
        """
        if position.stop is None or position.campaign_dol is None:
            return None
        low, high = self._bar_low_high(position)
        if low is None or high is None:
            return None
        view = PositionView(
            position.intent_id, position.quantity, position.stop, position.campaign_dol,
        )
        return LifecycleObservation(
            setup=None,
            risk=None,
            position=view,
            low=low,
            high=high,
            opposing_htf_invalidated=False,
            risk_reduction_qty=reduction.reduction_qty,
            entries_halted=False,
        )

    def sell_probe(self, position, quantity: Decimal) -> _PaperSellQuote:
        """Return a deterministic, approved paper sell quote for the exit quantity.

        Args:
            position: The open position being exited (unused for pricing — the paper
                exit is price-driven, not route-priced).
            quantity: The protective-exit quantity selected by the pipeline.

        Returns:
            An approved :class:`_PaperSellQuote` carrying a worst-case minimum output.
        """
        if quantity <= 0:
            return _PaperSellQuote(False, Decimal("0"), Decimal("0"))
        return _PaperSellQuote(True, quantity, quantity * _MINIMUM_OUTPUT_FRACTION)

    def execute(self, position, decision, quote) -> None:
        """Simulate the exit fill: REMOVE the closed position from the book.

        A full-quantity protective exit (stop / campaign-DOL / opposing-HTF) closes
        the position, so it is dropped from the book — ``positions()`` and the
        concurrency count fall and the end-of-cycle ``position_store.save`` persists
        the close. A partial risk reduction (``exit_quantity < quantity``) shrinks
        the position in place rather than closing it.

        Args:
            position: The position being exited.
            decision: The pipeline decision (carries ``exit_quantity``).
            quote: The approved paper sell quote (no funds move).
        """
        try:
            index = self._book.index(position)
        except ValueError:
            # Already absent (defensive — the manager iterates a snapshot copy); the
            # close is a no-op, never an error.
            return
        if decision.exit_quantity >= position.quantity:
            del self._book[index]
            return
        # Partial risk reduction: shrink the position in place, keep it open. Use
        # ``dataclasses.replace`` so EVERY other field carries through unchanged
        # (entry, stop, campaign_dol, symbol, identity_key, stressed_loss_per_unit,
        # intent_id) — reconstructing the position field-by-field dropped ``entry``,
        # regressing the dashboard projection to a fake "Entry 0.00" after a reduce.
        remaining = position.quantity - decision.exit_quantity
        self._book[index] = replace(position, quantity=remaining)

    def _bar_low_high(self, position) -> tuple[Decimal | None, Decimal | None]:
        """Resolve the position's latest closed bar low/high from the frame source.

        Builds the minimal candidate the frame source resolves (``identity_key``),
        fetches its closed frames, and reads the last :data:`_EXIT_TIMEFRAME` bar.

        Returns:
            The ``(low, high)`` of the latest closed exit-timeframe bar as
            :class:`Decimal`, or ``(None, None)`` when no frame/bar is available.
        """
        candidate = SimpleNamespace(
            identity_key=position.identity_key, symbol=position.symbol,
        )
        frames = self._frame_source.closed_frames(candidate)
        frame = frames.get(_EXIT_TIMEFRAME)
        if frame is None or len(frame) == 0:
            return None, None
        last = frame.iloc[-1]
        # Money stays exact: round-trip the float bar values through str -> Decimal.
        return Decimal(str(last["low"])), Decimal(str(last["high"]))
