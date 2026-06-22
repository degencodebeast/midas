from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from magic_agent.live_quotes import _parse_amount


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

    def __init__(self, *, twak, book, registry, stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
        self._book = book
        self._registry = registry
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
        quote = dict(payload)
        # Best-effort parse of the realized USDC output, when present.
        output = payload.get("output")
        if output is not None:
            try:
                usdc_out, _ = _parse_amount(output)
                quote["output_qty"] = str(usdc_out)
            except ValueError:
                return LiveSellQuote(False, None, ("malformed_amount",))
        return LiveSellQuote(True, quote, ())

    def execute(self, position, decision, quote) -> str:
        contract = self._contract(position)
        # SELL FIRST: broadcast the real TWAK sell (token -> USDC order). If
        # this raises, we propagate WITHOUT mutating the book — a position we may
        # still hold on-chain must never be dropped. Only AFTER a confirmed sell do
        # we mutate the book.
        payload = self._twak.json([
            "swap", str(decision.exit_quantity), contract, self._stable_symbol,
            "--chain", self._chain, "--json",
        ])
        tx_hash = (
            payload.get("data", {}).get("tx_hash") or payload.get("tx_hash") or "SUBMITTED"
        )
        # THEN mutate the book, mirroring PaperExitPorts.execute exactly: a
        # full-quantity exit removes the closed position; a partial reduction
        # shrinks it in place via dataclasses.replace (preserving all other fields).
        # This stops the price-driven observe from re-detecting the same stop/DOL
        # hit and broadcasting a SECOND real sell.
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
