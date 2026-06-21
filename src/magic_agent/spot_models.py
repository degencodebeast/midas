from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Literal


class ActionPurpose(str, Enum):
    STRATEGY = "strategy"
    RISK_EXIT = "risk_exit"
    COMPLIANCE = "compliance"
    X402 = "x402"


@dataclass(frozen=True)
class AuthorizedSetup:
    setup_id: str
    identity_key: str
    symbol: str
    # Scanner-owned effective grade used by RiskPolicy.
    grade: str
    # Frozen engine grade retained for audit/replay parity.
    raw_grade: str
    grade_promotion_reason: str | None
    entry: Decimal
    structural_stop: Decimal
    stop_source: str
    stop_anchor: Decimal
    stop_anchor_bar: int
    campaign_dol: Decimal
    campaign_dol_source: str
    checklist_dol: bool
    qml_id: str
    qml_state: Literal["active", "protected", "unknown"]
    governing_poi_id: str
    governing_poi_timeframe: str
    bias_alignment: Literal["aligned", "counter_bias", "no_bias"]
    scanner_commit: str
    observed_at: str

    def __post_init__(self) -> None:
        if self.campaign_dol is None:
            raise ValueError("campaign DOL is required")
        if not self.structural_stop < self.entry < self.campaign_dol:
            raise ValueError("spot-long geometry must be stop < entry < campaign DOL")

    @classmethod
    def example(cls, **changes: object) -> "AuthorizedSetup":
        base = cls(
            "setup-1", "zec-bsc", "ZEC/USDT", "B", "B", None, Decimal("100"),
            Decimal("90"), "h12_pivot", Decimal("91"), 12,
            Decimal("120"), "prior_week", False,
            "qml-1", "active", "h12-poi-1", "12h", "aligned",
            "scanner-sha", "2026-06-21T00:00:00Z",
        )
        return replace(base, **changes)


@dataclass(frozen=True)
class SpotIntent:
    intent_id: str
    setup: AuthorizedSetup
    quantity: Decimal
    side: str
    purpose: ActionPurpose

    def __post_init__(self) -> None:
        expected = "buy" if self.purpose is ActionPurpose.STRATEGY else "sell"
        if self.side != expected or self.quantity <= 0:
            raise ValueError("spot-long intent has invalid side or quantity")
