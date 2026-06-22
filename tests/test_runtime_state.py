"""Tests for the production RuntimeState read by the spot loop.

The spot loop (`runner.run_cycle`) reads EXACTLY four things off ``app.state``:
``blocks_new_exposure`` (bool), ``canary_mode`` (bool), ``risk_state()`` (->
:class:`PortfolioRiskState`), and ``as_dict()`` (handed to ``state_journal.save``).
These tests pin that contract plus byte-identical persistence round-tripping.
"""
from decimal import Decimal

import pytest

from magic_agent.risk_policy import PortfolioRiskState
from magic_agent.runtime_state import RuntimeState


def _populated() -> RuntimeState:
    """A RuntimeState with every tracked field set to a distinct, non-default value."""
    return RuntimeState(
        equity_usd=Decimal("1234.56"),
        cash_usd=Decimal("789.01"),
        peak_equity_usd=Decimal("1500.00"),
        daily_anchor_usd=Decimal("1300.00"),
        open_stressed_loss_usd=Decimal("12.34"),
        correlation_bucket_stressed_loss_usd=Decimal("5.67"),
        open_strategy_positions=2,
        consecutive_stops=1,
        equity_fresh=True,
        reconciled_position=True,
    )


def test_risk_state_returns_portfolio_risk_state_with_tracked_values():
    state = _populated()
    risk = state.risk_state()

    assert isinstance(risk, PortfolioRiskState)
    assert risk.equity_usd == Decimal("1234.56")
    assert risk.cash_usd == Decimal("789.01")
    assert risk.peak_equity_usd == Decimal("1500.00")
    assert risk.daily_anchor_usd == Decimal("1300.00")
    assert risk.open_stressed_loss_usd == Decimal("12.34")
    assert risk.correlation_bucket_stressed_loss_usd == Decimal("5.67")
    assert risk.open_strategy_positions == 2
    assert risk.consecutive_stops == 1
    assert risk.equity_fresh is True
    assert risk.reconciled_position is True


def test_risk_state_money_fields_are_decimal_not_float():
    risk = _populated().risk_state()
    for value in (
        risk.equity_usd,
        risk.cash_usd,
        risk.peak_equity_usd,
        risk.daily_anchor_usd,
        risk.open_stressed_loss_usd,
        risk.correlation_bucket_stressed_loss_usd,
    ):
        assert isinstance(value, Decimal)


def test_canary_mode_defaults_true():
    assert RuntimeState.new_session(Decimal("1000")).canary_mode is True


def test_blocks_new_exposure_defaults_false_and_is_settable():
    state = RuntimeState.new_session(Decimal("1000"))
    assert state.blocks_new_exposure is False
    state.blocks_new_exposure = True
    assert state.blocks_new_exposure is True


def test_new_session_sets_peak_and_anchor_to_equity():
    state = RuntimeState.new_session(Decimal("2500.50"))

    assert state.equity_usd == Decimal("2500.50")
    assert state.peak_equity_usd == Decimal("2500.50")
    assert state.daily_anchor_usd == Decimal("2500.50")
    assert state.cash_usd == Decimal("2500.50")
    assert state.open_strategy_positions == 0
    assert state.consecutive_stops == 0
    assert state.open_stressed_loss_usd == Decimal("0")
    assert state.correlation_bucket_stressed_loss_usd == Decimal("0")
    assert state.equity_fresh is True
    assert state.canary_mode is True
    assert state.blocks_new_exposure is False


def test_new_session_starting_equity_must_be_decimal():
    with pytest.raises((TypeError, ValueError)):
        RuntimeState.new_session(1000.0)


def test_as_dict_is_json_serializable_with_decimals_as_strings():
    import json

    state = _populated()
    state.blocks_new_exposure = True
    state.canary_mode = False
    payload = state.as_dict()

    # Decimals must be encoded as strings so the journal stays exact.
    assert payload["equity_usd"] == "1234.56"
    assert isinstance(payload["equity_usd"], str)
    assert payload["correlation_bucket_stressed_loss_usd"] == "5.67"
    # Round-trips through the stdlib JSON encoder with no custom default.
    assert json.loads(json.dumps(payload)) == payload


def test_as_dict_from_dict_round_trips_exactly():
    original = _populated()
    original.blocks_new_exposure = True
    original.canary_mode = False

    restored = RuntimeState.from_dict(original.as_dict())

    # Every tracked field plus the two flags survive the round trip exactly.
    assert restored.as_dict() == original.as_dict()
    assert restored.risk_state() == original.risk_state()
    assert restored.blocks_new_exposure is True
    assert restored.canary_mode is False


def test_mode_tag_is_persisted_and_round_trips():
    # The "mode" tag lets a consumer detect a cross-mode/contaminated state file (a
    # paper state.json restored into a live session). It is persisted and round-trips.
    paper = RuntimeState.new_session(Decimal("10000"), mode="paper")
    assert paper.mode == "paper"
    assert paper.as_dict()["mode"] == "paper"
    assert RuntimeState.from_dict(paper.as_dict()).mode == "paper"

    live = RuntimeState.new_live_session(Decimal("20"), Decimal("10"), mode="twak")
    assert live.mode == "twak"
    assert RuntimeState.from_dict(live.as_dict()).mode == "twak"


def test_mode_tag_defaults_none_and_is_excluded_from_equality():
    # A legacy/untagged payload (no "mode" key) decodes to mode=None, and the tag does
    # NOT perturb equality (compare=False) so the existing round-trip contract holds.
    legacy = RuntimeState.new_session(Decimal("1000"))
    assert legacy.mode is None
    payload = legacy.as_dict()
    del payload["mode"]
    assert RuntimeState.from_dict(payload).mode is None
    # Two states differing ONLY in mode remain equal (mode is compare=False).
    a = RuntimeState.new_session(Decimal("1000"), mode="paper")
    b = RuntimeState.new_session(Decimal("1000"), mode="twak")
    assert a == b


def test_round_trip_preserves_decimals_without_float_drift():
    # A value that is unrepresentable as a binary float must survive intact.
    state = RuntimeState.new_session(Decimal("1000"))
    state.equity_usd = Decimal("0.1")
    state.cash_usd = Decimal("0.2")
    state.open_stressed_loss_usd = Decimal("0.3")

    restored = RuntimeState.from_dict(state.as_dict())

    assert restored.equity_usd == Decimal("0.1")
    assert restored.cash_usd == Decimal("0.2")
    assert restored.open_stressed_loss_usd == Decimal("0.3")
    # Exact decimal equality, never coerced through float.
    assert restored.equity_usd + restored.cash_usd + restored.open_stressed_loss_usd == Decimal("0.6")
