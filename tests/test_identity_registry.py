from magic_agent.identity_registry import IdentityRegistry


def test_case_distinct_symbols_and_contract_lookup(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text('[{"competition_symbol":"USDf","cmc_id":1,"chain_id":56,"contract_address":"0x01","decimals":18,"onchain_symbol":"USDf","market_data_source":"gateio","market_data_symbol":"USDF_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]},{"competition_symbol":"USDF","cmc_id":2,"chain_id":56,"contract_address":"0x02","decimals":18,"onchain_symbol":"USDF","market_data_source":"gateio","market_data_symbol":"USDF2_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]}]')
    registry = IdentityRegistry.load(path)
    assert registry.by_symbol("USDf").contract_address == "0x01"
    assert registry.by_symbol("USDF").contract_address == "0x02"
    assert registry.by_contract("0x02").cmc_id == 2


def test_monitoring_and_execution_identity_are_separate(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text('[{"competition_symbol":"APE","cmc_id":18876,"chain_id":56,"contract_address":"0x01","decimals":18,"onchain_symbol":"APE","market_data_source":"gateio","market_data_symbol":"APE_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]},{"competition_symbol":"ZEC","cmc_id":1437,"chain_id":56,"contract_address":"0x02","decimals":18,"onchain_symbol":"ZEC","market_data_source":"gateio","market_data_symbol":"ZEC_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]}]')
    registry = IdentityRegistry.load(path)
    assert {row.competition_symbol for row in registry.monitorable()} == {"APE", "ZEC"}
    assert {row.competition_symbol for row in registry.execution_eligible()} == {"APE"}
