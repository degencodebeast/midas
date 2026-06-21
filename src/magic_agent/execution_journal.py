# execution_journal.py
from dataclasses import asdict, dataclass, field
from enum import Enum

from magic_agent.state_journal import StateJournal


class ExecutionState(str, Enum):
    INTENT_PERSISTED = "INTENT_PERSISTED"
    EXECUTING = "EXECUTING"
    SUBMITTED = "SUBMITTED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    REVERTED = "REVERTED"
    BROADCAST_UNKNOWN = "BROADCAST_UNKNOWN"
    RECONCILED = "RECONCILED"


_NEXT = {
    ExecutionState.INTENT_PERSISTED: {ExecutionState.EXECUTING},
    ExecutionState.EXECUTING: {ExecutionState.SUBMITTED, ExecutionState.BROADCAST_UNKNOWN},
    ExecutionState.SUBMITTED: {ExecutionState.MINED, ExecutionState.REVERTED, ExecutionState.BROADCAST_UNKNOWN},
    ExecutionState.MINED: {ExecutionState.CONFIRMED, ExecutionState.REVERTED},
    ExecutionState.CONFIRMED: {ExecutionState.RECONCILED, ExecutionState.BROADCAST_UNKNOWN},
    ExecutionState.REVERTED: set(),
    ExecutionState.BROADCAST_UNKNOWN: {ExecutionState.MINED, ExecutionState.REVERTED, ExecutionState.RECONCILED},
    ExecutionState.RECONCILED: set(),
}


@dataclass
class ExecutionRecord:
    intent_id: str
    idempotency_key: str
    state: ExecutionState
    evidence: dict = field(default_factory=dict)


class ExecutionJournal:
    def __init__(self, path) -> None:
        self.store = StateJournal(path)
        self.records: dict[str, ExecutionRecord] = {}
        if self.store.path.exists():
            raw = self.store.load()
            self.records = {
                key: ExecutionRecord(
                    value["intent_id"], value["idempotency_key"],
                    ExecutionState(value["state"]), value["evidence"],
                )
                for key, value in raw.items()
            }

    def _save(self) -> None:
        self.store.save({key: {**asdict(record), "state": record.state.value}
                         for key, record in self.records.items()})

    def create(self, intent_id: str, idempotency_key: str, evidence: dict) -> None:
        if intent_id in self.records:
            raise ValueError("duplicate intent")
        self.records[intent_id] = ExecutionRecord(
            intent_id, idempotency_key, ExecutionState.INTENT_PERSISTED, dict(evidence),
        )
        self._save()

    def transition(self, intent_id: str, state: ExecutionState, **evidence) -> None:
        record = self.records[intent_id]
        if record.state is state:
            if evidence and any(record.evidence.get(key) != value for key, value in evidence.items()):
                raise ValueError("conflicting idempotent transition")
            return
        if state not in _NEXT[record.state]:
            raise ValueError(f"invalid transition {record.state.value}->{state.value}")
        record.state = state
        record.evidence.update(evidence)
        self._save()

    def get(self, intent_id: str) -> ExecutionRecord:
        return self.records[intent_id]
