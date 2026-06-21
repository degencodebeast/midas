from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class IdentityRecord:
    competition_symbol: str
    cmc_id: int
    chain_id: int
    contract_address: str
    decimals: int
    onchain_symbol: str
    market_data_source: str
    market_data_symbol: str
    coverage_status: str
    verification_status: str
    verified_at: str
    sources: tuple[str, ...]


class IdentityRegistry:
    def __init__(self, records: list[IdentityRecord]) -> None:
        self._symbols = {r.competition_symbol: r for r in records}
        self._contracts = {r.contract_address.lower(): r for r in records}
        self._identity_keys = {f"{r.competition_symbol.lower()}-bsc": r for r in records}
        if len(self._symbols) != len(records) or len(self._contracts) != len(records):
            raise ValueError("duplicate symbol or contract identity")

    @classmethod
    def load(cls, path: str | Path) -> "IdentityRegistry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([IdentityRecord(**{**row, "sources": tuple(row["sources"])}) for row in raw])

    def by_symbol(self, symbol: str) -> IdentityRecord:
        return self._symbols[symbol]

    def get_by_symbol(self, symbol: str) -> IdentityRecord | None:
        return self._symbols.get(symbol)

    def by_contract(self, address: str) -> IdentityRecord:
        return self._contracts[address.lower()]

    def by_contract_key(self, identity_key: str) -> IdentityRecord:
        return self._identity_keys[identity_key]

    def monitorable(self) -> tuple[IdentityRecord, ...]:
        return tuple(r for r in self._symbols.values() if r.coverage_status == "scannable")

    def execution_eligible(self) -> tuple[IdentityRecord, ...]:
        return tuple(r for r in self.monitorable() if r.verification_status == "gold")
