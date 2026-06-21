from decimal import Decimal

from magic_agent.risk_policy import (
    MarketRiskContext, PortfolioRiskState, QuantityCaps, RiskConfig,
    evaluate_risk, position_risk_reduction,
)
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


def test_missing_config_denies_entry_but_allows_reconciled_exit():
    state = PortfolioRiskState.example(reconciled_position=True)
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    assert not evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.STRATEGY, caps, market).approved
    assert evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.RISK_EXIT, caps, market).approved


def test_wider_stop_reduces_quantity_and_caps_never_increase_it():
    state = PortfolioRiskState.example(equity_usd=Decimal("1000"))
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    narrow = evaluate_risk(AuthorizedSetup.example(structural_stop=Decimal("95")), state, config, ActionPurpose.STRATEGY, caps, market)
    wide = evaluate_risk(AuthorizedSetup.example(structural_stop=Decimal("80")), state, config, ActionPurpose.STRATEGY, caps, market)
    assert narrow.final_qty > wide.final_qty
    assert narrow.final_qty <= narrow.base_qty


def test_grade_and_counter_bias_momentum_reduce_risk_without_tightening_stop():
    state = PortfolioRiskState.example(equity_usd=Decimal("1000"))
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    aligned_a = evaluate_risk(AuthorizedSetup.example(grade="A", bias_alignment="aligned"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.aligned())
    counter_a = evaluate_risk(AuthorizedSetup.example(grade="A", bias_alignment="counter_bias"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.counter_bias_qualified())
    counter_b = evaluate_risk(AuthorizedSetup.example(grade="B", bias_alignment="counter_bias"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.counter_bias_qualified())
    assert aligned_a.risk_fraction == Decimal("0.005")
    assert counter_a.risk_fraction == Decimal("0.0025")
    assert counter_b.risk_fraction == Decimal("0.00125")
    assert counter_a.final_qty == aligned_a.final_qty / 2


def test_counter_bias_requires_positive_top_quartile_seven_day_momentum():
    decision = evaluate_risk(
        AuthorizedSetup.example(bias_alignment="counter_bias"),
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(),
        MarketRiskContext(Decimal("-0.01"), Decimal("0.10"), Decimal("1"), False),
    )
    assert not decision.approved
    assert decision.denied_by == "counter_bias_momentum"


def test_real_scanner_counter_bias_promotion_reaches_half_risk(monkeypatch):
    import pandas as pd

    import magic_scanner.authorization as scanner_auth
    from magic_agent.scanner_gateway import authorized_setup_from_scan
    from magic_scanner.authorization import authorize_track1_setup
    from magic_scanner.detectors.dol import DolTarget
    from magic_scanner.detectors.entry import EntrySetup
    from magic_scanner.detectors.poi import HtfPoiSourceSet, HtfPoiZone
    from magic_scanner.detectors.risk import StopPlan
    from magic_scanner.detectors.swings import DealingRange
    from magic_scanner.detectors.trade_levels import TradeLevels
    from magic_scanner.engine import evaluate_v2
    from magic_scanner.scan import ScanResult
    from magic_scanner.types import ChecklistInputs

    inputs = ChecklistInputs(
        h12_liquidity_sweep=False, orderflow="Bullish", htf_poi=True,
        draw_on_liquidity=False, htf_bias="Bearish",
        weekly_range="Discount", h12_range="Discount",
        trade_direction="Long", entry_type="Aggressive",
    )
    result = evaluate_v2(inputs)
    assert (result.rating, result.regime) == ("C", "counter-bias")

    entry = EntrySetup(
        97.0, "Aggressive", "none", False, False, False, None,
        97.0, pd.Timestamp("2026-06-21T00:00:00Z"), None, None,
        qml_id="Long:10:97", qml_state="active",
    )
    levels = TradeLevels(
        97.0, StopPlan(89.5, 90.0, "h12_pivot", 8),
        DolTarget(120.0, "prior_week"), (DolTarget(120.0, "prior_week"),),
    )
    zone = HtfPoiZone(
        "Breaker", "Bullish", 100.0, 95.0, 5, 7, True, "lux_breaker_block",
    )
    monkeypatch.setattr(
        scanner_auth, "build_htf_poi_source",
        lambda *args, **kwargs: HtfPoiSourceSet((zone,), (), (zone,)),
    )
    monkeypatch.setattr(
        scanner_auth, "dealing_range",
        lambda *args, **kwargs: DealingRange(140.0, 60.0, 100.0, "Bullish"),
    )
    authorization = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}), requested_side="Long",
        inputs=inputs, result=result, entry=entry, levels=levels, left=2, right=2,
    )
    scan = ScanResult(
        "ZEC/USDT", inputs, result, entry=entry, levels=levels,
        authorization=authorization,
    )

    setup = authorized_setup_from_scan(
        scan, identity_key="zec-bsc", scanner_commit="scanner-sha",
    )
    assert setup is not None
    assert (setup.raw_grade, setup.grade) == ("C", "B-")
    assert setup.grade_promotion_reason == "track1_counter_bias_structural"
    assert setup.bias_alignment == "counter_bias"

    decision = evaluate_risk(
        setup, PortfolioRiskState.example(equity_usd=Decimal("1000")),
        RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.counter_bias_qualified(),
    )
    assert decision.approved
    assert decision.risk_fraction == Decimal("0.00125")


