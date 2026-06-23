from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal

from magic_agent.live_quotes import _normalize_live_legs, _parse_amount

_log = logging.getLogger(__name__)


def sell_intent_id_for(position, decision) -> str:
    """Derive a STABLE protective-sell intent id for a position's exit.

    Deterministic across restarts: keyed on the position's identity (its
    ``intent_id``) + the exit reason + the exit quantity, with NO timestamp or
    RNG. A restart mid-exit reconstructs the SAME id from the same
    position/decision, so the execution journal blocks a second broadcast of the
    same protective sell (idempotency). The quantity is in the key so a distinct
    later partial reduction (different size) is a genuinely different exit, while
    a same-cycle / post-restart retry of the SAME exit collides and is blocked.
    """
    reason = getattr(decision, "reason", None) or "exit"
    return f"sell:{position.intent_id}:{reason}:{decision.exit_quantity}"


@dataclass(frozen=True)
class LiveSellQuote:
    approved: bool
    quote: dict | None
    reasons: tuple[str, ...] = ()


class TwakSellPorts:
    """Real TWAK protective-exit sells, reconciled to twak 0.19.1.

    A sell is the ``<from> <to>`` order ``<contract_address> USDC`` (token ->
    USDC); there is no separate direction flag. The token arg is the resolved
    registry CONTRACT ADDRESS, never identity_key.
    """

    def __init__(self, *, twak, book, registry, coordinator=None,
                 stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
        self._book = book
        self._registry = registry
        self._coordinator = coordinator
        self._stable_symbol = stable_symbol
        self._chain = chain

    def _contract(self, position) -> str:
        return self._registry.by_contract_key(position.identity_key).contract_address

    def sell_probe(self, position, quantity: Decimal) -> LiveSellQuote:
        if quantity <= 0:
            return LiveSellQuote(False, None, ("nonpositive_exit_quantity",))
        contract = self._contract(position)
        payload = self._twak.json([
            "swap", str(quantity), contract, self._stable_symbol,
            "--chain", self._chain, "--quote-only", "--json",
        ])
        if not isinstance(payload, dict) or payload.get("error") is not None:
            return LiveSellQuote(False, None, ("twak_error",))
        for field in ("output", "minReceived"):
            if field not in payload:
                return LiveSellQuote(False, None, (f"missing_{field}",))
        quote = dict(payload)
        # Normalize to the SAME live shape cost_viability's LIVE branch consumes
        # (output_qty / minimum_output / price_impact). For a SELL, output and
        # minReceived are USDC, so the (output_qty - minimum_output) spread is the
        # sell-leg slippage. Fail closed on any malformed field.
        try:
            output_qty, _symbol, minimum_output, price_impact = _normalize_live_legs(payload)
        except ValueError:
            return LiveSellQuote(False, None, ("malformed_amount",))
        if output_qty <= 0:
            return LiveSellQuote(False, None, ("nonpositive_output",))
        if minimum_output <= 0:
            return LiveSellQuote(False, None, ("nonpositive_minimum_output",))
        if price_impact != 0:
            # cost_viability fails closed on nonzero price_impact (unit unconfirmed);
            # mirror that here so a nonzero-impact sell quote never silently passes.
            return LiveSellQuote(False, None, ("price_impact_unit_unconfirmed",))
        quote["output_qty"] = str(output_qty)
        quote["minimum_output"] = str(minimum_output)
        quote["price_impact"] = str(price_impact)
        return LiveSellQuote(True, quote, ())

    def execute(self, position, decision, quote) -> str:
        contract = self._contract(position)
        if self._coordinator is not None:
            return self._execute_via_coordinator(position, decision, contract)
        # LEGACY direct-broadcast path (no idempotency journal). Retained for the
        # paper/standalone callers that construct TwakSellPorts without a
        # coordinator; the LIVE wiring always supplies a coordinator so protective
        # sells are journal-guarded (see _execute_via_coordinator).
        #
        # SELL FIRST: broadcast the real TWAK sell (token -> USDC order). If
        # this raises, we propagate WITHOUT mutating the book — a position we may
        # still hold on-chain must never be dropped. Only AFTER a confirmed sell do
        # we mutate the book.
        payload = self._twak.json([
            "swap", str(decision.exit_quantity), contract, self._stable_symbol,
            "--chain", self._chain, "--json",
        ])
        # A REAL executed sell returns the tx hash in the TOP-LEVEL "hash" field
        # (mirroring the coordinator's buy fix); the data.tx_hash / tx_hash shapes
        # are legacy fallbacks before the "SUBMITTED" placeholder.
        tx_hash = (
            payload.get("hash")
            or payload.get("data", {}).get("tx_hash")
            or payload.get("tx_hash")
            or "SUBMITTED"
        )
        self._shrink_book(position, decision)
        return tx_hash

    def _execute_via_coordinator(self, position, decision, contract: str) -> str:
        """Idempotent protective sell through the ExecutionCoordinator.

        Derives a STABLE sell intent_id and routes the broadcast through
        ``coordinator.submit_sell``, reusing the buy idempotency machinery: the
        journal's ``create`` RAISES on a duplicate, so a re-attempt with the same
        position/exit (lost response, same-cycle retry, or a restart mid-exit)
        does NOT re-broadcast — the swap is broadcast EXACTLY once.

        Book mutation is tied to a RECONCILED sell (sell-first-then-mutate
        ordering preserved): only a confirmed, balance-reconciled sell shrinks /
        closes the position. A non-reconciled outcome (BROADCAST_UNKNOWN, MINED,
        REVERTED, CONFIRMED_NOT_RECONCILED) leaves the book UNTOUCHED — no
        fabricated mutation, the position we may still hold is never dropped, and
        protective-exit availability is preserved for a later recovery cycle.
        """
        sell_intent_id = sell_intent_id_for(position, decision)
        try:
            outcome = self._coordinator.submit_sell(
                sell_intent_id=sell_intent_id,
                identity_key=position.identity_key,
                contract=contract,
                exit_quantity=decision.exit_quantity,
            )
        except ValueError:
            # Idempotency guard: a record for this stable sell intent already
            # exists (in flight or done). Do NOT re-broadcast and do NOT fabricate
            # a book mutation — the original submit owns the book transition.
            _log.info(
                "protective sell %s already journaled; not re-broadcasting",
                sell_intent_id,
            )
            return "ALREADY_SUBMITTED"
        if outcome == "RECONCILED":
            self._shrink_book(position, decision)
        return outcome

    def _shrink_book(self, position, decision) -> None:
        """Shrink/close the position in the book, mirroring PaperExitPorts.execute.

        A full-quantity exit removes the closed position; a partial reduction
        shrinks it in place via ``dataclasses.replace`` (preserving all other
        fields). This stops the price-driven observe from re-detecting the same
        stop/DOL hit and driving a SECOND sell.
        """
        try:
            index = self._book.index(position)
        except ValueError:
            return  # Already absent — no-op, not an error.
        if decision.exit_quantity >= position.quantity:
            del self._book[index]
            return
        remaining = position.quantity - decision.exit_quantity
        self._book[index] = replace(position, quantity=remaining)
