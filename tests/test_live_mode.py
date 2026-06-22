from decimal import Decimal

from magic_agent.live_mode import LiveModeState
from magic_agent.runtime_state import RuntimeState


def test_live_mode_initial_state_requires_canary():
    state = RuntimeState.new_session(Decimal("10000"))

    assert state.canary_mode is True
    assert state.live_mode_state()["mode"] == "canary"
    assert state.live_mode_state()["canary_required"] is True
    assert state.live_mode_state()["canary_completed"] is False


def test_live_mode_persists_canary_promotion_fields():
    state = RuntimeState.new_session(Decimal("10000"))
    state.canary_completed = True
    state.canary_mode = False
    state.canary_intent_id = "intent-1"
    state.canary_reconciled_at = "2026-06-22T12:00:00+00:00"
    state.promotion_reason = "canary_reconciled_cost_viable"
    state.normal_scoring_started_at = "2026-06-22T12:01:00+00:00"
    state.cost_viability_evidence = {
        "approved": True,
        "estimated_round_trip_cost_bps": "76",
    }

    restored = RuntimeState.from_dict(state.as_dict())

    assert restored.canary_mode is False
    assert restored.canary_completed is True
    assert restored.canary_intent_id == "intent-1"
    assert restored.canary_reconciled_at == "2026-06-22T12:00:00+00:00"
    assert restored.promotion_reason == "canary_reconciled_cost_viable"
    assert restored.normal_scoring_started_at == "2026-06-22T12:01:00+00:00"
    assert restored.cost_viability_evidence == {
        "approved": True,
        "estimated_round_trip_cost_bps": "76",
    }
    assert restored.live_mode_state()["mode"] == "normal_scoring"


def test_live_mode_from_legacy_state_defaults_to_canary():
    legacy = RuntimeState.new_session(Decimal("10000")).as_dict()
    for key in (
        "canary_completed",
        "canary_intent_id",
        "canary_reconciled_at",
        "promotion_reason",
        "normal_scoring_started_at",
        "cost_viability_evidence",
        "auto_promote_after_canary",
    ):
        legacy.pop(key, None)

    restored = RuntimeState.from_dict(legacy)

    assert restored.canary_mode is True
    assert restored.canary_completed is False
    assert restored.auto_promote_after_canary is True
    assert restored.live_mode_state()["mode"] == "canary"


def test_live_mode_state_reports_halted_review_when_exposure_blocked():
    state = RuntimeState.new_session(Decimal("10000"))
    state.blocks_new_exposure = True

    assert state.live_mode_state()["mode"] == "halted_review"
