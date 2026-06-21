"""Durable store for the open paper-position book (cross-restart re-entry guard).

The paper :class:`~magic_agent.position_manager.PositionManager` books reconciled
positions into an in-memory ``book`` list. That book is the single source for BOTH
protective exits and the concurrency cap, so if it is lost on restart the next
authorized cycle re-enters the same setup (cross-restart double-entry). This module
persists the open book to ``.magic_agent/positions.json`` (integrity-checked via the
shared :class:`~magic_agent.state_journal.StateJournal`) and restores it into the
book on startup, so a restart sees the open position: the concurrency cap blocks a
second entry AND ``process_exits`` can still manage/close the restored position.

PAPER-MODE MECHANISM. In live mode the open book is the CHAIN's truth — rebuilt by
reconcile/balances — not this local file. This durable store is the paper-mode
mechanism only; the live rebuild-from-chain path is a separate follow-on and must
NOT be wired off this file.

Fail-closed: a corrupt/tampered store surfaces ``IntegrityError`` (via the
StateJournal sha256 check) rather than starting with an empty book that would
re-enter — consistent with the corrupt runtime-state behavior.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from magic_agent.position_manager import ReconciledPosition
from magic_agent.state_journal import StateJournal

# The persisted fields of a ``ReconciledPosition``. ``quantity`` and
# ``stressed_loss_per_unit`` are money-valued, encoded/decoded as exact decimal
# strings so they round-trip with no binary-float drift (and the restored position
# stays a complete exit-source citizen).
_DECIMAL_FIELDS: tuple[str, ...] = ("quantity", "stressed_loss_per_unit")


class PositionStore:
    """Persist and restore the open paper-position book with integrity protection."""

    def __init__(self, path: str | Path) -> None:
        """Bind the store to its on-disk path.

        Args:
            path: Location of the ``positions.json`` durable store.
        """
        self.store = StateJournal(path)

    @property
    def path(self) -> Path:
        """The on-disk path of the durable store."""
        return self.store.path

    def save(self, positions: list[ReconciledPosition]) -> None:
        """Persist the open book as an integrity-checked JSON payload.

        Args:
            positions: The current open book to persist (Decimals are encoded as
                exact strings).
        """
        payload = [
            {
                "intent_id": position.intent_id,
                "quantity": str(position.quantity),
                "stressed_loss_per_unit": str(position.stressed_loss_per_unit),
                # Exit context (symbol/identity_key/stop/campaign_dol) so a restored
                # position stays exit-manageable. Optional strings/decimals are
                # encoded as exact strings (or null) with no binary-float drift.
                "symbol": position.symbol,
                "identity_key": position.identity_key,
                "stop": None if position.stop is None else str(position.stop),
                "campaign_dol": (
                    None if position.campaign_dol is None else str(position.campaign_dol)
                ),
            }
            for position in positions
        ]
        self.store.save(payload)

    def load(self) -> list[ReconciledPosition]:
        """Restore the open book from the durable store.

        Returns:
            The restored open positions (empty list if no store exists).

        Raises:
            IntegrityError: If the store is corrupt/tampered (sha256 mismatch). The
                caller must let this propagate — failing closed rather than starting
                with an empty book that would re-enter.
            ValueError: If a persisted record is missing a required field or carries
                an undecodable decimal.
        """
        if not self.store.path.exists():
            return []
        raw = self.store.load()
        return [self._decode(record) for record in raw]

    @staticmethod
    def _decode(record: dict) -> ReconciledPosition:
        try:
            intent_id = record["intent_id"]
            quantity = Decimal(record["quantity"])
            stressed_loss_per_unit = Decimal(record["stressed_loss_per_unit"])
        except KeyError as exc:
            raise ValueError(
                f"persisted position is missing required field {exc.args[0]!r}"
            ) from exc
        except (ArithmeticError, TypeError) as exc:
            raise ValueError(f"persisted position has an undecodable decimal: {exc}") from exc
        # Optional exit-context fields (records written before this field set will
        # simply lack them); decode the optional decimals exactly.
        try:
            stop = record.get("stop")
            campaign_dol = record.get("campaign_dol")
            stop_dec = None if stop is None else Decimal(stop)
            campaign_dol_dec = None if campaign_dol is None else Decimal(campaign_dol)
        except (ArithmeticError, TypeError) as exc:
            raise ValueError(f"persisted position has an undecodable decimal: {exc}") from exc
        return ReconciledPosition(
            intent_id,
            quantity,
            stressed_loss_per_unit,
            symbol=record.get("symbol"),
            identity_key=record.get("identity_key"),
            stop=stop_dec,
            campaign_dol=campaign_dol_dec,
        )
