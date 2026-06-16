from magic_agent.models import (
    Action, Side, Candle, ContextSnapshot, Setup, Outcome,
)
from magic_agent.executor import PaperExecutor
from magic_agent.context import CmcContextAdapter
from magic_agent.runner import check_stops, on_candle
from magic_agent.policy import PolicyConfig


def _setup(direction=Side.LONG, rating="A"):
    return Setup("BNB/USDT", direction, rating, "risk_on", 600.0, 588.0, 636.0, "chained_scob")


def test_check_stops_long_sl_and_tp():
    pos_long = PaperExecutor().open_position  # not used; build position via executor below
    ex = PaperExecutor(1000.0)
    from magic_agent.models import ExecutionIntent
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0))
    assert check_stops(ex.get_position(), Candle(600, 600, 588, 590)) is True   # low hit SL
    assert check_stops(ex.get_position(), Candle(600, 636, 599, 620)) is True   # high hit TP
    assert check_stops(ex.get_position(), Candle(600, 610, 595, 605)) is False  # neither


def test_on_candle_opens_on_allowed_setup():
    ex = PaperExecutor(1000.0)
    decision, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
    )
    assert outcome is Outcome.OPENED
    assert ex.get_position().side is Side.LONG


def test_on_candle_stops_first_closes_before_new_entry():
    ex = PaperExecutor(1000.0)
    from magic_agent.models import ExecutionIntent
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0))
    # candle hits SL -> must CLOSE, not consult setup_fn
    called = {"n": 0}
    def setup_fn():
        called["n"] += 1
        return _setup()
    decision, outcome = on_candle(ex, setup_fn=setup_fn, context=CmcContextAdapter(None),
                                  candle=Candle(600, 600, 580, 585))
    assert outcome is Outcome.CLOSED and called["n"] == 0
    assert ex.get_position().side is Side.FLAT


def test_on_candle_veto_does_not_open():
    ex = PaperExecutor(1000.0)
    _, outcome = on_candle(ex, setup_fn=lambda: _setup(rating="C"),
                           context=CmcContextAdapter(None), candle=Candle(600, 601, 599, 600))
    assert outcome is Outcome.SKIPPED_VETO and ex.get_position().side is Side.FLAT


def test_on_candle_no_setup_is_noop():
    ex = PaperExecutor(1000.0)
    _, outcome = on_candle(ex, setup_fn=lambda: None,
                           context=CmcContextAdapter(None), candle=Candle(600, 601, 599, 600))
    assert outcome is Outcome.NOOP


def test_policy_denial_prevents_open(monkeypatch):
    ex = PaperExecutor(1000.0)
    # max_concurrent=1 but force a state with an open position => deny path.
    # Simplest: a daily-loss kill-switch already tripped.
    cfg = PolicyConfig(max_daily_loss=50.0)
    _, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
        policy_config=cfg, realized_pnl_today=-100.0,  # kill-switch tripped
    )
    assert outcome is Outcome.SKIPPED_POLICY
    assert ex.get_position().side is Side.FLAT  # NEVER reached the executor


def test_on_candle_advisor_take_halves_qty_and_logs_baseline():
    from magic_agent.decision import LlmAdvice

    # baseline qty (deterministic, no advisor)
    ex0 = PaperExecutor(1000.0)
    base_decision, base_outcome = on_candle(
        ex0, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
    )
    assert base_outcome is Outcome.OPENED
    baseline_qty = ex0.get_position().size

    records: list[dict] = []
    ex = PaperExecutor(1000.0)
    decision, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
        advisor=lambda setup, ctx: LlmAdvice("take", size_factor=0.5),
        log=records.append,
    )
    assert outcome is Outcome.OPENED
    # opened qty is HALF the deterministic baseline
    assert ex.get_position().size == baseline_qty * 0.5
    assert len(records) == 1
    rec = records[0]
    assert rec["baseline_qty"] == baseline_qty       # pre-clamp deterministic size
    assert rec["llm_size_factor"] == 0.5
    assert rec["llm_action_hint"] == "take"
    assert rec["qty"] == baseline_qty * 0.5           # clamped final size


def test_on_candle_advisor_wait_does_not_open():
    from magic_agent.decision import LlmAdvice

    records: list[dict] = []
    ex = PaperExecutor(1000.0)
    decision, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
        advisor=lambda setup, ctx: LlmAdvice("wait", size_factor=1.0),
        log=records.append,
    )
    assert ex.get_position().side is Side.FLAT  # no position opened
    assert outcome is not Outcome.OPENED
    assert decision.action is Action.HOLD
    # the advisor's recommendation is still auditable in the log
    assert len(records) == 1
    assert records[0]["llm_action_hint"] == "wait"


def test_on_candle_advisor_none_matches_deterministic_baseline():
    records: list[dict] = []
    ex = PaperExecutor(1000.0)
    _, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
        log=records.append,
    )
    assert outcome is Outcome.OPENED
    # no advisor configured -> baseline/llm fields are None
    assert records[0]["baseline_qty"] is None
    assert records[0]["llm_size_factor"] is None
    assert records[0]["llm_action_hint"] is None


def test_on_candle_advisor_abstains_keeps_deterministic_qty_and_logs_baseline():
    # An advisor that is WIRED but returns None (abstains) must not alter the
    # deterministic decision: baseline_qty is still captured (advisor present),
    # but llm_size_factor/llm_action_hint stay None and the open proceeds at full size.
    ex0 = PaperExecutor(1000.0)
    on_candle(ex0, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
              candle=Candle(600, 601, 599, 600))
    baseline_qty = ex0.get_position().size

    records: list[dict] = []
    ex = PaperExecutor(1000.0)
    _, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
        advisor=lambda setup, ctx: None,  # advisor abstains
        log=records.append,
    )
    assert outcome is Outcome.OPENED
    assert ex.get_position().size == baseline_qty   # full deterministic size, unclamped
    assert records[0]["baseline_qty"] == baseline_qty
    assert records[0]["llm_size_factor"] is None
    assert records[0]["llm_action_hint"] is None
