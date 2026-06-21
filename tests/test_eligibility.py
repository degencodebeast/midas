from magic_agent.eligibility import EligibilityLedger


def test_authoritative_ledger_preserves_all_149_rows_and_duplicate_slx():
    ledger = EligibilityLedger.load("data/track1_eligibility.json")
    assert len(ledger.records) == 149
    assert len({row.eligibility_id for row in ledger.records}) == 149
    assert [row.competition_symbol for row in ledger.records].count("SLX") == 2
