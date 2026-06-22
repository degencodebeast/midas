from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class CostViabilityConfig:
    max_round_trip_cost_bps: Decimal = Decimal("150")


@dataclass(frozen=True)
class CostViabilityDecision:
    approved: bool
    denied_by: tuple[str, ...]
    evidence: dict[str, Any]


# PAPER quote schema (quotes.py PaperQuoteProvider._leg) carries explicit
# gas/fee/impact/slippage/notional fields. The REAL live quote (live_quotes.py
# TwakQuoteProvider) carries NONE of these — only output_qty/minimum_output (a
# slippage spread) and price_impact. We support BOTH and fail closed when neither
# is computable.
_PAPER_REQUIRED = {
    "output_qty",
    "minimum_output",
    "impact_bps",
    "slippage_bps",
    "expires_at",
    "gas_usd",
    "fee_usd",
    "notional_usd",
}
_LIVE_REQUIRED = {"output_qty", "minimum_output", "price_impact"}


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _decimal(value: object, code: str, denied: list[str]) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        denied.append(code)
        return Decimal("0")
    if not parsed.is_finite():
        denied.append(code)
        return Decimal("0")
    return parsed


def _spread_bps(output_qty: Decimal, minimum_output: Decimal) -> Decimal:
    """Slippage spread in bps: (output_qty - minimum_output) / output_qty * 10000."""
    return (output_qty - minimum_output) / output_qty * Decimal("10000")


def _validate_quote(
    name: str, quote: dict | None, now: str, denied: list[str]
) -> dict[str, Decimal] | None:
    """Validate one quote leg and return its per-leg cost components.

    Returns a dict with at least ``cost_bps`` (the per-leg cost in bps) and, for
    the paper schema, the additional fields used to compute fixed cost. Returns
    ``None`` and records a denial reason when the quote is missing/malformed or
    matches NEITHER the paper nor the live schema (fail closed).
    """
    if quote is None:
        denied.append(f"missing_{name}_quote")
        return None

    is_paper = _PAPER_REQUIRED <= set(quote)
    is_live = _LIVE_REQUIRED <= set(quote)

    if is_paper:
        return _validate_paper(name, quote, now, denied)
    if is_live:
        return _validate_live(name, quote, denied)

    # Neither schema is computable -> fail closed with a clear reason listing the
    # paper fields that are missing (the paper schema is the superset).
    missing = sorted(_PAPER_REQUIRED - set(quote))
    for field in missing:
        denied.append(f"{name}_missing_{field}")
    return None


def _validate_paper(
    name: str, quote: dict, now: str, denied: list[str]
) -> dict[str, Decimal]:
    # Expiry check applies only to the paper schema (the live CLI omits expires_at).
    try:
        if _parse_time(str(quote["expires_at"])) <= _parse_time(now):
            denied.append(f"{name}_quote_expired")
    except (ValueError, TypeError):
        denied.append(f"{name}_malformed_expires_at")
    values = {
        "schema": "paper",
        "output_qty": _decimal(quote["output_qty"], f"{name}_malformed_output_qty", denied),
        "minimum_output": _decimal(quote["minimum_output"], f"{name}_malformed_minimum_output", denied),
        "impact_bps": _decimal(quote["impact_bps"], f"{name}_malformed_impact_bps", denied),
        "slippage_bps": _decimal(quote["slippage_bps"], f"{name}_malformed_slippage_bps", denied),
        "gas_usd": _decimal(quote["gas_usd"], f"{name}_malformed_gas_usd", denied),
        "fee_usd": _decimal(quote["fee_usd"], f"{name}_malformed_fee_usd", denied),
        "notional_usd": _decimal(quote["notional_usd"], f"{name}_malformed_notional_usd", denied),
    }
    if values["output_qty"] <= 0:
        denied.append(f"{name}_nonpositive_output")
    if values["minimum_output"] <= 0:
        denied.append(f"{name}_nonpositive_minimum_output")
    if values["notional_usd"] <= 0:
        denied.append(f"{name}_nonpositive_notional")
    return values


