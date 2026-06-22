from decimal import Decimal

from magic_agent.risk_policy import (
    MarketRiskContext, PortfolioRiskState, QuantityCaps, RiskConfig,
    evaluate_risk, position_risk_reduction,
)
from magic_agent.runtime_state import RuntimeState
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


# FIXED-MARGIN small-account live profile (~$20 equity / ~$10 deployable USDC) -------
# These are the operator-facing proof that the position size is a fixed % of EQUITY
# (the deployed notional), NOT derived from the scanner stop: an A-grade trade deploys
# ~5% of equity (~$1.00) regardless of the stop distance, with the scanner stop used
# only for risk tracking + the safety ceilings (actual risk = deploy x stop%, small).


def _small_account_state():
    return RuntimeState.new_live_session(
        equity_usd=Decimal("20"), cash_usd=Decimal("10"),
    ).risk_state()


def PortfolioRiskState_with(state, **changes):  # tiny helper for readable concurrency cases
    return PortfolioRiskState(**{**state.__dict__, **changes})


def test_a_grade_deploys_five_percent_of_equity_regardless_of_stop_distance():
    # A-grade aligned, normal band. Deploy ~5% of $20 = ~$1.00 of NOTIONAL at BOTH a
    # 5% stop AND a 12% stop -> proves MARGIN (not risk) sizes the position.
    state = _small_account_state()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    config = RiskConfig.defaults()

    tight = evaluate_risk(
        AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("95"),
                                campaign_dol=Decimal("120")),
        state, config, ActionPurpose.STRATEGY, caps, market,
    )
    wide = evaluate_risk(
        AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("88"),
                                campaign_dol=Decimal("120")),
        state, config, ActionPurpose.STRATEGY, caps, market,
    )

    assert tight.approved is True, tight.denied_by
    assert wide.approved is True, wide.denied_by
    tight_notional = tight.final_qty * Decimal("100")
    wide_notional = wide.final_qty * Decimal("100")
    # Both deploy ~$1.00 (5% of $20) — the stop distance does NOT change the size.
    assert abs(tight_notional - Decimal("1.00")) < Decimal("0.01"), tight_notional
    assert abs(wide_notional - Decimal("1.00")) < Decimal("0.01"), wide_notional
    assert tight.final_qty == wide.final_qty
    # final_qty = deploy_notional / entry.
    assert tight.final_qty == Decimal("1.00") / Decimal("100")
    # The margin binds (the deploy is the full base size; cash cap ~$9.70 doesn't bind).
    assert tight.final_qty == tight.base_qty


def test_risk_is_an_output_equal_to_deploy_times_stop_percent():
    # $1 deploy with an 8% stop -> stressed_loss ~= $0.08; with a 12% stop -> ~$0.12.
    state = _small_account_state()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    config = RiskConfig.defaults()

    eight = evaluate_risk(
        AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("92"),
                                campaign_dol=Decimal("120")),
        state, config, ActionPurpose.STRATEGY, caps, market,
    )
    twelve = evaluate_risk(
        AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("88"),
                                campaign_dol=Decimal("120")),
        state, config, ActionPurpose.STRATEGY, caps, market,
    )

    risk_eight = eight.final_qty * (Decimal("100") - Decimal("92"))
    risk_twelve = twelve.final_qty * (Decimal("100") - Decimal("88"))
    assert abs(risk_eight - Decimal("0.08")) < Decimal("0.005"), risk_eight
    assert abs(risk_twelve - Decimal("0.12")) < Decimal("0.005"), risk_twelve


def test_b_grade_deploys_two_and_a_half_percent_of_equity():
    state = _small_account_state()
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="B", entry=Decimal("100"), structural_stop=Decimal("95"),
                                campaign_dol=Decimal("120")),
        state, RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is True, decision.denied_by
    notional = decision.final_qty * Decimal("100")
    assert abs(notional - Decimal("0.50")) < Decimal("0.01"), notional  # 2.5% of $20
    assert decision.risk_fraction == Decimal("0.025")


def test_counter_bias_halves_the_margin():
    # A-grade counter-bias -> 5% * 0.5 = 2.5% of $20 = ~$0.50.
    state = _small_account_state()
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="A", bias_alignment="counter_bias",
                                entry=Decimal("100"), structural_stop=Decimal("95"),
                                campaign_dol=Decimal("120")),
        state, RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.counter_bias_qualified(),
    )
    assert decision.approved is True, decision.denied_by
    notional = decision.final_qty * Decimal("100")
    assert abs(notional - Decimal("0.50")) < Decimal("0.01"), notional
    assert decision.risk_fraction == Decimal("0.025")


