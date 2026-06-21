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
