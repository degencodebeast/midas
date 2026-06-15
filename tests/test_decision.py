from magic_agent.models import (
    Action, Side, ContextSnapshot, Setup, AccountState,
)
from magic_agent.decision import gate, size, build_decision, RISK_PCT_DEFAULT


def _setup(direction=Side.LONG, rating="A", regime="risk_on"):
    return Setup(symbol="BNB/USDT", direction=direction, rating=rating, regime=regime,
                 entry=600.0, stop_loss=588.0, take_profit=636.0,
                 confirmation_kind="chained_scob")


def test_c_grade_is_vetoed():
    v = gate(_setup(rating="C"), ContextSnapshot("neutral", "low"))
    assert v.allow is False


def test_high_risk_flag_vetoes_even_a_grade():
    v = gate(_setup(rating="A"), ContextSnapshot("risk_on", "high"))
    assert v.allow is False


def test_long_in_risk_off_is_halved_not_vetoed_for_a_grade():
    v = gate(_setup(direction=Side.LONG, rating="A", regime="x"),
             ContextSnapshot("risk_off", "low"))
    assert v.allow is True and v.size_multiplier == 0.5


def test_b_grade_against_regime_is_vetoed():
    v = gate(_setup(direction=Side.SHORT, rating="B"),
             ContextSnapshot("risk_on", "low"))  # short vs risk_on = against
    assert v.allow is False


def test_full_a_family_is_tradeable():
    for r in ("A++", "A+", "A", "A-"):
        assert gate(_setup(rating=r), ContextSnapshot("risk_on", "low")).allow is True


def test_full_b_family_is_tradeable_when_aligned():
    for r in ("B+", "B", "B-"):
        assert gate(_setup(rating=r), ContextSnapshot("neutral", "low")).allow is True


def test_below_threshold_ratings_are_vetoed():
    for r in ("C", "N/A", "—"):
        assert gate(_setup(rating=r), ContextSnapshot("risk_on", "low")).allow is False


def test_unavailable_context_lets_setup_stand_at_full_size():
    v = gate(_setup(rating="A"), ContextSnapshot("neutral", "low", status="unavailable"))
    assert v.allow is True and v.size_multiplier == 1.0


def test_size_is_risk_over_stop_distance():
    # 1% of 1000 = 10 risk; |600-588| = 12 -> 0.8333 units
    qty = size(equity=1000.0, risk_pct=0.01, entry=600.0, stop_loss=588.0)
    assert abs(qty - (10.0 / 12.0)) < 1e-9


def test_zero_stop_distance_returns_zero_size():
    assert size(equity=1000.0, risk_pct=0.01, entry=600.0, stop_loss=600.0) == 0.0


def test_build_decision_enter_long_carries_scaled_intent():
    acct = AccountState(equity=1000.0, available=1000.0)
    d = build_decision(_setup(rating="A", regime="risk_on"),
                       ContextSnapshot("risk_on", "low"), acct)
    assert d.action is Action.ENTER_LONG
    assert d.intent is not None and d.intent.action is Action.ENTER_LONG
    # full size: 1% risk over 12 distance
    assert abs(d.intent.qty - (10.0 / 12.0)) < 1e-9


def test_build_decision_veto_has_no_intent():
    acct = AccountState(equity=1000.0, available=1000.0)
    d = build_decision(_setup(rating="C"), ContextSnapshot("neutral", "low"), acct)
    assert d.action is Action.HOLD and d.intent is None and d.gate.allow is False


from magic_agent.models import Action
from magic_agent.decision import LlmAdvice, clamp_advice


def _allowed():
    acct = AccountState(equity=1000.0, available=1000.0)
    return build_decision(_setup(rating="A", regime="risk_on"),
                          ContextSnapshot("risk_on", "low"), acct)


def test_clamp_size_factor_reduces():
    base = _allowed()
    out = clamp_advice(base, LlmAdvice(action_hint="take", size_factor=0.5))
    assert out.intent.qty == base.intent.qty * 0.5


def test_clamp_factor_above_one_is_capped_to_baseline():
    base = _allowed()
    out = clamp_advice(base, LlmAdvice(action_hint="take", size_factor=5.0))
    assert out.intent.qty == base.intent.qty  # never increases


def test_clamp_negative_factor_floors_to_hold():
    out = clamp_advice(_allowed(), LlmAdvice(action_hint="take", size_factor=-1.0))
    assert out.action is Action.HOLD and out.intent is None


def test_clamp_wait_forces_hold():
    out = clamp_advice(_allowed(), LlmAdvice(action_hint="wait", size_factor=1.0))
    assert out.action is Action.HOLD and out.intent is None


def test_clamp_cannot_unveto_a_vetoed_baseline():
    acct = AccountState(equity=1000.0, available=1000.0)
    vetoed = build_decision(_setup(rating="C"), ContextSnapshot("neutral", "low"), acct)
    out = clamp_advice(vetoed, LlmAdvice(action_hint="take", size_factor=1.0))
    assert out.action is Action.HOLD and out.intent is None  # stays vetoed
