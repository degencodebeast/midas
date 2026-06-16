# tests/test_models.py
import dataclasses
import pytest
from magic_agent.models import (
    Action, Side, Outcome, Candle, ContextSnapshot, Setup,
    GateVerdict, ExecutionIntent, AgentDecision, PositionState, AccountState,
)


def test_enums_have_expected_members():
    assert Action.ENTER_LONG.value == "enter_long"
    assert Action.ENTER_SHORT.value == "enter_short"
    assert {s.value for s in Side} == {"flat", "long", "short"}
    assert Outcome.OPENED.value == "opened"


def test_setup_is_frozen_and_carries_entry_and_stop():
    s = Setup(symbol="BNB/USDT", direction=Side.LONG, rating="A", regime="risk_on",
              entry=600.0, stop_loss=588.0, take_profit=636.0, confirmation_kind="chained_scob")
    assert s.entry == 600.0 and s.stop_loss == 588.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.entry = 1.0  # type: ignore[misc]


def test_position_and_account_defaults():
    p = PositionState()
    assert p.side is Side.FLAT and p.size == 0.0
    a = AccountState(equity=1000.0, available=1000.0)
    assert a.currency == "USDT"
