from dataclasses import dataclass, fields, replace
from decimal import Decimal
import hashlib
import json

from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


@dataclass(frozen=True)
class RiskConfig:
    canary_risk_fraction: Decimal
    a_grade_risk_fraction: Decimal
    b_grade_risk_fraction: Decimal
    counter_bias_multiplier: Decimal
    drawdown_throttle_multiplier: Decimal
    max_open_risk: Decimal
    max_correlation_bucket_risk: Decimal
    max_concurrent_positions: int
    hard_max_concurrent_positions: int
    max_token_fraction: Decimal
    stable_reserve_fraction: Decimal
    daily_loss_fraction: Decimal
    consecutive_stop_halt: int
    drawdown_throttle: Decimal
    drawdown_entry_halt: Decimal
    drawdown_review: Decimal
    hard_drawdown_dq: Decimal

    def __post_init__(self) -> None:
        if self.max_concurrent_positions > self.hard_max_concurrent_positions or self.hard_max_concurrent_positions > 2:
            raise ValueError("concurrent strategy positions exceed the Track 1 hard maximum")
        if not (self.drawdown_throttle < self.drawdown_entry_halt < self.drawdown_review < self.hard_drawdown_dq):
            raise ValueError("drawdown thresholds must be strictly ordered")

    @classmethod
    def defaults(cls) -> "RiskConfig":
        return cls(
            Decimal("0.0025"), Decimal("0.005"), Decimal("0.0025"), Decimal("0.50"),
            Decimal("0.50"), Decimal("0.01"), Decimal("0.01"), 1, 2,
            Decimal("0.25"), Decimal("0.30"), Decimal("0.015"), 3,
            Decimal("0.03"), Decimal("0.05"), Decimal("0.08"), Decimal("0.30"),
        )

    def stable_hash(self) -> str:
        payload = {field.name: str(getattr(self, field.name)) for field in fields(self)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PortfolioRiskState:
    equity_usd: Decimal
    cash_usd: Decimal
    peak_equity_usd: Decimal
    daily_anchor_usd: Decimal
    open_stressed_loss_usd: Decimal
    correlation_bucket_stressed_loss_usd: Decimal = Decimal("0")
    open_strategy_positions: int = 0
    consecutive_stops: int = 0
    equity_fresh: bool = True
    reconciled_position: bool = False

    @classmethod
    def example(cls, **changes: object) -> "PortfolioRiskState":
        return replace(cls(Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("0")), **changes)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    denied_by: str | None
    reasons: tuple[str, ...]
    risk_fraction: Decimal
    risk_budget_usd: Decimal
    base_qty: Decimal
    final_qty: Decimal


@dataclass(frozen=True)
class RiskReductionDecision:
    reduction_qty: Decimal
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MarketRiskContext:
    momentum_7d: Decimal
    momentum_7d_rank_pct: Decimal
    macro_clamp: Decimal
    canary: bool

    @classmethod
    def aligned(cls) -> "MarketRiskContext":
        return cls(Decimal("0"), Decimal("1"), Decimal("1"), False)

    @classmethod
    def counter_bias_qualified(cls) -> "MarketRiskContext":
        return cls(Decimal("0.01"), Decimal("0.25"), Decimal("1"), False)


@dataclass(frozen=True)
class QuantityCaps:
    liquidity_qty: Decimal
    pool_share_qty: Decimal
    concentration_qty: Decimal
    gap_stress_qty: Decimal
    minimum_notional_usd: Decimal

    @classmethod
    def unbounded(cls) -> "QuantityCaps":
        maximum = Decimal("Infinity")
        return cls(maximum, maximum, maximum, maximum, Decimal("0"))


def apply_quantity_caps(*, base_qty: Decimal, entry: Decimal, caps: QuantityCaps) -> tuple[Decimal, str | None]:
    final = min(base_qty, caps.liquidity_qty, caps.pool_share_qty,
                caps.concentration_qty, caps.gap_stress_qty)
    if final * entry < caps.minimum_notional_usd:
        return (Decimal("0"), "below_minimum_notional")
    return (final, None)


def evaluate_risk(setup: AuthorizedSetup, state: PortfolioRiskState,
                  config: RiskConfig | None, purpose: ActionPurpose,
                  caps: QuantityCaps, market: MarketRiskContext) -> RiskDecision:
    zero = Decimal("0")

    def deny(code: str, *reasons: str) -> RiskDecision:
        return RiskDecision(False, code, tuple(reasons or (code,)), zero, zero, zero, zero)

    if not Decimal("0") <= market.macro_clamp <= Decimal("1"):
        raise ValueError("macro clamp must only preserve or reduce quantity")
    if config is None:
        allowed_exit = purpose is ActionPurpose.RISK_EXIT and state.reconciled_position
        return RiskDecision(allowed_exit, None if allowed_exit else "missing_policy", ("fail_safe",), zero, zero, zero, zero)
    if purpose is ActionPurpose.RISK_EXIT:
        return RiskDecision(state.reconciled_position, None if state.reconciled_position else "unreconciled", (), zero, zero, zero, zero)
    if not state.equity_fresh:
        return deny("stale_equity")
    drawdown = max(zero, (state.peak_equity_usd - state.equity_usd) / state.peak_equity_usd)
    if drawdown >= config.hard_drawdown_dq:
        return deny("hard_drawdown_dq")
    if drawdown >= config.drawdown_review:
        return deny("drawdown_emergency_review")
    if drawdown >= config.drawdown_entry_halt:
        return deny("drawdown_entry_halt")
    if state.consecutive_stops >= config.consecutive_stop_halt:
        return deny("consecutive_stop_halt")
    if state.open_strategy_positions >= config.max_concurrent_positions:
        return deny("concurrency_cap")
    if setup.grade.startswith("A"):
        risk_fraction = config.a_grade_risk_fraction
    elif setup.grade.startswith("B"):
        risk_fraction = config.b_grade_risk_fraction
    else:
        return deny("unsupported_grade")
    if market.canary:
        risk_fraction = min(risk_fraction, config.canary_risk_fraction)
    if setup.bias_alignment == "counter_bias":
        if market.momentum_7d <= 0 or market.momentum_7d_rank_pct > Decimal("0.25"):
            return deny("counter_bias_momentum", "counter_bias_requires_positive_top_quartile_7d")
        risk_fraction *= config.counter_bias_multiplier
    if drawdown >= config.drawdown_throttle:
        risk_fraction *= config.drawdown_throttle_multiplier
    loss_per_unit = setup.entry - setup.structural_stop
    if loss_per_unit <= 0:
        return deny("geometry", "nonpositive_loss")
    budget = state.equity_usd * risk_fraction
    base = budget / loss_per_unit
    cash_cap = max(Decimal("0"), state.cash_usd - state.equity_usd * config.stable_reserve_fraction) / setup.entry
    token_cap = state.equity_usd * config.max_token_fraction / setup.entry
    capped, cap_reason = apply_quantity_caps(
        base_qty=min(base, cash_cap, token_cap), entry=setup.entry, caps=caps,
    )
    final = capped * market.macro_clamp
    new_stressed_loss = final * loss_per_unit
    projected = state.open_stressed_loss_usd + new_stressed_loss
    projected_bucket = state.correlation_bucket_stressed_loss_usd + new_stressed_loss
    daily_loss = max(Decimal("0"), state.daily_anchor_usd - state.equity_usd)
    within_daily = daily_loss + projected <= state.daily_anchor_usd * config.daily_loss_fraction
    if cap_reason:
        return deny(cap_reason)
    if final <= 0:
        return deny("zero_safe_quantity")
    if projected > state.equity_usd * config.max_open_risk:
        return deny("open_risk_cap")
    if projected_bucket > state.equity_usd * config.max_correlation_bucket_risk:
        return deny("correlation_bucket_cap")
    if not within_daily:
        return deny("daily_loss_cap")
    return RiskDecision(True, None, (), risk_fraction, budget, base, final)


def position_risk_reduction(position, state: PortfolioRiskState,
                            config: RiskConfig | None) -> RiskReductionDecision:
    if config is None:
        return RiskReductionDecision(position.quantity, ("missing_policy_reduce_only",))
    actual_daily_loss = max(Decimal("0"), state.daily_anchor_usd - state.equity_usd)
    daily_room = max(
        Decimal("0"), state.daily_anchor_usd * config.daily_loss_fraction - actual_daily_loss,
    )
    open_risk_room = state.equity_usd * config.max_open_risk
    allowed = min(daily_room, open_risk_room)
    excess = max(Decimal("0"), state.open_stressed_loss_usd - allowed)
    if excess == 0:
        return RiskReductionDecision(Decimal("0"), ())
    if position.stressed_loss_per_unit <= 0:
        return RiskReductionDecision(position.quantity, ("invalid_position_stress_reduce_only",))
    quantity = min(position.quantity, excess / position.stressed_loss_per_unit)
    return RiskReductionDecision(quantity, ("open_or_daily_risk_exceeds_budget",))


class RiskPolicy:
    def __init__(self, config: RiskConfig | None) -> None:
        self.config = config

    def evaluate(self, setup: AuthorizedSetup, state: PortfolioRiskState,
                 purpose: ActionPurpose, caps: QuantityCaps,
                 market: MarketRiskContext) -> RiskDecision:
        return evaluate_risk(setup, state, self.config, purpose, caps, market)

    def reduction_for(self, position, state: PortfolioRiskState) -> RiskReductionDecision:
        return position_risk_reduction(position, state, self.config)