def test_drawdown_consecutive_stop_concurrency_and_stale_equity_gates():
    setup = AuthorizedSetup.example()
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    assert evaluate_risk(setup, PortfolioRiskState.example(equity_usd=Decimal("950")), config, ActionPurpose.STRATEGY, caps, market).denied_by == "drawdown_entry_halt"
    assert evaluate_risk(setup, PortfolioRiskState.example(consecutive_stops=3), config, ActionPurpose.STRATEGY, caps, market).denied_by == "consecutive_stop_halt"
    assert evaluate_risk(setup, PortfolioRiskState.example(open_strategy_positions=1), config, ActionPurpose.STRATEGY, caps, market).denied_by == "concurrency_cap"
    assert evaluate_risk(setup, PortfolioRiskState.example(equity_fresh=False), config, ActionPurpose.STRATEGY, caps, market).denied_by == "stale_equity"


def test_confirmed_thirty_percent_drawdown_is_an_explicit_hard_dq_guard():
    decision = evaluate_risk(
        AuthorizedSetup.example(), PortfolioRiskState.example(equity_usd=Decimal("700")),
        RiskConfig.defaults(), ActionPurpose.STRATEGY, QuantityCaps.unbounded(),
        MarketRiskContext.aligned(),
    )
    assert not decision.approved
    assert decision.denied_by == "hard_drawdown_dq"


def test_three_percent_drawdown_throttles_and_correlation_bucket_caps_all_longs():
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    normal = evaluate_risk(AuthorizedSetup.example(), PortfolioRiskState.example(), config, ActionPurpose.STRATEGY, caps, market)
    throttled = evaluate_risk(AuthorizedSetup.example(), PortfolioRiskState.example(equity_usd=Decimal("970"), daily_anchor_usd=Decimal("970")), config, ActionPurpose.STRATEGY, caps, market)
    assert throttled.risk_fraction == normal.risk_fraction * Decimal("0.50")
    bucket_full = evaluate_risk(
        AuthorizedSetup.example(),
        PortfolioRiskState.example(correlation_bucket_stressed_loss_usd=Decimal("8")),
        config, ActionPurpose.STRATEGY, caps, market,
    )
    assert bucket_full.denied_by == "correlation_bucket_cap"


def test_position_risk_reduction_returns_quantity_needed_to_restore_budget():
    state = PortfolioRiskState.example(
        equity_usd=Decimal("1000"), daily_anchor_usd=Decimal("1000"),
        open_stressed_loss_usd=Decimal("15"),
    )
    position = type("Position", (), {
        "quantity": Decimal("2"), "stressed_loss_per_unit": Decimal("10"),
    })()
    decision = position_risk_reduction(position, state, RiskConfig.defaults())
    assert decision.reduction_qty == Decimal("0.5")
    assert decision.reasons == ("open_or_daily_risk_exceeds_budget",)


# REQ-044 deny-path regression locks ------------------------------------------------
# Each test isolates exactly one gate so no adjacent gate can mask the target denial.


