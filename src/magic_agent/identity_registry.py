from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class IdentityRecord:
    competition_symbol: str
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
    # cmc_id is OPTIONAL: a record may exist with only symbol + contract. The live
    # CMC client now reads the cmc_id from the CMC response (resolved by symbol +
    # contract), so it is no longer required as registry input. When present (>0)
    # it can be used to tighten disambiguation.
    cmc_id: int | None = None


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
        records = []
        for row in raw:
            # cmc_id is optional: a missing key or explicit null both mean "no id".
            normalized = {**row, "sources": tuple(row["sources"])}
            normalized.setdefault("cmc_id", None)
            records.append(IdentityRecord(**normalized))
        return cls(records)

    def by_symbol(self, symbol: str) -> IdentityRecord:
        return self._symbols[symbol]

    def get_by_symbol(self, symbol: str) -> IdentityRecord | None:
        return self._symbols.get(symbol)

    def by_contract(self, address: str) -> IdentityRecord:
        return self._contracts[address.lower()]

    def get_by_contract(self, address: str) -> IdentityRecord | None:
        """Case-insensitive contract -> record lookup (None when unknown).

        Used by the live CMC client to disambiguate single-ticker collisions by
        matching a CMC entry's BSC ``platform.token_address`` to a registry record.
        """
        return self._contracts.get(address.lower())

    def by_contract_key(self, identity_key: str) -> IdentityRecord:
        return self._identity_keys[identity_key]

    def monitorable(self) -> tuple[IdentityRecord, ...]:
        return tuple(r for r in self._symbols.values() if r.coverage_status == "scannable")

    def execution_eligible(self) -> tuple[IdentityRecord, ...]:
        return tuple(r for r in self.monitorable() if r.verification_status == "gold")
