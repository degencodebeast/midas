from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class CandidateSnapshot:
    identity_key: str
    observed_at: datetime
    expires_at: datetime
    momentum_7d: Decimal
    momentum_30d: Decimal
    momentum_7d_rank_pct: Decimal
    momentum_30d_rank_pct: Decimal
    vetoed: bool
    veto_reasons: tuple[str, ...]
    macro_clamp: Decimal

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.macro_clamp <= Decimal("1"):
            raise ValueError("macro clamp must only preserve or reduce size")
        if not all(
            Decimal("0") <= rank <= Decimal("1")
            for rank in (self.momentum_7d_rank_pct, self.momentum_30d_rank_pct)
        ):
            raise ValueError("momentum rank percentiles must be between zero and one")

    @property
    def counter_bias_momentum_qualified(self) -> bool:
        return self.momentum_7d > 0 and self.momentum_7d_rank_pct <= Decimal("0.25")


def select_candidates(rows: list[CandidateSnapshot], *, now: datetime) -> tuple[CandidateSnapshot, ...]:
    valid = [r for r in rows if r.expires_at >= now and not r.vetoed]
    return tuple(sorted(valid, key=lambda r: (r.momentum_7d_rank_pct, r.momentum_30d_rank_pct, r.identity_key)))
