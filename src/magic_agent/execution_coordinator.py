"""Coordinate TWAK submit -> receipt -> reconcile -> position booking.

A position is booked only after reconciliation returns ``RECONCILED``: the
coordinator calls :meth:`PositionManager.open_from_reconciliation` with the
realized on-chain token delta, never an optimistic or quoted size. Every other
outcome books nothing and records the actual terminal/intermediate journal state:

* ``REVERTED`` -- receipt failed.
* ``MINED`` -- insufficient confirmations.
* ``CONFIRMED_NOT_RECONCILED`` -- confirmed but balance deltas disagree.
* ``BROADCAST_UNKNOWN`` -- timeout, malformed output, or missing hash after a
  possible broadcast; treated as unsafe (fail closed), never assumed successful
  and never automatically retried.

Idempotency is enforced by the execution journal: re-running an already-created
intent raises before any swap is submitted, so the coordinator never
double-submits or double-books.
"""

import logging
from dataclasses import asdict
from decimal import Decimal

from magic_agent.execution_journal import ExecutionState
from magic_agent.reconcile import reconcile_buy
from magic_agent.twak import TwakError

_log = logging.getLogger(__name__)


class ExecutionCoordinator:
    def __init__(self, *, twak, rpc, balances, journal, positions, registry,
                 required_confirmations: int = 2) -> None:
        self.twak = twak
        self.rpc = rpc
        self.balances = balances
        self.journal = journal
        self.positions = positions
        self.registry = registry
        self.required_confirmations = required_confirmations

    def submit(self, intent, *, quote: dict, policy) -> str:
        return self.submit_buy(
            intent, quote=quote, policy=policy,
            pre_nonce=self.rpc.wallet_nonce(),
        )

    def submit_buy(self, intent, *, quote: dict, policy, pre_nonce: int) -> str:
        pre = self.balances.snapshot(intent.setup.identity_key)
        self.journal.create(intent.intent_id, intent.intent_id, {
            "intent": asdict(intent), "quote": quote, "policy": asdict(policy),
            "pre_balances": pre, "pre_nonce": pre_nonce,
        })
        self.journal.transition(intent.intent_id, ExecutionState.EXECUTING)
        try:
            contract = self.registry.by_contract_key(intent.setup.identity_key).contract_address
            # ``intent.quantity`` is a TOKEN qty; a BUY swap's SOURCE amount is USDC.
            # Spend usdc_in = qty * entry USDC — the SAME amount the live quote provider
            # priced (single source of truth: recompute, then verify the carried
            # quote's usdc_in matches if present). This is an explicit raise, NOT an
            # assert: asserts are stripped under ``python -O`` and a money-safety
            # invariant must survive that. The raise stays inside this try so a
            # mismatch maps to BROADCAST_UNKNOWN before any swap is broadcast.
            usdc_in = intent.quantity * intent.setup.entry
            quoted_usdc_in = quote.get("usdc_in") if isinstance(quote, dict) else None
            if quoted_usdc_in is not None and Decimal(str(quoted_usdc_in)) != usdc_in:
                raise TwakError(f"quote usdc_in {quoted_usdc_in} != qty*entry {usdc_in}")
            payload = self.twak.json([
                "swap", str(usdc_in), "USDC", contract,
                "--chain", "bsc", "--json",
            ])
            tx_hash = payload.get("data", {}).get("tx_hash") or payload.get("tx_hash")
            if not tx_hash:
                raise TwakError("swap response missing transaction hash")
        except Exception as exc:
            _log.warning("swap broadcast outcome unknown: %s", exc)
            self.journal.transition(
                intent.intent_id, ExecutionState.BROADCAST_UNKNOWN, error=str(exc),
            )
            return "BROADCAST_UNKNOWN"
        self.journal.transition(intent.intent_id, ExecutionState.SUBMITTED, tx_hash=tx_hash)
        # Fail closed on a receipt-poll failure: the real BscRpcClient RAISES on a
        # receipt timeout or RPC/transport error rather than fabricating a result.
        # SUBMITTED -> BROADCAST_UNKNOWN is a valid, exposure-blocking transition;
        # recovery.reconcile_unfinished blocks new exposure next cycle.
        try:
            receipt = self.rpc.wait_receipt(tx_hash)
        except Exception as exc:
            _log.warning("receipt wait failed; broadcast outcome unknown: %s", exc)
            self.journal.transition(
                intent.intent_id, ExecutionState.BROADCAST_UNKNOWN, error=str(exc),
            )
            return "BROADCAST_UNKNOWN"
        self.journal.transition(intent.intent_id, ExecutionState.MINED, receipt=receipt)
        # The post-MINED reads (confirmations + post balance snapshot + reconcile) can
        # fault on a real chain/CLI error. Returning "MINED" WITHOUT transitioning
        # leaves the record at the non-terminal MINED state (so recovery blocks new
        # exposure next cycle) — never a crash, and never the invalid
        # MINED -> BROADCAST_UNKNOWN transition.
        try:
            confirmations = self.rpc.confirmations(receipt)
            post = self.balances.snapshot(intent.setup.identity_key)
            result = reconcile_buy(
                receipt=receipt, confirmations=confirmations,
                required_confirmations=self.required_confirmations,
                stable_before=pre["stable"], stable_after=post["stable"],
                token_before=pre["token"], token_after=post["token"],
            )
        except Exception:
            _log.exception("post-MINED reconcile read failed; returning MINED (fail closed)")
            return "MINED"
        if result.state != "RECONCILED":
            return result.state
        self.journal.transition(intent.intent_id, ExecutionState.CONFIRMED)
        self.journal.transition(intent.intent_id, ExecutionState.RECONCILED,
                                post_balances=post, actual_qty=str(result.position_qty))
        self.positions.open_from_reconciliation(intent, result.position_qty)
        return "RECONCILED"