def test_canary_first_trade_sizes_at_grade_margin():
    # Under fixed-margin there is no scale-up: the canary sizes at the grade margin
    # (canary_margin_fraction defaults to the A-grade 5%). Same ~$1.00 deploy.
    state = _small_account_state()
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("95"),
                                campaign_dol=Decimal("120")),
        state, RiskConfig.defaults(), ActionPurpose.STRATEGY, QuantityCaps.unbounded(),
        MarketRiskContext(Decimal("0"), Decimal("1"), Decimal("1"), canary=True),
    )
    assert decision.approved is True, decision.denied_by
    assert decision.risk_fraction == Decimal("0.05")
    notional = decision.final_qty * Decimal("100")
    assert abs(notional - Decimal("1.00")) < Decimal("0.01"), notional


def test_three_a_grade_positions_deploy_fifteen_percent_and_fourth_is_denied():
    # Max 3 concurrent -> max deployed ~3x5% = 15% of equity. The 4th concurrent
    # A-grade must be DENIED by the concurrency cap.
    state = _small_account_state()
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    setup = AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("95"),
                                    campaign_dol=Decimal("120"))

    per_trade_notional = Decimal("0")
    for n in range(3):
        d = evaluate_risk(setup, PortfolioRiskState_with(state, open_strategy_positions=n),
                          config, ActionPurpose.STRATEGY, caps, market)
        assert d.approved is True, (n, d.denied_by)
        per_trade_notional = d.final_qty * Decimal("100")
    total_deployed = per_trade_notional * 3
    assert abs(total_deployed - (Decimal("20") * Decimal("0.15"))) < Decimal("0.03"), total_deployed

    fourth = evaluate_risk(
        setup, PortfolioRiskState_with(state, open_strategy_positions=3),
        config, ActionPurpose.STRATEGY, caps, market,
    )
    assert fourth.approved is False
    assert fourth.denied_by == "concurrency_cap"


# Graduated drawdown ladder ----------------------------------------------------------


def _dd_state(drawdown_pct, equity=Decimal("20"), cash=Decimal("10"), **changes):
    # peak fixed at ``equity``; lower the live equity to realize the target drawdown.
    peak = equity
    live = peak * (Decimal("1") - drawdown_pct)
    base = RuntimeState.new_live_session(equity_usd=peak, cash_usd=cash).risk_state()
    return PortfolioRiskState(**{
        **base.__dict__, "equity_usd": live, "peak_equity_usd": peak,
        "daily_anchor_usd": live, **changes,
    })


def _a_setup():
    return AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("95"),
                                   campaign_dol=Decimal("120"))


def _b_setup():
    return AuthorizedSetup.example(grade="B", entry=Decimal("100"), structural_stop=Decimal("95"),
                                   campaign_dol=Decimal("120"))


def test_throttle_band_halves_margin_and_caps_at_two_positions():
    # 7% drawdown -> throttle band: A deploys ~2.5% (0.5x of 5%), max 2 positions.
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    state = _dd_state(Decimal("0.07"))

    d = evaluate_risk(_a_setup(), state, config, ActionPurpose.STRATEGY, caps, market)
    assert d.approved is True, d.denied_by
    assert d.risk_fraction == Decimal("0.025")  # 5% * 0.5
    notional = d.final_qty * Decimal("100")
    assert abs(notional - state.equity_usd * Decimal("0.025")) < Decimal("0.01"), notional

    # A 3rd concurrent position in the throttle band is denied (band max = 2).
    third = evaluate_risk(_a_setup(), PortfolioRiskState_with(state, open_strategy_positions=2),
                          config, ActionPurpose.STRATEGY, caps, market)
    assert third.denied_by == "concurrency_cap"
    # The 2nd is still allowed.
    second = evaluate_risk(_a_setup(), PortfolioRiskState_with(state, open_strategy_positions=1),
                           config, ActionPurpose.STRATEGY, caps, market)
    assert second.approved is True, second.denied_by


