# tests/test_executor.py
from magic_agent.models import (
    Action, Side, Outcome, ExecutionIntent, PositionState,
)
from magic_agent.executor import PaperExecutor, PerpExecutor


def _intent(action, qty=2.0, entry=600.0, sl=588.0, tp=636.0):
    return ExecutionIntent(symbol="BNB/USDT", action=action, qty=qty,
                           entry=entry, stop_loss=sl, take_profit=tp, leverage=1.0)


def test_paper_executor_satisfies_protocol():
    assert isinstance(PaperExecutor(starting_equity=1000.0), PerpExecutor)


def test_open_long_sets_position_and_reduces_available():
    ex = PaperExecutor(starting_equity=1000.0)
    out = ex.open_position(_intent(Action.ENTER_LONG, qty=1.0, entry=600.0))
    assert out is Outcome.OPENED
    pos = ex.get_position()
    assert pos.side is Side.LONG and pos.size == 1.0 and pos.entry_price == 600.0


def test_open_short_then_close_realizes_pnl():
    ex = PaperExecutor(starting_equity=1000.0)
    ex.open_position(_intent(Action.ENTER_SHORT, qty=1.0, entry=600.0))
    out = ex.close_position(mark_price=580.0)  # short profits when price falls
    assert out is Outcome.CLOSED
    assert ex.get_position().side is Side.FLAT
    assert ex.get_account(mark_price=580.0).equity == 1020.0  # +20


def test_open_when_already_in_position_is_skipped():
    ex = PaperExecutor(starting_equity=1000.0)
    ex.open_position(_intent(Action.ENTER_LONG, qty=1.0))
    out = ex.open_position(_intent(Action.ENTER_LONG, qty=1.0))
    assert out is Outcome.SKIPPED_IN_POSITION


def test_zero_qty_is_skipped():
    ex = PaperExecutor(starting_equity=1000.0)
    assert ex.open_position(_intent(Action.ENTER_LONG, qty=0.0)) is Outcome.SKIPPED_ZERO_SIZE
