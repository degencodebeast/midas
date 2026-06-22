"""The production ``RuntimeState`` the spot loop reads each cycle.

``runner.run_cycle`` touches the shared state object through exactly four members:

* ``state.blocks_new_exposure`` — a bool gate set by the recovery layer (Task C)
  when an unfinished execution is detected; while ``True`` the loop processes
  protective exits but books no new exposure.
* ``state.canary_mode`` — a bool that keeps the first live orders on the 0.25%
  canary risk fraction until an explicit promotion flips it ``False``.
* ``state.risk_state()`` — builds the frozen :class:`PortfolioRiskState` snapshot
  the :class:`RiskPolicy` sizes against, from the tracked portfolio fields.
* ``state.as_dict()`` — a JSON-serialisable mapping handed to
  ``state_journal.save`` so the session survives a restart; :meth:`from_dict`
  reconstructs it byte-for-byte.

Money is carried as :class:`~decimal.Decimal` end to end. Persistence encodes the
Decimals as strings and decodes them back, so a value such as ``Decimal("0.1")``
round-trips exactly with no binary-float drift.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from magic_agent.risk_policy import PortfolioRiskState

# The money-valued fields, persisted/restored as exact decimal strings.
_DECIMAL_FIELDS: tuple[str, ...] = (
    "equity_usd",
    "cash_usd",
    "peak_equity_usd",
    "daily_anchor_usd",
    "open_stressed_loss_usd",
    "correlation_bucket_stressed_loss_usd",
)
_INT_FIELDS: tuple[str, ...] = ("open_strategy_positions", "consecutive_stops")
_BOOL_FIELDS: tuple[str, ...] = (
    "equity_fresh",
    "reconciled_position",
    "canary_mode",
    "blocks_new_exposure",
    "canary_completed",
    "auto_promote_after_canary",
)
_OPTIONAL_STRING_FIELDS: tuple[str, ...] = (
    "canary_intent_id",
    "canary_reconciled_at",
    "promotion_reason",
    "normal_scoring_started_at",
)


@dataclass
class RuntimeState:
    """Mutable per-session state feeding the spot loop's risk snapshot.

    The portfolio fields mirror :class:`PortfolioRiskState`; the two flags
    (``canary_mode``, ``blocks_new_exposure``) are loop-control gates.
    """

    equity_usd: Decimal
    cash_usd: Decimal
    peak_equity_usd: Decimal
    daily_anchor_usd: Decimal
    open_stressed_loss_usd: Decimal = Decimal("0")
    correlation_bucket_stressed_loss_usd: Decimal = Decimal("0")
    open_strategy_positions: int = 0
    consecutive_stops: int = 0
    equity_fresh: bool = True
    reconciled_position: bool = False
    canary_mode: bool = True
    blocks_new_exposure: bool = False
    canary_completed: bool = False
    canary_intent_id: str | None = None
    canary_reconciled_at: str | None = None
    promotion_reason: str | None = None
    normal_scoring_started_at: str | None = None
    cost_viability_evidence: dict[str, Any] | None = None
    auto_promote_after_canary: bool = True
    # The run mode that WROTE this state ("paper"/"twak"/None). Persisted so a
    # consumer can detect a cross-mode/contaminated state file (e.g. a paper-written
    # state.json restored into a live session) and re-baseline off real money rather
    # than inherit the wrong book size. Excluded from equality (compare=False) so it
    # never perturbs the existing round-trip/equality contract; it IS persisted.
    mode: str | None = field(default=None, compare=False)
    # Live source for the open-position count, wired by ``build_app`` to the
    # position manager's reconcile book so a booked position is visible to the
    # next cycle's concurrency cap and a close/exit decrements it. Excluded from
    # init, repr, equality, and persistence: it is a runtime wiring hook, not
    # session state. When ``None`` the stored ``open_strategy_positions`` int is
    # used (the persisted/restored value and the existing-test default).
    open_positions: Callable[[], int] | None = field(
        default=None, init=False, repr=False, compare=False,
    )

    @classmethod
    def new_session(cls, starting_equity: Decimal, *, mode: str | None = None) -> "RuntimeState":
        """Build a fresh session from a starting equity.

        Peak and daily anchor seed to the starting equity, cash equals equity
        (no open positions), there are no stops, equity is fresh, the canary is
        armed, and new exposure is unblocked. ``mode`` tags which run wrote it.
        """
        if not isinstance(starting_equity, Decimal):
            raise TypeError("starting_equity must be a Decimal, not float")
        if starting_equity < 0:
            raise ValueError("starting_equity must not be negative")
        return cls(
            equity_usd=starting_equity,
            cash_usd=starting_equity,
            peak_equity_usd=starting_equity,
            daily_anchor_usd=starting_equity,
            mode=mode,
        )

    @classmethod
    def new_live_session(cls, equity_usd: Decimal, cash_usd: Decimal, *, mode: str | None = None) -> "RuntimeState":
        """Build a fresh LIVE session from the real wallet's equity and cash.

        Mirrors :meth:`new_session` (peak + daily anchor seed to ``equity_usd``, no
        stops, equity fresh, canary armed, exposure unblocked) but takes a DISTINCT
        ``cash_usd``: in live, the deployable stable (USDC) is typically less than
        total equity (which also includes the native gas coin's USD), so cash must
        not be forced equal to equity the way ``new_session`` does.
        """
        for name, value in (("equity_usd", equity_usd), ("cash_usd", cash_usd)):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal, not float")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        return cls(
            equity_usd=equity_usd,
            cash_usd=cash_usd,
            peak_equity_usd=equity_usd,
            daily_anchor_usd=equity_usd,
            mode=mode,
        )

    def risk_state(self) -> PortfolioRiskState:
        """Project the tracked fields into the frozen risk snapshot.

        ``open_strategy_positions`` reflects the live open-position count from the
        wired :attr:`open_positions` provider when present (the reconcile book), so
        a position booked this cycle is visible to the next cycle's concurrency cap;
        otherwise it falls back to the stored integer.
        """
        if self.open_positions is not None:
            open_strategy_positions = self.open_positions()
        else:
            open_strategy_positions = self.open_strategy_positions
        return PortfolioRiskState(
            equity_usd=self.equity_usd,
            cash_usd=self.cash_usd,
            peak_equity_usd=self.peak_equity_usd,
            daily_anchor_usd=self.daily_anchor_usd,
            open_stressed_loss_usd=self.open_stressed_loss_usd,
            correlation_bucket_stressed_loss_usd=self.correlation_bucket_stressed_loss_usd,
            open_strategy_positions=open_strategy_positions,
            consecutive_stops=self.consecutive_stops,
            equity_fresh=self.equity_fresh,
            reconciled_position=self.reconciled_position,
        )

    def live_mode_state(self) -> dict:
        """Project canary/scoring mode into the status/dashboard contract."""
        if self.blocks_new_exposure:
            mode = "halted_review"
        elif self.canary_mode:
            mode = "canary"
        else:
            mode = "normal_scoring"
        return {
            "mode": mode,
            "canary_required": self.canary_mode,
            "canary_completed": self.canary_completed,
            "canary_intent_id": self.canary_intent_id,
            "canary_reconciled_at": self.canary_reconciled_at,
            "promotion_reason": self.promotion_reason,
            "normal_scoring_started_at": self.normal_scoring_started_at,
            "cost_viability_evidence": self.cost_viability_evidence,
        }

    def as_dict(self) -> dict:
        """A JSON-serialisable mapping; Decimals are encoded as exact strings."""
        payload: dict = {name: str(getattr(self, name)) for name in _DECIMAL_FIELDS}
        for name in _INT_FIELDS:
            payload[name] = getattr(self, name)
        for name in _BOOL_FIELDS:
            payload[name] = getattr(self, name)
        for name in _OPTIONAL_STRING_FIELDS:
            payload[name] = getattr(self, name)
        payload["cost_viability_evidence"] = self.cost_viability_evidence
        # Mode tag: lets a consumer detect a cross-mode/contaminated state file.
        payload["mode"] = self.mode
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "RuntimeState":
        """Reconstruct a :class:`RuntimeState` from :meth:`as_dict` output."""
        kwargs: dict = {name: Decimal(data[name]) for name in _DECIMAL_FIELDS}
        for name in _INT_FIELDS:
            kwargs[name] = int(data[name])
        for name in _BOOL_FIELDS:
            if name in data:
                kwargs[name] = bool(data[name])
        for name in _OPTIONAL_STRING_FIELDS:
            kwargs[name] = data.get(name)
        kwargs["cost_viability_evidence"] = data.get("cost_viability_evidence")
        # Older state files predate the mode tag -> None (treated as "unknown mode",
        # which the live re-baseline path conservatively discards as cross-mode).
        kwargs["mode"] = data.get("mode")
        return cls(**kwargs)