def test_defense_band_quarters_margin_is_a_only_and_caps_at_one_position():
    # 12% drawdown -> defense band: A deploys ~1.25% (0.25x of 5%), B DENIED, max 1.
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    state = _dd_state(Decimal("0.12"))

    a = evaluate_risk(_a_setup(), state, config, ActionPurpose.STRATEGY, caps, market)
    assert a.approved is True, a.denied_by
    assert a.risk_fraction == Decimal("0.0125")  # 5% * 0.25
    notional = a.final_qty * Decimal("100")
    assert abs(notional - state.equity_usd * Decimal("0.0125")) < Decimal("0.01"), notional

    b = evaluate_risk(_b_setup(), state, config, ActionPurpose.STRATEGY, caps, market)
    assert b.approved is False
    assert b.denied_by == "drawdown_defense_a_only"

    # A 2nd concurrent position in the defense band is denied (band max = 1).
    second = evaluate_risk(_a_setup(), PortfolioRiskState_with(state, open_strategy_positions=1),
                           config, ActionPurpose.STRATEGY, caps, market)
    assert second.denied_by == "concurrency_cap"


def test_entry_halt_band_denies_new_entries_at_sixteen_percent():
    config = RiskConfig.defaults()
    state = _dd_state(Decimal("0.16"))
    d = evaluate_risk(_a_setup(), state, config, ActionPurpose.STRATEGY,
                      QuantityCaps.unbounded(), MarketRiskContext.aligned())
    assert d.approved is False
    assert d.denied_by == "drawdown_entry_halt"


def test_hard_review_band_denies_new_entries_at_twenty_two_percent():
    config = RiskConfig.defaults()
    state = _dd_state(Decimal("0.22"))
    d = evaluate_risk(_a_setup(), state, config, ActionPurpose.STRATEGY,
                      QuantityCaps.unbounded(), MarketRiskContext.aligned())
    assert d.approved is False
    assert d.denied_by == "drawdown_hard_review"


def test_hard_dq_band_denies_new_entries_at_thirty_one_percent():
    config = RiskConfig.defaults()
    state = _dd_state(Decimal("0.31"))
    d = evaluate_risk(_a_setup(), state, config, ActionPurpose.STRATEGY,
                      QuantityCaps.unbounded(), MarketRiskContext.aligned())
    assert d.approved is False
    assert d.denied_by == "hard_drawdown_dq"


def test_five_percent_drawdown_is_a_throttle_not_a_halt():
    # REGRESSION (supersedes d28f2f4): 5% drawdown no longer HALTS entries — it is the
    # bottom of the THROTTLE band. An A-grade trade must still be APPROVED (de-rated).
    config = RiskConfig.defaults()
    state = _dd_state(Decimal("0.05"))
    d = evaluate_risk(_a_setup(), state, config, ActionPurpose.STRATEGY,
                      QuantityCaps.unbounded(), MarketRiskContext.aligned())
    assert d.approved is True, d.denied_by
    assert d.risk_fraction == Decimal("0.025")


def test_protective_exits_still_run_while_entries_are_halted():
    # At 16%+ drawdown new entries are HALTED, but the RISK_EXIT path must still
    # approve (exit a reconciled position) and position_risk_reduction must still
    # return a reduction — protective de-risking is never blocked by the entry halt.
    config = RiskConfig.defaults()
    state = _dd_state(Decimal("0.16"), open_stressed_loss_usd=Decimal("65"),
                      reconciled_position=True)

    exit_decision = evaluate_risk(
        _a_setup(), state, config, ActionPurpose.RISK_EXIT,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert exit_decision.approved is True

    # Drive a reduction: large equity/anchor so allowed = open_risk_room binds and the
    # seeded open_stressed_loss exceeds it.
    reduce_state = PortfolioRiskState(
        equity_usd=Decimal("1000"), cash_usd=Decimal("1000"),
        peak_equity_usd=Decimal("1200"),  # 16.7% drawdown -> entries halted
        daily_anchor_usd=Decimal("1000"), open_stressed_loss_usd=Decimal("65"),
    )
    position = type("Position", (), {
        "quantity": Decimal("2"), "stressed_loss_per_unit": Decimal("10"),
    })()
    reduction = position_risk_reduction(position, reduce_state, config)
    assert reduction.reduction_qty > 0
    assert reduction.reasons == ("open_or_daily_risk_exceeds_budget",)


def test_wide_stop_still_trimmed_or_denied_by_open_risk_cap():
    # The scanner stop is exit-only for SIZING, but a pathologically WIDE stop still
    # pushes the stressed_loss high enough that open_risk_cap trims/denies. Use a large
    # account so the margin deploy is meaningful and a ~40% stop dominates.
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    # equity 1000: A-grade deploy = 5% = $50 notional = 0.5 qty at entry 100. With a
    # 40% stop, stressed_loss = 0.5 * 40 = 20 < open_risk_cap (1000*0.06 = 60). Seed
    # the open book near the ceiling so the wide-stop position tips it over.
    state = PortfolioRiskState(
        equity_usd=Decimal("1000"), cash_usd=Decimal("1000"),
        peak_equity_usd=Decimal("1000"), daily_anchor_usd=Decimal("1000"),
        open_stressed_loss_usd=Decimal("45"),
    )
    wide = AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("60"),
                                   campaign_dol=Decimal("120"))
    d = evaluate_risk(wide, state, config, ActionPurpose.STRATEGY, caps, market)
    assert d.approved is False
    assert d.denied_by == "open_risk_cap"

    # A NARROW stop on the same state passes (same margin deploy, small stressed_loss).
    narrow = AuthorizedSetup.example(grade="A", entry=Decimal("100"), structural_stop=Decimal("99"),
                                     campaign_dol=Decimal("120"))
    ok = evaluate_risk(narrow, state, config, ActionPurpose.STRATEGY, caps, market)
    assert ok.approved is True, ok.denied_by


