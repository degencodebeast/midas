from datetime import datetime, timedelta, timezone
from decimal import Decimal

from magic_agent.cmc_selector import CandidateSnapshot, select_candidates


def test_cmc_cannot_authorize_or_increase_size():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    rows = [CandidateSnapshot("zec-bsc", now, now + timedelta(minutes=15), Decimal("12"), Decimal("40"), Decimal("0.10"), Decimal("0.20"), False, (), Decimal("1"))]
    selected = select_candidates(rows, now=now)
    assert selected[0].identity_key == "zec-bsc"
    assert selected[0].macro_clamp <= Decimal("1")
    assert not hasattr(selected[0], "authorized")


def test_stale_snapshot_yields_no_new_candidates():
    now = datetime(2026, 6, 21, 1, tzinfo=timezone.utc)
    expired = CandidateSnapshot("zec-bsc", now, now - timedelta(seconds=1), Decimal("12"), Decimal("40"), Decimal("0.10"), Decimal("0.20"), False, (), Decimal("1"))
    assert select_candidates([expired], now=now) == ()


def test_counter_bias_momentum_is_a_risk_input_not_a_universe_exclusion():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    strong = CandidateSnapshot("eth-bsc", now, now + timedelta(minutes=15), Decimal("4"), Decimal("-8"), Decimal("0.20"), Decimal("0.80"), False, (), Decimal("1"))
    weak = CandidateSnapshot("ada-bsc", now, now + timedelta(minutes=15), Decimal("-1"), Decimal("20"), Decimal("0.80"), Decimal("0.20"), False, (), Decimal("1"))
    assert select_candidates([strong, weak], now=now) == (strong, weak)
    assert strong.counter_bias_momentum_qualified
    assert not weak.counter_bias_momentum_qualified
