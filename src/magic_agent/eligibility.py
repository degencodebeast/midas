from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class EligibilityRecord:
    eligibility_id: str
    competition_symbol: str


class EligibilityLedger:
    def __init__(self, records: tuple[EligibilityRecord, ...]) -> None:
        if len(records) != 149 or len({row.eligibility_id for row in records}) != 149:
            raise ValueError("Track 1 ledger must preserve 149 unique eligibility rows")
        self.records = records

    @classmethod
    def load(cls, path: str | Path) -> "EligibilityLedger":
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(tuple(EligibilityRecord(**row) for row in rows))
