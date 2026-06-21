from datetime import datetime, timezone

from magic_agent.candidate_source import CandidateSource
from magic_agent.eligibility import EligibilityLedger
from magic_agent.identity_registry import IdentityRegistry
from magic_agent.watchlist import PINNED_SYMBOLS, WatchlistState


def test_every_eligibility_row_becomes_a_candidate_or_reasoned_exclusion():
    now = datetime(2026, 6, 21, 8, tzinfo=timezone.utc)
    source = CandidateSource(
        EligibilityLedger.load("data/track1_eligibility.json"),
        IdentityRegistry.load("data/track1_identities.json"),
    )
    batch = source.enumerate(now=now, watchlist=WatchlistState.initial(), snapshots={})
    accounted = len(batch.monitoring) + len(batch.discovery) + len(batch.exclusions)
    assert accounted == 149
    assert {row.symbol for row in batch.monitoring} == set(PINNED_SYMBOLS)
    assert all(row.reason_code for row in batch.exclusions)
