from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LiveSellQuote:
    approved: bool
    quote: dict | None
    reasons: tuple[str, ...] = ()


class TwakSellPorts:
    def __init__(self, *, twak, stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
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
        payload = self._twak.json([
            "swap", str(decision.exit_quantity), token, self._stable_symbol,
            "--chain", self._chain, "--sell", "--json",
        ])
        return payload.get("data", {}).get("tx_hash") or payload.get("tx_hash") or "SUBMITTED"
