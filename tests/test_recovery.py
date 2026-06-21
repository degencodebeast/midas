"""Crash-recovery and entry-observation tests (TDD-first).

Covers the production behind ``app.reconcile_unfinished`` (fail-closed blocking
when any non-terminal execution survives a restart) and ``app.observe_entry``
(the entry-candidate ``LifecycleObservation`` factory).
"""
from decimal import Decimal

from magic_agent.execution_journal import ExecutionJournal, ExecutionState
from magic_agent.lifecycle import LifecycleEvaluator
from magic_agent.recovery import observe_entry, reconcile_unfinished
from magic_agent.risk_policy import RiskDecision
from magic_agent.runtime_state import RuntimeState
from magic_agent.spot_models import AuthorizedSetup

NOW = "2026-06-21T00:00:00+00:00"


def _state() -> RuntimeState:
    return RuntimeState.new_session(Decimal("10000"))


def _risk() -> RiskDecision:
    return RiskDecision(
        True, None, (), Decimal("0.0025"), Decimal("2.5"),
        Decimal("0.25"), Decimal("0.25"),
    )


def _journal(tmp_path, name: str) -> ExecutionJournal:
    return ExecutionJournal(tmp_path / name)


def _drive(journal: ExecutionJournal, intent_id: str, target: ExecutionState) -> None:
    """Walk a record from intent through the FSM to ``target``."""
    journal.create(intent_id, f"key-{intent_id}", {"src": "test"})
    path = {
        ExecutionState.EXECUTING: [ExecutionState.EXECUTING],
        ExecutionState.SUBMITTED: [ExecutionState.EXECUTING, ExecutionState.SUBMITTED],
        ExecutionState.MINED: [ExecutionState.EXECUTING, ExecutionState.SUBMITTED, ExecutionState.MINED],
        ExecutionState.CONFIRMED: [
            ExecutionState.EXECUTING, ExecutionState.SUBMITTED,
            ExecutionState.MINED, ExecutionState.CONFIRMED,
        ],
        ExecutionState.BROADCAST_UNKNOWN: [ExecutionState.EXECUTING, ExecutionState.BROADCAST_UNKNOWN],
        ExecutionState.REVERTED: [ExecutionState.EXECUTING, ExecutionState.SUBMITTED, ExecutionState.REVERTED],
        ExecutionState.RECONCILED: [ExecutionState.EXECUTING, ExecutionState.BROADCAST_UNKNOWN, ExecutionState.RECONCILED],
    }[target]
    for step in path:
        journal.transition(intent_id, step)


def test_non_terminal_execution_fail_closed_blocks_new_exposure(tmp_path):
    journal = _journal(tmp_path, "nonterminal.json")
    _drive(journal, "i-1", ExecutionState.BROADCAST_UNKNOWN)
    state = _state()
    assert state.blocks_new_exposure is False

    reconcile_unfinished(journal, state)

    assert state.blocks_new_exposure is True


def test_any_non_terminal_state_blocks_even_with_terminal_siblings(tmp_path):
    journal = _journal(tmp_path, "mixed.json")
    _drive(journal, "done", ExecutionState.RECONCILED)
    _drive(journal, "pending", ExecutionState.MINED)
    state = _state()

    reconcile_unfinished(journal, state)

    assert state.blocks_new_exposure is True


def test_all_terminal_journal_leaves_exposure_unblocked(tmp_path):
    journal = _journal(tmp_path, "terminal.json")
    _drive(journal, "ok", ExecutionState.RECONCILED)
    _drive(journal, "bad", ExecutionState.REVERTED)
    state = _state()

    reconcile_unfinished(journal, state)

    assert state.blocks_new_exposure is False


def test_empty_journal_leaves_exposure_unblocked(tmp_path):
    journal = _journal(tmp_path, "empty.json")
    state = _state()

    reconcile_unfinished(journal, state)

    assert state.blocks_new_exposure is False


def test_observe_entry_builds_entry_candidate_observation():
    setup = AuthorizedSetup.example()
    risk = _risk()

    observation = observe_entry(setup, risk, NOW)

    assert observation.setup is setup
    assert observation.risk is risk
    assert observation.position is None
    assert observation.low is None
    assert observation.high is None
    assert observation.opposing_htf_invalidated is False
    assert observation.risk_reduction_qty == Decimal("0")
    assert observation.entries_halted is False


def test_observe_entry_evaluates_to_source_stamped_decision_inputs():
    observation = observe_entry(AuthorizedSetup.example(), _risk(), NOW)

    inputs = LifecycleEvaluator().evaluate(observation)

    assert inputs.source == "lifecycle_evaluator_v1"
    assert inputs.exit_reason is None
    assert inputs.exit_quantity == Decimal("0")
    assert inputs.entries_halted is False