def test_missing_config_denies_entry_but_allows_reconciled_exit():
    state = PortfolioRiskState.example(reconciled_position=True)
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    assert not evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.STRATEGY, caps, market).approved
    assert evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.RISK_EXIT, caps, market).approved


def test_grade_and_counter_bias_reduce_margin_not_via_stop():
    state = PortfolioRiskState.example(equity_usd=Decimal("1000"))
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    aligned_a = evaluate_risk(AuthorizedSetup.example(grade="A", bias_alignment="aligned"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.aligned())
    counter_a = evaluate_risk(AuthorizedSetup.example(grade="A", bias_alignment="counter_bias"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.counter_bias_qualified())
    counter_b = evaluate_risk(AuthorizedSetup.example(grade="B", bias_alignment="counter_bias"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.counter_bias_qualified())
    assert aligned_a.risk_fraction == Decimal("0.05")
    assert counter_a.risk_fraction == Decimal("0.025")
    assert counter_b.risk_fraction == Decimal("0.0125")
    # Same entry/stop, so half the margin -> half the quantity.
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


def test_real_scanner_counter_bias_promotion_reaches_half_margin(monkeypatch):
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
    assert decision.risk_fraction == Decimal("0.0125")  # B 2.5% * 0.5 counter-bias


def test_consecutive_stop_concurrency_and_stale_equity_gates():
    setup = AuthorizedSetup.example()
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    assert evaluate_risk(setup, PortfolioRiskState.example(consecutive_stops=3), config, ActionPurpose.STRATEGY, caps, market).denied_by == "consecutive_stop_halt"
    # Normal band: concurrency cap is the configured max (3).
    assert evaluate_risk(setup, PortfolioRiskState.example(open_strategy_positions=3), config, ActionPurpose.STRATEGY, caps, market).denied_by == "concurrency_cap"
    assert evaluate_risk(setup, PortfolioRiskState.example(open_strategy_positions=2), config, ActionPurpose.STRATEGY, caps, market).approved is True
    assert evaluate_risk(setup, PortfolioRiskState.example(equity_fresh=False), config, ActionPurpose.STRATEGY, caps, market).denied_by == "stale_equity"


def test_correlation_bucket_caps_all_longs():
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    # equity 1000, A-grade deploy 5% = $50 = 0.5 qty at entry 100; default example stop
    # is entry 100 - stop 90 = 10/unit -> new_stressed_loss = 0.5*10 = 5. open_risk_cap
    # = 1000*0.06 = 60 (passes with open=0). Seed the bucket at 58 so projected_bucket
    # = 58 + 5 = 63 > 60 and ONLY the correlation_bucket_cap fires.
    bucket_full = evaluate_risk(
        AuthorizedSetup.example(grade="A"),
        PortfolioRiskState.example(correlation_bucket_stressed_loss_usd=Decimal("58")),
        config, ActionPurpose.STRATEGY, caps, market,
    )
    assert bucket_full.denied_by == "correlation_bucket_cap"


def test_position_risk_reduction_returns_quantity_needed_to_restore_budget():
    # daily_room = 1000*0.10 = 100; open_risk_room = 1000*0.06 = 60; allowed = 60.
    # open_stressed_loss 65 -> excess = 5 -> reduce_qty = min(2, 5/10) = 0.5.
    state = PortfolioRiskState.example(
        equity_usd=Decimal("1000"), daily_anchor_usd=Decimal("1000"),
        open_stressed_loss_usd=Decimal("65"),
    )
    position = type("Position", (), {
        "quantity": Decimal("2"), "stressed_loss_per_unit": Decimal("10"),
    })()
    decision = position_risk_reduction(position, state, RiskConfig.defaults())
    assert decision.reduction_qty == Decimal("0.5")
    assert decision.reasons == ("open_or_daily_risk_exceeds_budget",)


# Deny-path regression locks --------------------------------------------------------
# Each test isolates exactly one gate so no adjacent gate can mask the target denial.


def test_open_risk_cap_isolated_from_correlation_bucket_cap():
    # open_risk_cap is checked BEFORE correlation_bucket_cap. A-grade deploy 5% of 1000
    # = $50 = 0.5 qty; example stop 100->90 = 10/unit -> new_stressed_loss = 5.
    # correlation_bucket=0 keeps the bucket within cap; open_stressed_loss 56 + 5 = 61
    # > equity*max_open_risk (1000*0.06 = 60) -> open_risk_cap fires alone.
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="A"),
        PortfolioRiskState.example(
            equity_usd=Decimal("1000"),
            open_stressed_loss_usd=Decimal("56"),
            correlation_bucket_stressed_loss_usd=Decimal("0"),
        ),
        RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "open_risk_cap"


def test_daily_loss_cap_denies_when_existing_loss_plus_new_exposure_exceeds_fraction():
    # daily_loss_fraction 0.10. Drive a large intraday loss WITHOUT tripping the
    # drawdown gates (peak=1000, equity=985 -> 1.5% drawdown = normal band).
    # daily_anchor=1100, equity=985 -> daily_loss=115. A-grade deploy 5% of 985 =
    # $49.25 = 0.4925 qty; example stop 10/unit -> stressed_loss ~= 4.925; total ~= 4.925
    # (open=0) under open_risk_cap (985*0.06=59.1). daily_loss(115)+projected(~4.9) ~= 120
    # > daily_anchor*0.10 (110) -> daily_loss_cap.
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="A"),
        PortfolioRiskState.example(
            equity_usd=Decimal("985"),
            daily_anchor_usd=Decimal("1100"),
            open_stressed_loss_usd=Decimal("0"),
            correlation_bucket_stressed_loss_usd=Decimal("0"),
        ),
        RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "daily_loss_cap"


def test_unsupported_grade_outside_a_b_family_is_denied():
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="C"),
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "unsupported_grade"


