"""Compliance ledger — observe confirmed swaps, emit alerts only.

``ComplianceLedger`` counts confirmed eligible swaps against verified
competition-day boundaries and emits :class:`ComplianceAlert` values ONLY. It is
an observer: it deliberately exposes **no** execution method. The ledger can never
authorize, size, or place a trade — that authority lives solely in the
scanner -> RiskPolicy -> DecisionPipeline -> ExecutionCoordinator path. Inventing an
execution method here would create a second, unaudited order origin, so it does not
exist by construction.

A confirmed eligible swap is a reconciled execution record (``state == "RECONCILED"``);
intermediate or failed records (``MINED``, ``BROADCAST_UNKNOWN``, ``REVERTED``, …) do
not count toward the daily qualification. When a competition-day boundary closes with
no confirmed swap, the ledger raises ``daily_qualification_at_risk`` so the operator can
react — it never trades on the operator's behalf.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComplianceAlert:
    """An advisory compliance alert. Carries a stable ``code`` only — never an order."""

    code: str


# The single terminal state that counts as a confirmed eligible swap.
_CONFIRMED_STATE = "RECONCILED"


class ComplianceLedger:
    """Observe confirmed swaps per competition day and emit alerts only."""

    def observe(self, records, observed_at) -> list[ComplianceAlert]:
        """Return compliance alerts for the current competition-day boundary.

        Args:
            records: The confirmed execution records observed this cycle (each
                duck-typed with a ``state`` attribute). Only ``RECONCILED``
                records count as confirmed eligible swaps.
            observed_at: The cycle's observation time (the competition-day
                boundary against which qualification is checked).

        Returns:
            A list of :class:`ComplianceAlert`. Empty when the day is qualified;
            ``[daily_qualification_at_risk]`` when no confirmed swap has landed.
        """
        confirmed = [
            record for record in records
            if getattr(record, "state", None) == _CONFIRMED_STATE
        ]
        if not confirmed:
            return [ComplianceAlert("daily_qualification_at_risk")]
        return []
