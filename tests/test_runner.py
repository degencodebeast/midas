from magic_agent.models import (
    Action, Side, Candle, ContextSnapshot, Setup, Outcome,
)
from magic_agent.executor import PaperExecutor
from magic_agent.context import CmcContextAdapter
from magic_agent.runner import check_stops, on_candle


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
