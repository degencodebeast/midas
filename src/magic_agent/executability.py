# executability.py
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from magic_agent.risk_policy import QuantityCaps
from magic_agent.spot_models import ActionPurpose


@dataclass(frozen=True)
class ExecutabilityDecision:
    approved: bool
    reasons: tuple[str, ...]
    quantity_caps: QuantityCaps


@dataclass(frozen=True)
class PreparedOrder:
    approved: bool
    reasons: tuple[str, ...]
    risk: object | None
    quote: dict | None


def validate_round_trip(buy: dict | None, sell: dict | None, *, now: str) -> ExecutabilityDecision:
    reasons: list[str] = []
    if buy is None:
        reasons.append("missing_buy_quote")
    if sell is None:
        reasons.append("missing_sell_quote")
    required = {"output_qty", "provider", "minimum_output", "impact_bps", "slippage_bps", "expires_at"}
    for name, quote in (("buy", buy), ("sell", sell)):
        if quote is None:
            continue
        missing = required - set(quote)
        if missing:
            reasons.append(f"{name}_missing_{'_'.join(sorted(missing))}")
        elif Decimal(str(quote["output_qty"])) <= 0 or Decimal(str(quote["minimum_output"])) <= 0:
            reasons.append(f"{name}_nonpositive_output")
        elif datetime.fromisoformat(str(quote["expires_at"]).replace("Z", "+00:00")) <= datetime.fromisoformat(now.replace("Z", "+00:00")):
            reasons.append(f"{name}_quote_expired")
    maximum = Decimal("Infinity")
    caps = QuantityCaps(maximum, maximum, maximum, maximum, Decimal("0"))
    return ExecutabilityDecision(not reasons, tuple(reasons), caps)


def prepare_exact_order(*, setup, market, risk_state, risk_policy, quote_provider) -> PreparedOrder:
    probe = quote_provider(setup, None)
    if not probe.approved:
        return PreparedOrder(False, probe.reasons, None, None)
    risk = risk_policy.evaluate(
        setup, risk_state, ActionPurpose.STRATEGY, probe.quantity_caps, market,
    )
    if not risk.approved or risk.final_qty <= 0:
        return PreparedOrder(False, ("risk_denied", risk.denied_by or "unknown"), risk, None)
    for _ in range(2):
        exact = quote_provider(setup, risk.final_qty)
        if not exact.approved:
            return PreparedOrder(False, exact.reasons, risk, None)
        revised = risk_policy.evaluate(
            setup, risk_state, ActionPurpose.STRATEGY, exact.quantity_caps, market,
        )
        if not revised.approved or revised.final_qty <= 0:
            return PreparedOrder(False, ("risk_denied", revised.denied_by or "unknown"), revised, None)
        if revised.final_qty == risk.final_qty:
            return PreparedOrder(True, (), revised, exact.quote)
        risk = revised
    return PreparedOrder(False, ("quote_size_did_not_converge",), risk, None)


class ExecutabilityProbe:
    def __init__(self, quote_provider) -> None:
        self.quote_provider = quote_provider

    def prepare_order(self, *, setup, market, risk_state, risk_policy) -> PreparedOrder:
        return prepare_exact_order(
            setup=setup, market=market, risk_state=risk_state,
            risk_policy=risk_policy, quote_provider=self.quote_provider,
        )