def test_geometry_nonpositive_loss_when_stop_equals_entry():
    class _FakeSetup:
        grade = "A"
        entry = Decimal("100")
        structural_stop = Decimal("100")  # loss_per_unit = 0 -> nonpositive_loss
        bias_alignment = "aligned"

    decision = evaluate_risk(
        _FakeSetup(),  # type: ignore[arg-type]
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "geometry"
    assert "nonpositive_loss" in decision.reasons


def test_below_minimum_notional_returns_zero_quantity():
    caps = QuantityCaps(
        liquidity_qty=Decimal("Infinity"),
        pool_share_qty=Decimal("Infinity"),
        concentration_qty=Decimal("Infinity"),
        gap_stress_qty=Decimal("Infinity"),
        minimum_notional_usd=Decimal("9999"),  # capped notional ($50) << 9999
    )
    decision = evaluate_risk(
        AuthorizedSetup.example(grade="A"),
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        caps, MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "below_minimum_notional"


def test_zero_safe_quantity_when_macro_clamp_zeros_out_sized_quantity():
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


def test_risk_exit_denied_when_position_not_reconciled():
    decision = evaluate_risk(
        AuthorizedSetup.example(),
        PortfolioRiskState.example(reconciled_position=False),
        RiskConfig.defaults(), ActionPurpose.RISK_EXIT,
        QuantityCaps.unbounded(), MarketRiskContext.aligned(),
    )
    assert decision.approved is False
    assert decision.denied_by == "unreconciled"


def test_missing_policy_denies_strategy_and_reduction_failsafe_returns_full_qty():
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


def test_position_risk_reduction_invalid_stress_returns_full_qty_as_failsafe():
    state = PortfolioRiskState.example(
        equity_usd=Decimal("1000"), daily_anchor_usd=Decimal("1000"),
        open_stressed_loss_usd=Decimal("65"),
    )
    position = type("Position", (), {
        "quantity": Decimal("4"), "stressed_loss_per_unit": Decimal("0"),  # invalid
    })()
    reduction = position_risk_reduction(position, state, RiskConfig.defaults())
    assert reduction.reduction_qty == Decimal("4")  # full qty returned as fail-safe
    assert "invalid_position_stress_reduce_only" in reduction.reasons
