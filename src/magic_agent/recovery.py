"""Crash-recovery and entry-observation factories for the spot loop.

Two production helpers the runtime cycle calls each pass:

* :func:`reconcile_unfinished` runs *first* in ``run_cycle`` and fails closed:
  if any prior execution survived a restart in a non-terminal state, the loop is
  forbidden from booking new exposure until that swap's fate is resolved.
* :func:`observe_entry` builds the :class:`~magic_agent.lifecycle.LifecycleObservation`
  for an entry candidate, which the shared :class:`LifecycleEvaluator` (the single
  producer of ``DecisionInputs``) then resolves.
"""
from __future__ import annotations

from decimal import Decimal

from magic_agent.execution_journal import ExecutionJournal, ExecutionState
from magic_agent.lifecycle import LifecycleObservation
from magic_agent.risk_policy import RiskDecision
from magic_agent.runtime_state import RuntimeState
from magic_agent.spot_models import AuthorizedSetup

# An execution is *settled* only once it has reached one of these states; every
# other state means a swap's on-chain fate is still unresolved.
_TERMINAL_STATES: frozenset[ExecutionState] = frozenset(
    {ExecutionState.REVERTED, ExecutionState.RECONCILED},
)


def reconcile_unfinished(execution_journal: ExecutionJournal, state: RuntimeState) -> None:
    """Fail closed if any execution survived a restart in a non-terminal state.

    Scans the execution journal; if *any* record is in a non-terminal state
    (anything other than ``REVERTED`` / ``RECONCILED``) we set
    ``state.blocks_new_exposure = True`` so the loop processes protective exits
    but books no new exposure while a prior swap's outcome is unknown. An empty
    or all-terminal journal leaves the flag untouched (``False``).

    NOTE (live enhancement): auto-resuming — re-polling an on-chain receipt to
    AUTO-RESOLVE a ``BROADCAST_UNKNOWN`` transaction — requires live chain access
    and is a deliberate follow-on. For now the safe, deterministic, testable
    behavior is conservative blocking.

    Args:
        execution_journal: The persisted execution journal to scan on startup.
        state: The shared runtime state whose exposure gate may be raised.
    """
    for record in execution_journal.records.values():
        if record.state not in _TERMINAL_STATES:
            state.blocks_new_exposure = True
            return


def observe_entry(
    setup: AuthorizedSetup,
    risk: RiskDecision,
    now: object,
) -> LifecycleObservation:
    """Build the lifecycle observation for an entry candidate.

    The cycle only reaches this factory after passing the halt gate and sizing,
    so there is no open position and no protective exit to express: ``position``,
    ``low``, ``high`` are ``None``, ``opposing_htf_invalidated`` is ``False``,
    ``risk_reduction_qty`` is ``Decimal("0")``, and ``entries_halted`` is
    ``False``. The evaluator turns this into a source-stamped ``DecisionInputs``.

    Args:
        setup: The scanner-authorized setup driving this entry candidate.
        risk: The RiskPolicy decision sizing the candidate.
        now: The cycle timestamp (kept for call-site symmetry; entry observations
            carry no time-derived exit fields).

    Returns:
        A :class:`LifecycleObservation` describing an entry with no exit pressure.
    """
    return LifecycleObservation(
        setup=setup,
        risk=risk,
        position=None,
        low=None,
        high=None,
        opposing_htf_invalidated=False,
        risk_reduction_qty=Decimal("0"),
        entries_halted=False,
    )
