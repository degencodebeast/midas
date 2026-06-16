# tests/test_setup_view.py
import pytest
from magic_agent.models import Side
from magic_agent.setup_view import from_scan_result


class _Inputs:
    def __init__(self, direction):
        self.trade_direction = direction


class _Result:
    def __init__(self, rating, regime="risk_on"):
        self.rating = rating
        self.regime = regime


class _Entry:
    def __init__(self, entry, qml_key_level, confirmation_kind="chained_scob"):
        self.entry = entry
        self.qml_key_level = qml_key_level
        self.confirmation_kind = confirmation_kind


class _Scan:
    def __init__(self, symbol, direction, rating, entry, qml, regime="risk_on"):
        self.symbol = symbol
        self.inputs = _Inputs(direction)
        self.result = _Result(rating, regime)
        self.entry = _Entry(entry, qml) if entry is not None else None
        self.confirmation_kind = "chained_scob"


def test_long_setup_stop_below_qml_and_tp_by_rr():
    sc = _Scan("BNB/USDT", "Long", "A", entry=600.0, qml=594.0)
    s = from_scan_result(sc, stop_buffer_pct=0.005, min_rr=3.0)
    assert s is not None
    assert s.direction is Side.LONG
    assert s.stop_loss == pytest.approx(594.0 * (1 - 0.005))  # below QML level
    risk = s.entry - s.stop_loss
    assert s.take_profit == pytest.approx(s.entry + 3.0 * risk)


def test_short_setup_stop_above_qml():
    sc = _Scan("BNB/USDT", "Short", "A", entry=600.0, qml=606.0)
    s = from_scan_result(sc, stop_buffer_pct=0.005, min_rr=3.0)
    assert s.direction is Side.SHORT
    assert s.stop_loss == pytest.approx(606.0 * (1 + 0.005))  # above QML level
    risk = s.stop_loss - s.entry
    assert s.take_profit == pytest.approx(s.entry - 3.0 * risk)


def test_no_entry_or_no_direction_returns_none():
    assert from_scan_result(_Scan("X", "—", "A", entry=600.0, qml=594.0)) is None
    assert from_scan_result(_Scan("X", "Long", "A", entry=None, qml=None)) is None
