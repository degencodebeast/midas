import pytest

from magic_agent.execution_journal import ExecutionJournal, ExecutionState
from magic_agent.state_journal import IntegrityError, StateJournal


def test_corrupt_state_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"payload":{},"sha256":"wrong"}')
    with pytest.raises(IntegrityError):
        StateJournal(path).load()


def test_execution_transition_is_monotonic_and_idempotent(tmp_path):
    journal = ExecutionJournal(tmp_path / "exec.json")
    journal.create("i-1", "key-1", {"quote": "q"})
    journal.transition("i-1", ExecutionState.EXECUTING)
    journal.transition("i-1", ExecutionState.SUBMITTED, tx_hash="0xabc")
    journal.transition("i-1", ExecutionState.SUBMITTED, tx_hash="0xabc")
    assert journal.get("i-1").state is ExecutionState.SUBMITTED
    with pytest.raises(ValueError):
        journal.transition("i-1", ExecutionState.INTENT_PERSISTED)


def test_idempotent_replay_is_noop_returns_existing(tmp_path):
    """Replaying the same transition (same state, same evidence) is a no-op."""
    journal = ExecutionJournal(tmp_path / "exec.json")
    journal.create("i-2", "key-2", {"quote": "q2"})
    journal.transition("i-2", ExecutionState.EXECUTING)
    journal.transition("i-2", ExecutionState.SUBMITTED, tx_hash="0xdef")
    # Replay exact same transition — must not raise, must not duplicate
    journal.transition("i-2", ExecutionState.SUBMITTED, tx_hash="0xdef")
    record = journal.get("i-2")
    assert record.state is ExecutionState.SUBMITTED
    assert record.evidence["tx_hash"] == "0xdef"


def test_conflicting_idempotent_transition_raises(tmp_path):
    """Same state but different evidence must raise ValueError."""
    journal = ExecutionJournal(tmp_path / "exec.json")
    journal.create("i-3", "key-3", {"quote": "q3"})
    journal.transition("i-3", ExecutionState.EXECUTING)
    journal.transition("i-3", ExecutionState.SUBMITTED, tx_hash="0xaaa")
    with pytest.raises(ValueError):
        journal.transition("i-3", ExecutionState.SUBMITTED, tx_hash="0xbbb")


def test_duplicate_create_raises(tmp_path):
    """Creating the same intent_id twice must raise ValueError."""
    journal = ExecutionJournal(tmp_path / "exec.json")
    journal.create("i-4", "key-4", {"quote": "q4"})
    with pytest.raises(ValueError, match="duplicate intent"):
        journal.create("i-4", "key-4", {"quote": "q4"})


def test_journal_persists_across_instances(tmp_path):
    """Records saved by one instance are loadable by a new instance."""
    path = tmp_path / "exec.json"
    j1 = ExecutionJournal(path)
    j1.create("i-5", "key-5", {"quote": "q5"})
    j1.transition("i-5", ExecutionState.EXECUTING)

    j2 = ExecutionJournal(path)
    record = j2.get("i-5")
    assert record.state is ExecutionState.EXECUTING
    assert record.idempotency_key == "key-5"


def test_invalid_backward_transition_raises(tmp_path):
    """Cannot go backward (e.g., SUBMITTED -> INTENT_PERSISTED)."""
    journal = ExecutionJournal(tmp_path / "exec.json")
    journal.create("i-6", "key-6", {"quote": "q6"})
    journal.transition("i-6", ExecutionState.EXECUTING)
    journal.transition("i-6", ExecutionState.SUBMITTED, tx_hash="0xccc")
    with pytest.raises(ValueError):
        journal.transition("i-6", ExecutionState.INTENT_PERSISTED)
