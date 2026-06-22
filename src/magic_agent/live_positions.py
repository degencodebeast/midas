from __future__ import annotations

from decimal import Decimal

from magic_agent.execution_journal import ExecutionState
from magic_agent.position_manager import ReconciledPosition


def _is_reconciled(record) -> bool:
    state = getattr(record, "state", None)
    return state == "RECONCILED" or state is ExecutionState.RECONCILED


def rebuild_positions_from_chain(*, records, intents: dict[str, object], balances) -> list[ReconciledPosition]:
    rebuilt: list[ReconciledPosition] = []
    for record in records:
        if not _is_reconciled(record):
            continue
        intent_id = getattr(record, "intent_id", None) or record.evidence.get("intent", {}).get("intent_id")
        if intent_id not in intents:
            continue
        intent = intents[intent_id]
        setup = intent.setup
        snapshot = balances.snapshot(setup.identity_key)
        quantity = Decimal(str(snapshot["token"]))
        if quantity <= 0:
            continue
        rebuilt.append(ReconciledPosition(
            intent_id,
            quantity,
            setup.entry - setup.structural_stop,
            symbol=setup.symbol,
            identity_key=setup.identity_key,
            entry=setup.entry,
            stop=setup.structural_stop,
            campaign_dol=setup.campaign_dol,
        ))
    return rebuilt
