import json
from pathlib import Path

from magic_agent.identity_registry import IdentityRegistry


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TRACK1_PATH = _REPO_ROOT / "data" / "track1_identities.json"

# The 6 historically-known tokens that must keep their cmc_ids + contracts.
_KNOWN_IDS = {
    "APE": (18876, "0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79"),
    "ZEC": (1437, "0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb"),
    "DEXE": (7326, "0x6E88056E8376Ae7709496Ba64d37fa2f8015ce3e"),
    "TRX": (1958, "0xCE7de646e7208a4Ef112cb6ed5038FA6cC6b12e3"),
    "LINK": (1975, "0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD"),
    "XRP": (52, "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE"),
}


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


def test_record_without_cmc_id_loads_with_none(tmp_path):
    # cmc_id is now optional: a record with only symbol + contract must load.
    path = tmp_path / "ids.json"
    path.write_text('[{"competition_symbol":"B","chain_id":56,"contract_address":"0x6bdcCe4A559076e37755a78Ce0c06214E59e4444","decimals":18,"onchain_symbol":"B","market_data_source":"gateio","market_data_symbol":"B_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]}]')
    registry = IdentityRegistry.load(path)
    assert registry.by_symbol("B").cmc_id is None


def test_get_by_contract_is_case_insensitive(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text('[{"competition_symbol":"B","chain_id":56,"contract_address":"0x6bdcCe4A559076e37755a78Ce0c06214E59e4444","decimals":18,"onchain_symbol":"B","market_data_source":"gateio","market_data_symbol":"B_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]}]')
    registry = IdentityRegistry.load(path)
    assert registry.get_by_contract("0x6BDCCE4A559076E37755A78CE0C06214E59E4444").competition_symbol == "B"
    assert registry.get_by_contract("0x6bdcce4a559076e37755a78ce0c06214e59e4444").competition_symbol == "B"
    assert registry.get_by_contract("0xdeadbeef") is None


def test_full_track1_registry_loads_all_gold_and_known_intact():
    registry = IdentityRegistry.load(_TRACK1_PATH)
    records = json.loads(_TRACK1_PATH.read_text(encoding="utf-8"))

    # The authoritative Track-1 BSC set is ~147 tokens.
    assert len(records) >= 140
    # Every shipped record is execution-eligible gold.
    assert all(r["verification_status"] == "gold" for r in records)
    assert len(registry.execution_eligible()) == len(records)

    # The 6 known tokens keep their cmc_ids + correct contracts.
    for sym, (cid, contract) in _KNOWN_IDS.items():
        rec = registry.by_symbol(sym)
        assert rec.cmc_id == cid
        assert rec.contract_address == contract

    # Tokens beyond the known 6 carry no cmc_id (resolved at runtime via CMC).
    no_id = [r for r in records if r.get("cmc_id") is None]
    assert len(no_id) >= 1