def test_req044_drawdown_emergency_review_at_eight_percent_ladder_rung():
    # equity=920 → drawdown=8% ≥ drawdown_review(0.08); below hard_drawdown_dq(0.30).
    # drawdown_entry_halt(0.05) would fire at 5%, but the review rung is checked AFTER
    # hard_drawdown_dq and BEFORE drawdown_entry_halt — drawdown=0.08 trips review first.
    decision = evaluate_risk(
        AuthorizedSetup.example(),
        PortfolioRiskState.example(equity_usd=Decimal("920")),
        RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "drawdown_emergency_review"


def test_req044_open_risk_cap_isolated_from_correlation_bucket_cap():
    # open_risk_cap is checked BEFORE correlation_bucket_cap (lines 184 vs 187 in source).
    # correlation_bucket_stressed_loss_usd=0 keeps projected_bucket=new_stressed_loss well
    # within the bucket cap (5 < 10), so open_risk_cap fires alone on the portfolio total.
    # open_stressed_loss_usd=9.5 + new_stressed_loss=5 → projected=14.5 > equity*0.01=10.
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("99")),
        PortfolioRiskState.example(
            equity_usd=Decimal("1000"),
            open_stressed_loss_usd=Decimal("9.5"),
            correlation_bucket_stressed_loss_usd=Decimal("0"),  # isolates from bucket cap
        ),
        RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "open_risk_cap"


def test_req044_daily_loss_cap_denies_when_existing_loss_plus_new_exposure_exceeds_fraction():
    # existing daily_loss=15 (daily_anchor=1000, equity=985) + new stressed_loss=5 = 20 >
    # daily_anchor*0.015=15 → daily_loss_cap.  open_stressed_loss_usd=0 keeps projected=5
    # below open_risk_cap(10) and correlation_bucket_cap(10), so neither fires first.
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("99")),
        PortfolioRiskState.example(
            equity_usd=Decimal("985"),
            daily_anchor_usd=Decimal("1000"),
            open_stressed_loss_usd=Decimal("0"),
            correlation_bucket_stressed_loss_usd=Decimal("0"),
        ),
        RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "daily_loss_cap"


def test_req044_unsupported_grade_outside_a_b_family_is_denied():
    # Grade "C" does not start with "A" or "B"; the policy has no promotion path for it.
    # All gates before the grade branch (drawdown, stops, concurrency) are bypassed by
    # using a clean PortfolioRiskState.example() with no adverse values.
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="C"),
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "unsupported_grade"


