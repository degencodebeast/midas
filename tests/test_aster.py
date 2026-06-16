# tests/test_aster.py
from magic_agent.models import Action, Side, Outcome, ExecutionIntent
from magic_agent.executor import PerpExecutor
from magic_agent.executors.aster import AsterRestExecutor


class FakeExchange:
    """Minimal ccxt-shaped stub. Records calls; returns canned state."""
    def __init__(self):
        self.orders = []
        self.leverage_set = []
        self._positions = []

    def set_leverage(self, lev, symbol):
        self.leverage_set.append((lev, symbol))

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.orders.append({"symbol": symbol, "type": type_, "side": side,
                            "amount": amount, "params": params or {}})
        self._positions = [{"symbol": symbol, "contracts": amount,
                            "side": "long" if side == "buy" else "short",
                            "entryPrice": 600.0}]
        return {"id": "o1"}

    def fetch_positions(self, symbols=None):
        return self._positions

    def fetch_balance(self, params=None):
        return {"USDT": {"total": 1000.0, "free": 800.0}}


def test_satisfies_protocol():
    assert isinstance(AsterRestExecutor(FakeExchange(), "BNB/USDT"), PerpExecutor)


def test_open_long_sends_buy_with_hedge_position_side():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    out = ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0, leverage=5.0))
    assert out is Outcome.OPENED
    assert fx.leverage_set == [(5.0, "BNB/USDT")]
    o = fx.orders[0]
    assert o["side"] == "buy" and o["amount"] == 1.0
    assert o["params"]["positionSide"] == "LONG"


def test_open_short_sends_sell_with_short_position_side():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_SHORT, 2.0, 600.0, 612.0, 564.0))
    o = fx.orders[0]
    assert o["side"] == "sell" and o["params"]["positionSide"] == "SHORT"


def test_get_position_maps_ccxt_position():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0))
    pos = ex.get_position()
    assert pos.side is Side.LONG and pos.size == 1.0 and pos.entry_price == 600.0


def test_zero_qty_skips_without_order():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    assert ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 0.0, 600.0, 588.0, 636.0)) is Outcome.SKIPPED_ZERO_SIZE
    assert fx.orders == []
