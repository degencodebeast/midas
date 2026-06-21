from dataclasses import dataclass
from datetime import datetime

from magic_agent.cmc_selector import CandidateSnapshot


@dataclass(frozen=True)
class MonitoringCandidate:
    eligibility_id: str
    symbol: str
    identity_key: str
    snapshot: CandidateSnapshot | None
    execution_eligible: bool
    pinned: bool


@dataclass(frozen=True)
class CandidateExclusion:
    eligibility_id: str
    symbol: str
    reason_code: str


@dataclass(frozen=True)
class CandidateBatch:
    monitoring: tuple[MonitoringCandidate, ...]
    discovery: tuple[MonitoringCandidate, ...]
    exclusions: tuple[CandidateExclusion, ...]


class CandidateSource:
    def __init__(self, ledger, registry) -> None:
        self.ledger = ledger
        self.registry = registry

    def enumerate(self, *, now: datetime, watchlist, snapshots: dict[str, CandidateSnapshot]) -> CandidateBatch:
        monitoring, discovery, exclusions = [], [], []
        discovery_due = watchlist.discovery_due(now)
        for row in self.ledger.records:
            snapshot = snapshots.get(row.competition_symbol)
            identity = self.registry.get_by_symbol(row.competition_symbol)
            if identity is None:
                reason = (
                    "identity_verification_required"
                    if discovery_due and snapshot is not None and snapshot.expires_at >= now and not snapshot.vetoed
                    else "identity_unresolved"
                )
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, reason))
                continue
            if identity.coverage_status != "scannable":
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "market_data_unscannable"))
                continue
            candidate = MonitoringCandidate(
                row.eligibility_id, row.competition_symbol,
                f"{row.competition_symbol.lower()}-bsc", snapshot,
                identity.verification_status == "gold", row.competition_symbol in watchlist.active_symbols,
            )
            if candidate.pinned:
                monitoring.append(candidate)
            elif not discovery_due:
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "discovery_not_due"))
            elif snapshot is None or snapshot.expires_at < now:
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "cmc_missing_or_stale"))
            elif snapshot.vetoed:
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "cmc_veto"))
            else:
                discovery.append(candidate)
        return CandidateBatch(tuple(monitoring), tuple(discovery), tuple(exclusions))
