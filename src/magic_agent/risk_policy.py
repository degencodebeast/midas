from dataclasses import dataclass, fields, replace
from decimal import Decimal
import hashlib
import json

from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


@dataclass(frozen=True)
class RiskConfig:
    # FIXED-MARGIN sizing. The position is a fixed % of EQUITY (the deployed
    # notional), NOT derived from the stop. ``a_grade_margin_fraction`` /
    # ``b_grade_margin_fraction`` are the per-trade deploy-% of equity; the scanner
    # stop is exit geometry only (used for risk TRACKING + the safety ceilings, never
    # to size the position). Actual per-trade risk = deploy_notional * stop_distance%
    # — an OUTPUT, small.
    canary_margin_fraction: Decimal
    a_grade_margin_fraction: Decimal
    b_grade_margin_fraction: Decimal
    counter_bias_multiplier: Decimal
    max_open_risk: Decimal
    max_correlation_bucket_risk: Decimal
    max_concurrent_positions: int
    hard_max_concurrent_positions: int
    max_token_fraction: Decimal
    stable_reserve_fraction: Decimal
    daily_loss_fraction: Decimal
    consecutive_stop_halt: int
    # ---- Graduated drawdown ladder (highest band first) -------------------------
    # drawdown = (peak_equity - equity)/peak_equity. Applied in order:
    #   >= hard_drawdown_dq        -> DENY hard_drawdown_dq
    #   >= drawdown_hard_review    -> DENY drawdown_hard_review (no new entries)
    #   >= drawdown_entry_halt     -> DENY drawdown_entry_halt (halt new entries)
    #   >= drawdown_defense        -> DEFENSE: margin x defense mult, A-grade only,
    #                                 max ``drawdown_defense_max_positions``
    #   >= drawdown_throttle       -> THROTTLE: margin x throttle mult, A+B,
    #                                 max ``drawdown_throttle_max_positions``
    #   <  drawdown_throttle       -> NORMAL: margin x1, A+B, full concurrency
    # Protective EXITS still run while new entries are halted (the RISK_EXIT path and
    # position_risk_reduction are not gated by these bands).
    drawdown_throttle: Decimal
    drawdown_defense: Decimal
    drawdown_entry_halt: Decimal
    drawdown_hard_review: Decimal
    hard_drawdown_dq: Decimal
    drawdown_throttle_multiplier: Decimal
    drawdown_defense_multiplier: Decimal
    drawdown_throttle_max_positions: int
    drawdown_defense_max_positions: int

    def __post_init__(self) -> None:
        if self.max_concurrent_positions > self.hard_max_concurrent_positions:
            raise ValueError("concurrent strategy positions exceed the hard maximum")
        if self.hard_max_concurrent_positions < 3:
            raise ValueError("Track 1 fixed-margin model requires >=3 concurrent slots")
        if not (
            self.drawdown_throttle
            < self.drawdown_defense
            < self.drawdown_entry_halt
            < self.drawdown_hard_review
            < self.hard_drawdown_dq
        ):
            raise ValueError("drawdown ladder thresholds must be strictly ordered")

    @classmethod
    def defaults(cls) -> "RiskConfig":
        # FIXED-MARGIN small-account profile for the ~$20 live competition wallet
        # (~$10 deployable USDC). Each trade deploys a fixed % of EQUITY:
        #   * A-grade aligned -> 5% of equity (on $20 => ~$1.00) REGARDLESS of the
        #     scanner stop distance. B-grade -> 2.5% (=> ~$0.50). Counter-bias keeps
        #     the 0.5x multiplier (applied to the margin %).
        #   * The scanner stop NO LONGER sizes the position; it is exit geometry only,
        #     used for risk tracking + the safety ceilings (open_risk_cap /
        #     correlation_bucket_cap / daily_loss_cap), so a pathologically WIDE stop
        #     can still TRIM/deny via open_risk_cap.
        #   * max_concurrent_positions = 3 -> max deployed ~3x5% = 15% of equity
        #     (A-only); enforced naturally by concurrency x margin (no separate cap).
        #   * cash_cap (don't deploy more USDC than held, minus a small stable
        #     reserve), token_cap, and macro_clamp are KEPT. At ~$1 deploy the cash
        #     cap (~$9.70 of USDC) no longer binds — the margin binds.
        #   * Graduated drawdown ladder REPLACES the old hard-5%-halt: <5% normal,
        #     5-10% throttle (0.5x, max 2 positions, A+B), 10-15% defense (0.25x,
        #     max 1, A-only), 15-20% halt new entries, 20-30% hard review, >=30% DQ.
        # Core safety KEPT: 30% hard-DQ, a (small) stable reserve, and the
        # consecutive-stop halt (kept at 3).
        return cls(
            canary_margin_fraction=Decimal("0.05"),
            a_grade_margin_fraction=Decimal("0.05"),
            b_grade_margin_fraction=Decimal("0.025"),
            counter_bias_multiplier=Decimal("0.50"),
            max_open_risk=Decimal("0.06"),
            max_correlation_bucket_risk=Decimal("0.06"),
            max_concurrent_positions=3,
            hard_max_concurrent_positions=3,
            max_token_fraction=Decimal("0.50"),
            stable_reserve_fraction=Decimal("0.015"),
            daily_loss_fraction=Decimal("0.10"),
            consecutive_stop_halt=3,
            drawdown_throttle=Decimal("0.05"),
            drawdown_defense=Decimal("0.10"),
            drawdown_entry_halt=Decimal("0.15"),
            drawdown_hard_review=Decimal("0.20"),
            hard_drawdown_dq=Decimal("0.30"),
            drawdown_throttle_multiplier=Decimal("0.50"),
            drawdown_defense_multiplier=Decimal("0.25"),
            drawdown_throttle_max_positions=2,
            drawdown_defense_max_positions=1,
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
    # ``risk_fraction`` now carries the effective MARGIN fraction (deploy-% of equity)
    # after grade / counter-bias / drawdown-band multipliers. The field name is
    # retained for the downstream narrative + cost-viability passthrough.
    risk_fraction: Decimal
    # ``risk_budget_usd`` now carries the deploy NOTIONAL (equity * margin fraction).
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

    # ---- Graduated drawdown ladder (highest band first) -------------------------
    drawdown = max(zero, (state.peak_equity_usd - state.equity_usd) / state.peak_equity_usd)
    if drawdown >= config.hard_drawdown_dq:
        return deny("hard_drawdown_dq")
    if drawdown >= config.drawdown_hard_review:
        return deny("drawdown_hard_review")
    if drawdown >= config.drawdown_entry_halt:
        return deny("drawdown_entry_halt")

    # De-rating bands: defense (A-only, 0.25x, max 1) and throttle (A+B, 0.5x, max 2).
    band_margin_mult = Decimal("1")
    band_max_positions = config.max_concurrent_positions
    in_defense_band = drawdown >= config.drawdown_defense
    if in_defense_band:
        band_margin_mult = config.drawdown_defense_multiplier
        band_max_positions = config.drawdown_defense_max_positions
    elif drawdown >= config.drawdown_throttle:
        band_margin_mult = config.drawdown_throttle_multiplier
        band_max_positions = config.drawdown_throttle_max_positions

    if state.consecutive_stops >= config.consecutive_stop_halt:
        return deny("consecutive_stop_halt")
    # The per-band max-positions OVERRIDES the normal concurrency cap when in-band.
    if state.open_strategy_positions >= band_max_positions:
        return deny("concurrency_cap")

    if setup.grade.startswith("A"):
        margin_fraction = config.a_grade_margin_fraction
    elif setup.grade.startswith("B"):
        if in_defense_band:
            return deny("drawdown_defense_a_only")
        margin_fraction = config.b_grade_margin_fraction
    else:
        return deny("unsupported_grade")
    if market.canary:
        margin_fraction = min(margin_fraction, config.canary_margin_fraction)
    if setup.bias_alignment == "counter_bias":
        if market.momentum_7d <= 0 or market.momentum_7d_rank_pct > Decimal("0.25"):
            return deny("counter_bias_momentum", "counter_bias_requires_positive_top_quartile_7d")
        margin_fraction *= config.counter_bias_multiplier
    margin_fraction *= band_margin_mult

    # ---- FIXED-MARGIN sizing: deploy a fixed % of EQUITY as notional. -----------
    # The scanner stop (loss_per_unit) is exit geometry only — it no longer sizes the
    # position. It IS used below for risk tracking + the safety ceilings.
    loss_per_unit = setup.entry - setup.structural_stop
    if loss_per_unit <= 0:
        return deny("geometry", "nonpositive_loss")
    deploy_notional = state.equity_usd * margin_fraction
    base = deploy_notional / setup.entry
    cash_cap = max(Decimal("0"), state.cash_usd - state.equity_usd * config.stable_reserve_fraction) / setup.entry
    token_cap = state.equity_usd * config.max_token_fraction / setup.entry
    capped, cap_reason = apply_quantity_caps(
        base_qty=min(base, cash_cap, token_cap), entry=setup.entry, caps=caps,
    )
    final = capped * market.macro_clamp
    # Risk TRACKING + safety ceilings use the stop. A wide stop -> high stressed_loss
    # -> open_risk_cap / correlation_bucket_cap / daily_loss_cap can still TRIM or deny.
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
    return RiskDecision(True, None, (), margin_fraction, deploy_notional, base, final)


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
