from __future__ import annotations

import logging
from decimal import Decimal

from magic_agent.execution_journal import ExecutionState
from magic_agent.position_manager import ReconciledPosition
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent

_log = logging.getLogger(__name__)


def _is_reconciled(record) -> bool:
    state = getattr(record, "state", None)
    return state == "RECONCILED" or state is ExecutionState.RECONCILED


# A protective-sell record in any of these states means the sell was at least
# broadcast and (SUBMITTED+) is in flight or done — the position must be treated
# as CLOSED even if the wallet snapshot hasn't settled yet, so a restart mid-exit
# does NOT rebuild a just-sold position and re-drive its exit. EXECUTING/
# INTENT_PERSISTED (pre-broadcast) and BROADCAST_UNKNOWN/REVERTED (no confirmed
# sell) are deliberately EXCLUDED: those are not a known-closing sell, so the
# position stays managed (fail closed toward keeping a position we may still hold).
_CLOSING_SELL_STATES = frozenset({
    ExecutionState.SUBMITTED.value,
    ExecutionState.MINED.value,
    ExecutionState.CONFIRMED.value,
    ExecutionState.RECONCILED.value,
})


def _record_state_value(record) -> str | None:
    state = getattr(record, "state", None)
    if isinstance(state, ExecutionState):
        return state.value
    return state


def _intents_with_closing_sell(records) -> set[str]:
    """Collect buy intent_ids that have a closing protective-SELL record.

    A protective sell is journaled under a STABLE id ``sell:{buy_intent_id}:...``
    (see ``live_exits.sell_intent_id_for``) and carries ``evidence['side'] ==
    'sell'``. Any sell record in a SUBMITTED-or-beyond state means the exit is in
    flight or done, so the underlying buy intent's position is CLOSED.
    """
    closing: set[str] = set()
    for record in records:
        evidence = getattr(record, "evidence", {}) or {}
        if not isinstance(evidence, dict) or evidence.get("side") != "sell":
            continue
        if _record_state_value(record) not in _CLOSING_SELL_STATES:
            continue
        sell_id = getattr(record, "intent_id", None) or ""
        # ``sell:{buy_intent_id}:{reason}:{qty}`` -> recover the buy intent_id.
        if sell_id.startswith("sell:"):
            rest = sell_id[len("sell:"):]
            buy_intent_id = rest.rsplit(":", 2)[0]
            if buy_intent_id:
                closing.add(buy_intent_id)
    return closing


def _record_intent_id(record) -> str | None:
    return getattr(record, "intent_id", None) or record.evidence.get("intent", {}).get("intent_id")


def _intent_from_evidence(evidence: dict) -> SpotIntent | None:
    """Reconstruct a typed :class:`SpotIntent` from a journal record's evidence.

    The execution coordinator persists ``evidence["intent"] = asdict(intent)`` at
    intent-persist time, so the reconciled buy record carries the full strategy
    geometry (entry / structural_stop / campaign_dol / identity_key / symbol). After
    a JSON round-trip every ``Decimal`` field comes back as a string, so we coerce
    each numeric field back to ``Decimal`` here. Returns ``None`` when the evidence
    is missing or malformed (cannot reconstruct -> caller skips it rather than
    fabricating geometry).
    """
    raw_intent = evidence.get("intent") if isinstance(evidence, dict) else None
    if not isinstance(raw_intent, dict):
        return None
    raw_setup = raw_intent.get("setup")
    if not isinstance(raw_setup, dict):
        return None
    try:
        setup = AuthorizedSetup(
            setup_id=raw_setup["setup_id"],
            identity_key=raw_setup["identity_key"],
            symbol=raw_setup["symbol"],
            grade=raw_setup["grade"],
            raw_grade=raw_setup["raw_grade"],
            grade_promotion_reason=raw_setup.get("grade_promotion_reason"),
            entry=Decimal(str(raw_setup["entry"])),
            structural_stop=Decimal(str(raw_setup["structural_stop"])),
            stop_source=raw_setup["stop_source"],
            stop_anchor=Decimal(str(raw_setup["stop_anchor"])),
            stop_anchor_bar=int(raw_setup["stop_anchor_bar"]),
            campaign_dol=Decimal(str(raw_setup["campaign_dol"])),
            campaign_dol_source=raw_setup["campaign_dol_source"],
            checklist_dol=bool(raw_setup["checklist_dol"]),
            qml_id=raw_setup["qml_id"],
            qml_state=raw_setup["qml_state"],
            governing_poi_id=raw_setup["governing_poi_id"],
            governing_poi_timeframe=raw_setup["governing_poi_timeframe"],
            bias_alignment=raw_setup["bias_alignment"],
            scanner_commit=raw_setup["scanner_commit"],
            observed_at=raw_setup["observed_at"],
        )
        return SpotIntent(
            intent_id=raw_intent["intent_id"],
            setup=setup,
            quantity=Decimal(str(raw_intent["quantity"])),
            side=raw_intent["side"],
            purpose=ActionPurpose(raw_intent["purpose"]),
        )
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return None


