from datetime import datetime, timezone

from magic_agent.qualification import QualificationConfig, evaluate_qualification_pace


def test_qualification_pace_on_track():
    decision = evaluate_qualification_pace(
        completed_trade_count=2,
        now=datetime(2026, 6, 24, 0, 0, tzinfo=timezone.utc),
        config=QualificationConfig(
            minimum_trade_count=7,
            window_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
        ),
    )

    assert decision.behind_pace is False
    assert decision.required_by_now == 2
    assert decision.warning is None


def test_qualification_pace_behind_is_advisory_only():
    decision = evaluate_qualification_pace(
        completed_trade_count=0,
        now=datetime(2026, 6, 25, 0, 0, tzinfo=timezone.utc),
        config=QualificationConfig(
            minimum_trade_count=7,
            window_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
        ),
    )

    assert decision.behind_pace is True
    assert decision.required_by_now == 3
    assert decision.can_force_trade is False
    assert decision.warning == "minimum_trade_count_behind_pace"
