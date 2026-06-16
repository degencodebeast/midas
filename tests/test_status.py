"""Tests for magic_agent.status.build_status (Task L1 — TDD)."""
from __future__ import annotations

import json

import pytest

from magic_agent.executor import PaperExecutor
from magic_agent.models import Action, ExecutionIntent, Side
from magic_agent.status import build_status


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_intent(
    *,
    symbol: str = "BNB/USDT",
    action: Action = Action.ENTER_LONG,
    qty: float = 1.0,
    entry: float = 300.0,
    stop_loss: float = 285.0,
    take_profit: float = 330.0,
    leverage: float = 1.0,
) -> ExecutionIntent:
    return ExecutionIntent(
        symbol=symbol,
        action=action,
        qty=qty,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        leverage=leverage,
    )


# ---------------------------------------------------------------------------
# flat executor tests
# ---------------------------------------------------------------------------

class TestFlatExecutor:
    def test_positions_empty_when_flat(self):
        executor = PaperExecutor(starting_equity=1000.0)
        result = build_status(
            executor,
            symbol="BNB/USDT",
            mode="paper",
            venue="binance",
            mark_price=300.0,
            starting_equity=1000.0,
            halted=False,
        )
        assert result["positions"] == []

    def test_halted_reflects_arg_false(self):
        executor = PaperExecutor()
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=1000.0, halted=False)
        assert result["halted"] is False

    def test_halted_reflects_arg_true(self):
        executor = PaperExecutor()
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=1000.0, halted=True)
        assert result["halted"] is True

    def test_equity_from_account(self):
        executor = PaperExecutor(starting_equity=2500.0)
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=2500.0)
        assert result["equity"] == pytest.approx(2500.0)

    def test_available_from_account(self):
        executor = PaperExecutor(starting_equity=1500.0)
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=1500.0)
        assert result["available"] == pytest.approx(1500.0)

    def test_mode_and_venue_propagated(self):
        executor = PaperExecutor()
        result = build_status(executor, symbol="BNB/USDT", mode="live", venue="bybit", mark_price=300.0, starting_equity=1000.0)
        assert result["mode"] == "live"
        assert result["venue"] == "bybit"

    def test_currency_present(self):
        executor = PaperExecutor()
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=1000.0)
        assert "currency" in result
        assert result["currency"] == "USDT"

    def test_realized_and_open_pnl_zero_when_flat(self):
        executor = PaperExecutor(starting_equity=1000.0)
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=1000.0)
        assert result["realized_pnl"] == pytest.approx(0.0)
        assert result["open_pnl"] == pytest.approx(0.0)
        assert result["positions"] == []


# ---------------------------------------------------------------------------
# open position tests
# ---------------------------------------------------------------------------

class TestOpenLongPosition:
    def setup_method(self):
        self.executor = PaperExecutor(starting_equity=1000.0)
        intent = _make_intent(
            action=Action.ENTER_LONG,
            qty=2.5,
            entry=300.0,
            stop_loss=285.0,
            take_profit=330.0,
        )
        self.executor.open_position(intent)

    def test_positions_has_one_entry(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        assert len(result["positions"]) == 1

    def test_position_side_is_string_long(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        # Must be a plain string, not a Side enum, so JSON-serializable
        assert pos["side"] == "long"
        assert isinstance(pos["side"], str)

    def test_position_size(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["size"] == pytest.approx(2.5)

    def test_position_entry_price(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["entry_price"] == pytest.approx(300.0)

    def test_position_stop_loss(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["stop_loss"] == pytest.approx(285.0)

    def test_position_take_profit(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["take_profit"] == pytest.approx(330.0)

    def test_equity_reflects_unrealized_pnl(self):
        # mark_price=310, entry=300, size=2.5, long → unrealized = 2.5 * 10 = 25.0
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        assert result["equity"] == pytest.approx(1025.0)

    def test_position_symbol_is_traded_symbol(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["symbol"] == "BNB/USDT"

    def test_position_qty_equals_size(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["qty"] == pytest.approx(2.5)

    def test_position_pnl_long_after_price_move(self):
        # mark=310, entry=300, size=2.5, long → pnl = +1 * (310-300) * 2.5 = 25.0
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["pnl"] == pytest.approx(25.0)

    def test_top_level_open_pnl_equals_position_unrealized(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        assert result["open_pnl"] == pytest.approx(25.0)
        assert result["open_pnl"] == pytest.approx(result["positions"][0]["pnl"])

    def test_top_level_realized_pnl_is_available_minus_starting(self):
        # available is realized cash = 1000.0; starting_equity = 1000.0 → realized_pnl = 0.0
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=310.0, starting_equity=1000.0)
        assert result["realized_pnl"] == pytest.approx(0.0)


class TestOpenShortPosition:
    def setup_method(self):
        self.executor = PaperExecutor(starting_equity=1000.0)
        intent = _make_intent(
            action=Action.ENTER_SHORT,
            qty=1.0,
            entry=300.0,
            stop_loss=315.0,
            take_profit=270.0,
        )
        self.executor.open_position(intent)

    def test_position_side_is_string_short(self):
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=290.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["side"] == "short"
        assert isinstance(pos["side"], str)

    def test_position_pnl_short_after_price_move(self):
        # mark=290, entry=300, size=1.0, short → pnl = -1 * (290-300) * 1.0 = +10.0
        result = build_status(self.executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=290.0, starting_equity=1000.0)
        pos = result["positions"][0]
        assert pos["pnl"] == pytest.approx(10.0)
        assert result["open_pnl"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# JSON-serializable test
# ---------------------------------------------------------------------------

class TestJsonSerializable:
    def test_flat_dict_is_json_dumps_able(self):
        executor = PaperExecutor()
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=1000.0)
        # Should not raise
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed["positions"] == []
        assert parsed["mode"] == "paper"

    def test_open_position_dict_is_json_dumps_able(self):
        executor = PaperExecutor(starting_equity=1000.0)
        intent = _make_intent(action=Action.ENTER_LONG, qty=1.0, entry=300.0)
        executor.open_position(intent)
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=305.0, starting_equity=1000.0)
        # Should not raise; side must be string not enum
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed["positions"][0]["side"] == "long"

    def test_no_enum_objects_in_result(self):
        """Verify that no value in the result (including nested) is a Side enum."""
        executor = PaperExecutor()
        intent = _make_intent(action=Action.ENTER_LONG, qty=1.0, entry=300.0)
        executor.open_position(intent)
        result = build_status(executor, symbol="BNB/USDT", mode="paper", venue="binance", mark_price=300.0, starting_equity=1000.0)

        def _check_no_enum(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    _check_no_enum(v)
            elif isinstance(obj, list):
                for item in obj:
                    _check_no_enum(item)
            else:
                assert not isinstance(obj, Side), f"Found Side enum in result: {obj!r}"

        _check_no_enum(result)
