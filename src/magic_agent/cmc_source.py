from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from magic_agent.cmc_selector import CandidateSnapshot


NON_DIRECTIONAL = {
    "USDT", "USDC", "DAI", "USD1", "USDE", "USDD", "TUSD", "FDUSD",
    "FRAX", "FRXUSD", "USDF", "USDF", "LISUSD", "DUSD", "XUSD", "EURI",
    "BILL", "STABLE", "XAUT", "XAUM",
}


@dataclass(frozen=True)
class RawCmcQuote:
    cmc_id: int
    symbol: str
    momentum_7d: str
    momentum_30d: str


@dataclass(frozen=True)
class CmcExclusion:
    eligibility_id: str
    symbol: str
    reason_code: str


@dataclass(frozen=True)
class CmcBatch:
    snapshots: dict[str, CandidateSnapshot]
    exclusions: tuple[CmcExclusion, ...]


class CmcCandidateSource:
    def __init__(self, ledger, registry, client) -> None:
        self.ledger, self.registry, self.client = ledger, registry, client

    def snapshot(self, now: datetime) -> CmcBatch:
        symbols = tuple(dict.fromkeys(row.competition_symbol for row in self.ledger.records))
        quotes = self.client.fetch(symbols=symbols, observed_at=now)
        by_symbol: dict[str, list[RawCmcQuote]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote.symbol, []).append(quote)
        chosen, exclusions = [], []
        for row in self.ledger.records:
            if row.competition_symbol.upper() in NON_DIRECTIONAL:
                exclusions.append(CmcExclusion(row.eligibility_id, row.competition_symbol, "non_directional_asset"))
                continue
            identity = self.registry.get_by_symbol(row.competition_symbol)
            matches = by_symbol.get(row.competition_symbol, [])
            if identity is not None and identity.cmc_id is not None:
                matches = [quote for quote in matches if quote.cmc_id == identity.cmc_id]
            if len(matches) != 1:
                reason = "cmc_missing" if not matches else "cmc_symbol_ambiguous"
                exclusions.append(CmcExclusion(row.eligibility_id, row.competition_symbol, reason))
                continue
            chosen.append((row, identity, matches[0]))
        count = Decimal(len(chosen))
        rank_7d = {
            item[0].eligibility_id: Decimal(rank) / count
            for rank, item in enumerate(
                sorted(chosen, key=lambda value: (-Decimal(value[2].momentum_7d), value[0].eligibility_id)),
                start=1,
            )
        }
        rank_30d = {
            item[0].eligibility_id: Decimal(rank) / count
            for rank, item in enumerate(
                sorted(chosen, key=lambda value: (-Decimal(value[2].momentum_30d), value[0].eligibility_id)),
                start=1,
            )
        }
        snapshots = {}
        for row, identity, quote in chosen:
            identity_key = f"{row.competition_symbol.lower()}-bsc" if identity else row.eligibility_id
            snapshots[row.competition_symbol] = CandidateSnapshot(
                identity_key, now, now + timedelta(minutes=15),
                Decimal(quote.momentum_7d), Decimal(quote.momentum_30d),
                rank_7d[row.eligibility_id], rank_30d[row.eligibility_id],
                False, (), Decimal("1"),
            )
        return CmcBatch(snapshots, tuple(exclusions))
