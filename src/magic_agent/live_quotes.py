from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from magic_agent.risk_policy import QuantityCaps


@dataclass(frozen=True)
class TwakQuoteResult:
    approved: bool
    reasons: tuple[str, ...]
    quantity_caps: QuantityCaps
    quote: dict | None


_REQUIRED_QUOTE_FIELDS = (
    "output_qty",
    "minimum_output",
    "impact_bps",
    "slippage_bps",
    "expires_at",
    "gas_usd",
    "fee_usd",
    "notional_usd",
    "asset",
    "network",
)


class TwakQuoteProvider:
    """TWAK-backed quote provider matching prepare_exact_order's callable contract."""

    def __init__(
        self,
        *,
        twak,
        wallet_address: str,
        stable_symbol: str = "USDC",
        chain: str = "bsc",
        now: str,
        probe_quantity: Decimal = Decimal("1"),
    ) -> None:
        self._twak = twak
        self._wallet_address = wallet_address
        self._stable_symbol = stable_symbol
        self._chain = chain
        self._now = now
        self._probe_quantity = probe_quantity

    def __call__(self, setup, quantity: Decimal | None) -> TwakQuoteResult:
        qty = self._probe_quantity if quantity is None else quantity
        if qty <= 0:
            return TwakQuoteResult(False, ("nonpositive_quote_quantity",), QuantityCaps.unbounded(), None)
        payload = self._twak.json([
            "swap", str(qty), self._stable_symbol, setup.identity_key,
            "--chain", self._chain, "--quote-only", "--json",
        ])
        data = payload.get("data", payload)
        reasons = self._validate(data)
        if reasons:
            return TwakQuoteResult(False, reasons, QuantityCaps.unbounded(), None)
        quote = {field: str(data[field]) for field in _REQUIRED_QUOTE_FIELDS}
        quote["price"] = str(setup.entry)
        quote["symbol"] = setup.symbol
        quote["recipient"] = self._wallet_address
        return TwakQuoteResult(True, (), QuantityCaps.unbounded(), quote)

    def _validate(self, data: dict) -> tuple[str, ...]:
        reasons: list[str] = []
        for field in _REQUIRED_QUOTE_FIELDS:
            if field not in data:
                reasons.append(f"missing_{field}")
        if reasons:
            return tuple(reasons)
        if str(data["asset"]).upper() != self._stable_symbol.upper():
            reasons.append("unexpected_asset")
        if str(data["network"]).lower() != self._chain.lower():
            reasons.append("unexpected_network")
        for field in ("output_qty", "minimum_output", "notional_usd"):
            try:
                value = Decimal(str(data[field]))
            except (InvalidOperation, TypeError):
                reasons.append(f"malformed_{field}")
                continue
            if value <= 0:
                reasons.append(f"nonpositive_{field}")
        return tuple(reasons)
