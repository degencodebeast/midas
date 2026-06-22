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

from dataclasses import asdict

from magic_agent.execution_journal import ExecutionState
from magic_agent.reconcile import reconcile_buy
from magic_agent.twak import TwakError


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
            payload = self.twak.json([
                "swap", str(intent.quantity), "USDC", contract,
                "--chain", "bsc", "--json",
            ])
            tx_hash = payload.get("data", {}).get("tx_hash") or payload.get("tx_hash")
            if not tx_hash:
                raise TwakError("swap response missing transaction hash")
        except Exception as exc:
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
            return "MINED"
        if result.state != "RECONCILED":
            return result.state
        self.journal.transition(intent.intent_id, ExecutionState.CONFIRMED)
        self.journal.transition(intent.intent_id, ExecutionState.RECONCILED,
                                post_balances=post, actual_qty=str(result.position_qty))
        self.positions.open_from_reconciliation(intent, result.position_qty)
        return "RECONCILED"
