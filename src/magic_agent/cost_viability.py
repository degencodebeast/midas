from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


_REQUIRED = {
    "output_qty",
    "minimum_output",
    "impact_bps",
    "slippage_bps",
    "expires_at",
    "gas_usd",
    "fee_usd",
    "notional_usd",
}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _decimal(value: object, code: str, denied: list[str]) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        denied.append(code)
        return Decimal("0")


def _validate_quote(name: str, quote: dict | None, now: str, denied: list[str]) -> dict[str, Decimal]:
    if quote is None:
        denied.append(f"missing_{name}_quote")
        return {}
    missing = sorted(_REQUIRED - set(quote))
    for field in missing:
        denied.append(f"{name}_missing_{field}")
    if missing:
        return {}
    try:
        if _parse_time(str(quote["expires_at"])) <= _parse_time(now):
            denied.append(f"{name}_quote_expired")
    except ValueError:
        denied.append(f"{name}_malformed_expires_at")
    values = {
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
    if denied:
        return CostViabilityDecision(False, tuple(denied), {
            "approved": False,
            "intended_risk_fraction": str(intended_risk_fraction),
            "denied_by": tuple(denied),
        })
    notional = min(buy["notional_usd"], sell["notional_usd"])
    fixed_cost_bps = (buy["gas_usd"] + buy["fee_usd"] + sell["gas_usd"] + sell["fee_usd"]) / notional * Decimal("10000")
    variable_cost_bps = buy["impact_bps"] + buy["slippage_bps"] + sell["impact_bps"] + sell["slippage_bps"]
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
    if round_trip > cfg.max_round_trip_cost_bps:
        return CostViabilityDecision(False, ("round_trip_cost_too_high",), evidence)
    return CostViabilityDecision(True, (), evidence)
