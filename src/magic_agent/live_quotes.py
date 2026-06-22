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


def _parse_amount(s: object) -> tuple[Decimal, str]:
    """Parse a TWAK amount string like ``"7.06622917239096244 APE"``.

    Splits on the LAST space into (number, symbol), parses the number with
    Decimal. Raises ``ValueError`` (fail closed) on any malformed input: no
    space, empty, non-numeric number, or empty symbol.
    """
    text = str(s).strip()
    if " " not in text:
        raise ValueError(f"malformed twak amount (no space): {text!r}")
    number_part, symbol = text.rsplit(" ", 1)
    number_part = number_part.strip()
    symbol = symbol.strip()
    if not number_part or not symbol:
        raise ValueError(f"malformed twak amount (empty field): {text!r}")
    try:
        value = Decimal(number_part)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"malformed twak amount (non-numeric): {text!r}") from exc
    if not value.is_finite():
        raise ValueError(f"malformed twak amount (non-finite): {text!r}")
    return value, symbol


class TwakQuoteProvider:
    """TWAK-backed quote provider matching prepare_exact_order's callable contract.

    Reconciled against the REAL twak 0.19.1 CLI: a BUY quote is
    ``swap <usdc_amount> USDC <contract_address> --chain bsc --quote-only --json``
    and the response is the bare object
    ``{"input", "output", "minReceived", "provider", "priceImpact"}`` (no
    success/data wrapper, and NONE of gas_usd/fee_usd/expires_at/asset/network).
    The token arg is the resolved registry CONTRACT ADDRESS, never identity_key.
    """

    def __init__(
        self,
        *,
        twak,
        registry,
        wallet_address: str,
        stable_symbol: str = "USDC",
        chain: str = "bsc",
        now: str,
        probe_quantity: Decimal = Decimal("1"),
    ) -> None:
        self._twak = twak
        self._registry = registry
        self._wallet_address = wallet_address
        self._stable_symbol = stable_symbol
        self._chain = chain
        self._now = now
        self._probe_quantity = probe_quantity

    def __call__(self, setup, quantity: Decimal | None) -> TwakQuoteResult:
        qty = self._probe_quantity if quantity is None else quantity
        if qty <= 0:
            return TwakQuoteResult(
                False, ("nonpositive_quote_quantity",), QuantityCaps.unbounded(), None,
            )
        contract = self._registry.by_contract_key(setup.identity_key).contract_address
        # BUY: first positional is the USDC SOURCE amount to spend.
        payload = self._twak.json([
            "swap", str(qty), self._stable_symbol, contract,
            "--chain", self._chain, "--quote-only", "--json",
        ])
        approved, reasons, quote = self._build_quote(payload, qty, setup)
        if not approved:
            return TwakQuoteResult(False, reasons, QuantityCaps.unbounded(), None)
        return TwakQuoteResult(True, (), QuantityCaps.unbounded(), quote)

    def _build_quote(self, payload, usdc_in: Decimal, setup):
        if not isinstance(payload, dict):
            return False, ("malformed_quote_response",), None
        if payload.get("error") is not None:
            return False, ("twak_error",), None
        reasons: list[str] = []
        for field in ("input", "output", "minReceived"):
            if field not in payload:
                reasons.append(f"missing_{field}")
        if reasons:
            return False, tuple(reasons), None
        try:
            output_qty, output_symbol = _parse_amount(payload["output"])
            minimum_output, _ = _parse_amount(payload["minReceived"])
            _parse_amount(payload["input"])  # validate shape; value unused here
        except ValueError:
            return False, ("malformed_amount",), None
        if output_qty <= 0:
            return False, ("nonpositive_output",), None
        if minimum_output <= 0:
            return False, ("nonpositive_minimum_output",), None
        try:
            price_impact = Decimal(str(payload.get("priceImpact", "0")))
        except (InvalidOperation, TypeError):
            return False, ("malformed_price_impact",), None
        quote = {
            "output_qty": str(output_qty),
            "output_symbol": output_symbol,
            "minimum_output": str(minimum_output),
            "price": str(usdc_in / output_qty),
            "provider": str(payload.get("provider", "")),
            "price_impact": str(price_impact),
            "input": str(payload["input"]),
            "output": str(payload["output"]),
            "minReceived": str(payload["minReceived"]),
            "symbol": setup.symbol,
            "recipient": self._wallet_address,
        }
        return True, (), quote
