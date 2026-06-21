from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.cmc_source import CmcCandidateSource, RawCmcQuote


def test_contract_bound_cmc_id_wins_over_same_symbol_results():
    ledger = SimpleNamespace(records=(SimpleNamespace(eligibility_id="track1-001", competition_symbol="APE"),))
    registry = SimpleNamespace(get_by_symbol=lambda symbol: SimpleNamespace(cmc_id=18876))
    client = SimpleNamespace(fetch=lambda **kwargs: (
        RawCmcQuote(18876, "APE", "2", "10"), RawCmcQuote(999999, "APE", "200", "500"),
    ))
    result = CmcCandidateSource(ledger, registry, client).snapshot(datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert result.snapshots["APE"].identity_key == "ape-bsc"
    assert result.snapshots["APE"].momentum_7d == Decimal("2")


def test_unresolved_ambiguous_symbol_is_excluded_not_guessed():
    ledger = SimpleNamespace(records=(SimpleNamespace(eligibility_id="track1-001", competition_symbol="B"),))
    registry = SimpleNamespace(get_by_symbol=lambda symbol: None)
    client = SimpleNamespace(fetch=lambda **kwargs: (
        RawCmcQuote(1, "B", "2", "10"), RawCmcQuote(2, "B", "3", "20"),
    ))
    result = CmcCandidateSource(ledger, registry, client).snapshot(datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert result.snapshots == {}
    assert result.exclusions[0].reason_code == "cmc_symbol_ambiguous"
