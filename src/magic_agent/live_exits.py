from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal


@dataclass(frozen=True)
class LiveSellQuote:
    approved: bool
    quote: dict | None
    reasons: tuple[str, ...] = ()


class TwakSellPorts:
    def __init__(self, *, twak, book, stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
        self._book = book
        self._stable_symbol = stable_symbol
        self._chain = chain

    def sell_probe(self, position, quantity: Decimal) -> LiveSellQuote:
        if quantity <= 0:
            return LiveSellQuote(False, None, ("nonpositive_exit_quantity",))
        token = position.identity_key
        payload = self._twak.json([
            "swap", str(quantity), token, self._stable_symbol,
            "--chain", self._chain, "--quote-only", "--sell", "--json",
        ])
        return LiveSellQuote(True, payload.get("data", payload), ())

    def execute(self, position, decision, quote) -> str:
        token = position.identity_key
        # SELL FIRST: broadcast the real TWAK sell. If this raises, we return early
        # (propagate) WITHOUT mutating the book — a position we may still hold on-chain
        # must never be dropped. Only AFTER a confirmed sell do we mutate the book.
        payload = self._twak.json([
            "swap", str(decision.exit_quantity), token, self._stable_symbol,
            "--chain", self._chain, "--sell", "--json",
        ])
        tx_hash = (
            payload.get("data", {}).get("tx_hash") or payload.get("tx_hash") or "SUBMITTED"
        )
        # THEN mutate the book, mirroring PaperExitPorts.execute exactly: a full-quantity
        # exit removes the closed position; a partial reduction shrinks it in place via
        # dataclasses.replace (preserving entry/stop/campaign_dol/symbol/identity_key/
        # stressed_loss_per_unit/intent_id). This is what stops the price-driven observe
        # from re-detecting the same stop/DOL hit and broadcasting a SECOND real sell.
        try:
            index = self._book.index(position)
        except ValueError:
            # Already absent — no-op, not an error.
            return tx_hash
        if decision.exit_quantity >= position.quantity:
            del self._book[index]
            return tx_hash
        remaining = position.quantity - decision.exit_quantity
        self._book[index] = replace(position, quantity=remaining)
        return tx_hash