def test_req044_geometry_nonpositive_loss_when_stop_equals_entry():
    # structural_stop=entry → loss_per_unit=0 → nonpositive_loss geometry denial.
    # AuthorizedSetup.__post_init__ requires stop < entry < campaign_dol, so we set
    # structural_stop just below entry (0.01 gap) then override to equal via example()
    # which bypasses the guard — but example() internally uses replace() which re-runs
    # __post_init__.  Use entry=100, structural_stop=99.99 then test stop >= entry by
    # setting structural_stop=100.01 is also invalid.  Correct approach: use a stop of
    # 99 and entry 99 is forbidden.  Instead use entry=100, structural_stop=99.99 gives
    # loss=0.01 > 0, which passes.  To get nonpositive we need stop=entry: construct
    # manually with a mock that bypasses validation, or use the dict-based approach.
    # Simplest: use object() trick identical to the position mock pattern already in this
    # file — bypass dataclass __post_init__ via object.__setattr__ on a new frozen copy.
    # Actually, AuthorizedSetup.example() calls replace() which triggers __post_init__.
    # We exploit that entry=100 and structural_stop=100 would fail __post_init__.
    # Instead: pass entry=Decimal("100"), structural_stop=Decimal("100") via a plain
    # object that duck-types AuthorizedSetup (evaluate_risk accesses .grade, .entry,
    # .structural_stop, .bias_alignment only).
    class _FakeSetup:
        grade = "A"
        entry = Decimal("100")
        structural_stop = Decimal("100")  # loss_per_unit = 0 → nonpositive_loss
        bias_alignment = "aligned"

    decision = evaluate_risk(
        _FakeSetup(),  # type: ignore[arg-type]
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "geometry"
    assert "nonpositive_loss" in decision.reasons


def test_req044_below_minimum_notional_returns_zero_quantity():
    # QuantityCaps.minimum_notional_usd set so high that even the full base_qty * entry
    # falls short.  No other cap fires: liquidity/pool/concentration/gap are Infinity.
    # All prior gates (drawdown, stops, concurrency, grade) pass with default example state.
    caps = QuantityCaps(
        liquidity_qty=Decimal("Infinity"),
        pool_share_qty=Decimal("Infinity"),
        concentration_qty=Decimal("Infinity"),
        gap_stress_qty=Decimal("Infinity"),
        minimum_notional_usd=Decimal("9999"),  # entry(100)*base_qty(0.5)=50 < 9999
    )
    decision = evaluate_risk(
        AuthorizedSetup.example(),
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        caps, MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "below_minimum_notional"


def test_req044_zero_safe_quantity_when_macro_clamp_zeros_out_sized_quantity():
    # macro_clamp=0 collapses a positive capped qty to 0 after apply_quantity_caps.
    # cap_reason=None (minimum_notional=0, caps unbounded), so the zero_safe_quantity
    # branch fires.  This is distinct from below_minimum_notional: the cap routine
    # returns a positive qty but the macro_clamp multiplication zeroes it.
    market = MarketRiskContext(
        momentum_7d=Decimal("0"),
        momentum_7d_rank_pct=Decimal("1"),
        macro_clamp=Decimal("0"),  # valid in [0,1]; collapses final qty to 0
        canary=False,
    )
    decision = evaluate_risk(
        AuthorizedSetup.example(),
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), market,
    )
    assert decision.approved is False
    assert decision.denied_by == "zero_safe_quantity"


def test_req044_risk_exit_denied_when_position_not_reconciled():
    # RISK_EXIT branch is entered before any grade/drawdown gate when config is present.
    # reconciled_position=False → "unreconciled" denial proves exit is fail-closed when
    # the position record has not been confirmed — complements the existing exit-allowed test.
    decision = evaluate_risk(
        AuthorizedSetup.example(),
        PortfolioRiskState.example(reconciled_position=False),
        RiskConfig.defaults(), ActionPurpose.RISK_EXIT,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "unreconciled"


def test_req044_missing_policy_denies_strategy_and_reduction_failsafe_returns_full_qty():
    # config=None + STRATEGY → "missing_policy" (fail-closed, no approval).
    # Distinct from the existing test which only checks STRATEGY denied + EXIT allowed;
    # this additionally pins the denied_by string and exercises position_risk_reduction
    # fail-safe: config=None must return the full position qty with "missing_policy_reduce_only".
    state = PortfolioRiskState.example(reconciled_position=False)
    entry_denial = evaluate_risk(
        AuthorizedSetup.example(), state, None, ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert entry_denial.approved is False
    assert entry_denial.denied_by == "missing_policy"

    position = type("Position", (), {
        "quantity": Decimal("3"), "stressed_loss_per_unit": Decimal("5"),
    })()
    reduction = position_risk_reduction(position, state, config=None)
    assert reduction.reduction_qty == Decimal("3")  # returns full quantity as fail-safe
    assert "missing_policy_reduce_only" in reduction.reasons


def test_req044_position_risk_reduction_invalid_stress_returns_full_qty_as_failsafe():
    # stressed_loss_per_unit <= 0 with config present → "invalid_position_stress_reduce_only".
    # This covers the second fail-safe branch in position_risk_reduction (line 207 in source).
    # open_stressed_loss_usd=15 > allowed=10 so excess > 0 (enters the branch), then
    # stressed_loss_per_unit=0 triggers the guard before division occurs.
    state = PortfolioRiskState.example(
        equity_usd=Decimal("1000"), daily_anchor_usd=Decimal("1000"),
        open_stressed_loss_usd=Decimal("15"),
    )
    position = type("Position", (), {
        "quantity": Decimal("4"), "stressed_loss_per_unit": Decimal("0"),  # invalid
    })()
    reduction = position_risk_reduction(position, state, RiskConfig.defaults())
    assert reduction.reduction_qty == Decimal("4")  # full qty returned as fail-safe
    assert "invalid_position_stress_reduce_only" in reduction.reasons