def _validate_live(name: str, quote: dict, denied: list[str]) -> dict[str, Decimal]:
    # SPREAD-ONLY policy: the real CLI returns no gas/fee/notional, so we cannot
    # compute fixed (USD) cost. The (output_qty - minimum_output)/output_qty slippage
    # spread is unambiguous and reliable, so we cost on that.
    #
    # priceImpact UNIT IS UNCONFIRMED: the only real sample is "0", which gives no
    # signal about the unit (it could be percent, fraction, or bps). Guessing percent
    # and multiplying by 100 would understate impact 100x if it is actually a fraction
    # -> a too-lax gate that could approve a too-costly trade. So we FAIL CLOSED here:
    #   - priceImpact == 0  -> proceed on the spread-only cost (zero impact either way).
    #   - priceImpact != 0  -> deny ("{name}_price_impact_unit_unconfirmed").
    # TODO: confirm the priceImpact unit against a real NON-ZERO-impact twak quote, then
    # implement the correct unit conversion and re-enable nonzero-impact costing.
    output_qty = _decimal(quote["output_qty"], f"{name}_malformed_output_qty", denied)
    minimum_output = _decimal(quote["minimum_output"], f"{name}_malformed_minimum_output", denied)
    price_impact = _decimal(quote["price_impact"], f"{name}_malformed_price_impact", denied)
    if output_qty <= 0:
        denied.append(f"{name}_nonpositive_output")
    if minimum_output <= 0:
        denied.append(f"{name}_nonpositive_minimum_output")
    if price_impact != 0:
        denied.append(f"{name}_price_impact_unit_unconfirmed")
    return {
        "schema": "live",
        "output_qty": output_qty,
        "minimum_output": minimum_output,
        "price_impact": price_impact,
    }


def evaluate_cost_viability(
    *,
    buy_quote: dict | None,
    sell_quote: dict | None,
    intended_risk_fraction: Decimal,
    now: str,
    config: CostViabilityConfig | None = None,
) -> CostViabilityDecision:
    cfg = config or CostViabilityConfig()
    denied: list[str] = []
    buy = _validate_quote("buy", buy_quote, now, denied)
    sell = _validate_quote("sell", sell_quote, now, denied)
    if denied or buy is None or sell is None:
        return CostViabilityDecision(False, tuple(denied), {
            "approved": False,
            "intended_risk_fraction": str(intended_risk_fraction),
            "denied_by": tuple(denied),
        })

    if buy["schema"] == "paper" and sell["schema"] == "paper":
        notional = min(buy["notional_usd"], sell["notional_usd"])
        fixed_cost_bps = (
            buy["gas_usd"] + buy["fee_usd"] + sell["gas_usd"] + sell["fee_usd"]
        ) / notional * Decimal("10000")
        variable_cost_bps = (
            buy["impact_bps"] + buy["slippage_bps"] + sell["impact_bps"] + sell["slippage_bps"]
        )
        round_trip = fixed_cost_bps + variable_cost_bps
        evidence = {
            "approved": round_trip <= cfg.max_round_trip_cost_bps,
            "intended_risk_fraction": str(intended_risk_fraction),
            "estimated_notional_usd": str(notional),
            "buy_impact_bps": str(buy["impact_bps"]),
            "sell_impact_bps": str(sell["impact_bps"]),
            "estimated_round_trip_cost_bps": f"{round_trip:.2f}",
            "max_round_trip_cost_bps": str(cfg.max_round_trip_cost_bps),
        }
    else:
        # SPREAD-ONLY (live, or any mix involving a live leg): per-leg cost is the
        # slippage spread bps. We reach here only when every live leg had
        # priceImpact == 0 (any nonzero priceImpact already failed closed in
        # _validate_live, with the denial short-circuiting above), so there is no
        # priceImpact term to add — it is zero. No gas/fee data either.
        def _leg_cost(leg: dict[str, Decimal]) -> tuple[Decimal, Decimal]:
            spread = _spread_bps(leg["output_qty"], leg["minimum_output"])
            return spread, Decimal("0")

        buy_spread, buy_impact = _leg_cost(buy)
        sell_spread, sell_impact = _leg_cost(sell)
        round_trip = buy_spread + buy_impact + sell_spread + sell_impact
        evidence = {
            "approved": round_trip <= cfg.max_round_trip_cost_bps,
            "intended_risk_fraction": str(intended_risk_fraction),
            "cost_model": "spread_only",
            "buy_spread_bps": f"{buy_spread:.2f}",
            "sell_spread_bps": f"{sell_spread:.2f}",
            "buy_impact_bps": f"{buy_impact:.2f}",
            "sell_impact_bps": f"{sell_impact:.2f}",
            "estimated_round_trip_cost_bps": f"{round_trip:.2f}",
            "max_round_trip_cost_bps": str(cfg.max_round_trip_cost_bps),
        }

    if round_trip > cfg.max_round_trip_cost_bps:
        return CostViabilityDecision(False, ("round_trip_cost_too_high",), evidence)
    return CostViabilityDecision(True, (), evidence)