def intents_from_journal(records) -> dict[str, SpotIntent]:
    """Reconstruct ``{intent_id: SpotIntent}`` from RECONCILED journal records.

    The reconciled buy records are the source of strategy levels (entry / stop /
    target / qty / identity). A record whose evidence cannot be reconstructed into a
    valid intent is skipped (logged at WARNING) rather than fabricated.
    """
    intents: dict[str, SpotIntent] = {}
    for record in records:
        if not _is_reconciled(record):
            continue
        intent = _intent_from_evidence(getattr(record, "evidence", {}) or {})
        if intent is None:
            _log.warning(
                "reconciled journal record %s carries no reconstructable intent; skipping",
                _record_intent_id(record),
            )
            continue
        intents[intent.intent_id] = intent
    return intents


def rebuild_positions_from_chain(*, records, intents: dict[str, object], balances) -> list[ReconciledPosition]:
    """Rebuild the open position book from CHAIN TRUTH at live startup.

    Combine the reconciled execution-journal buy records (which carry the strategy
    levels — entry / structural_stop / campaign_dol / intended qty / identity) with
    the CURRENT on-chain wallet token balances. A position is rebuilt ONLY when a
    reconciled buy record exists AND the wallet still holds that token; the realized
    on-chain quantity (chain truth) is used as the position size.

    Cases handled:

    * Reconciled record + token still held -> rebuilt into the book.
    * Reconciled record + token NO LONGER held (sold/exited out-of-band) -> DROPPED
      (left out of the book; logged at INFO).
    * A token held on-chain with NO reconciled record -> cannot reconstruct strategy
      levels, so it is NOT booked (no fabricated stop/target). A WARNING is logged for
      operator visibility (an unmanaged on-chain balance was found). Detected only
      when ``balances`` exposes ``held_tokens()`` (duck-typed); otherwise skipped.
    """
    rebuilt: list[ReconciledPosition] = []
    rebuilt_identity_keys: set[str] = set()
    records = list(records)
    # A position whose protective SELL is already broadcast (SUBMITTED+) / reconciled
    # is CLOSED even if the wallet snapshot hasn't settled yet — exclude it so a
    # restart mid-exit cannot rebuild a just-sold position and re-drive the exit.
    closed_by_sell = _intents_with_closing_sell(records)
    for record in records:
        if not _is_reconciled(record):
            continue
        intent_id = _record_intent_id(record)
        if intent_id not in intents:
            continue
        if intent_id in closed_by_sell:
            _log.info(
                "dropping reconciled position %s: a protective sell is journaled "
                "(submitted/reconciled); treating as closed",
                intent_id,
            )
            continue
        intent = intents[intent_id]
        setup = intent.setup
        snapshot = balances.snapshot(setup.identity_key)
        quantity = Decimal(str(snapshot["token"]))
        if quantity <= 0:
            # Journal-recorded buy whose token is no longer held on-chain (sold or
            # exited out-of-band) -> drop it: do not re-enter or manage it.
            _log.info(
                "dropping reconciled position %s (%s): token no longer held on-chain",
                intent_id, setup.identity_key,
            )
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
        rebuilt_identity_keys.add(setup.identity_key)

    _warn_unmanaged_balances(balances, rebuilt_identity_keys)
    return rebuilt


def _warn_unmanaged_balances(balances, managed_identity_keys: set[str]) -> None:
    """Log a WARNING for any held on-chain token with no reconciled journal record.

    These cannot be reconstructed into a managed position (no known stop/target), so
    they are flagged for operator visibility and left OUT of the strategy book. Only
    runs when ``balances`` exposes a ``held_tokens()`` enumeration; the production
    reader's full enumeration is a follow-up.
    """
    held_tokens = getattr(balances, "held_tokens", None)
    if held_tokens is None:
        return
    try:
        held = held_tokens()
    except Exception:
        _log.warning("could not enumerate held on-chain tokens for unmanaged-balance check")
        return
    for token in held or []:
        identity_key = token.get("identity_key") if isinstance(token, dict) else None
        quantity = token.get("quantity") if isinstance(token, dict) else None
        if identity_key in managed_identity_keys:
            continue
        try:
            qty = Decimal(str(quantity))
        except (ArithmeticError, TypeError, ValueError):
            qty = Decimal("0")
        if qty <= 0:
            continue
        _log.warning(
            "unmanaged on-chain balance: token %s (qty %s) is held but has no reconciled "
            "journal record; leaving it OUT of the strategy book (no known stop/target)",
            identity_key, qty,
        )
