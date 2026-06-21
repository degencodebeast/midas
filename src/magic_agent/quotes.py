"""Deterministic paper-mode quote source + the paper executability adapter.

``app.executability`` in paper mode is :class:`ExecutabilityAdapter` wrapping
:class:`PaperQuoteProvider`. The provider is a NO-NETWORK, NO-FUNDS quote source:
it prices both legs of the round trip off the authorized ``setup.entry`` (the
authorized entry *is* the deterministic paper mid — there is no external price
frame), stamps a fresh TTL-bounded ``expires_at``, and reuses the real
:func:`validate_round_trip` from :mod:`magic_agent.executability` for the
``approved`` / ``reasons`` / ``quantity_caps`` surface rather than reimplementing
validation. It plugs into :func:`prepare_exact_order` as its ``quote_provider``
callable: ``provider(setup, None)`` is the capacity probe and
``provider(setup, qty)`` returns the exact, risk-sized quote.

The live port (TWAK-backed quote provider, real routes + funds) is OUT OF SCOPE
here — see ``magic_agent.twak`` for the live execution surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from magic_agent.executability import (
    PreparedOrder,
    prepare_exact_order,
    validate_round_trip,
)
from magic_agent.risk_policy import QuantityCaps

# Paper microstructure constants — small, deterministic frictions.
_PROVIDER = "paper"
_TTL = timedelta(minutes=1)
_DEFAULT_PROBE_QTY = Decimal("1")
_IMPACT_BPS = Decimal("5")
_SLIPPAGE_BPS = Decimal("10")
# Worst-case fill on the requested quantity, expressed as a fraction of qty.
_MINIMUM_OUTPUT_FRACTION = (Decimal("10000") - _SLIPPAGE_BPS) / Decimal("10000")


@dataclass(frozen=True)
class _PaperProbeResult:
    """The ``quote_provider`` contract surface expected by ``prepare_exact_order``."""

    approved: bool
    reasons: tuple[str, ...]
    quantity_caps: QuantityCaps
    quote: dict | None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class PaperQuoteProvider:
    """Deterministic paper quote source — callable as ``provider(setup, qty)``.

    ``now`` is the injected clock that stamps each quote (``expires_at = now + TTL``);
    no wall-clock is read inside, so quote freshness is fully deterministic.
    ``valid_at`` overrides the moment the round trip is *validated* against (defaults
    to ``now``); pushing it past the TTL window deterministically expires the quotes,
    which makes the provider fail closed.
    """

    def __init__(self, *, now: str, valid_at: str | None = None) -> None:
        self._now = _parse_iso(now)
        self._valid_at = valid_at if valid_at is not None else now

    def __call__(self, setup, quantity: Decimal | None) -> _PaperProbeResult:
        qty = _DEFAULT_PROBE_QTY if quantity is None else quantity
        if qty <= 0:
            return _PaperProbeResult(False, ("nonpositive_quote_quantity",), QuantityCaps.unbounded(), None)
        buy = self._leg(setup, qty)
        sell = self._leg(setup, qty)
        decision = validate_round_trip(buy, sell, now=self._valid_at)
        quote = buy if (decision.approved and quantity is not None) else None
        return _PaperProbeResult(
            decision.approved, decision.reasons, decision.quantity_caps, quote,
        )

    def _leg(self, setup, qty: Decimal) -> dict:
        # Priced off the authorized entry (the paper mid). Money stays Decimal;
        # only the round-trip dict carries string-serialized values.
        entry = setup.entry
        minimum_output = qty * _MINIMUM_OUTPUT_FRACTION
        expires_at = self._now + _TTL
        return {
            "output_qty": str(qty),
            "provider": _PROVIDER,
            "minimum_output": str(minimum_output),
            "impact_bps": str(_IMPACT_BPS),
            "slippage_bps": str(_SLIPPAGE_BPS),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            "price": str(entry),
            "symbol": setup.symbol,
        }


class ExecutabilityAdapter:
    """Paper-mode ``app.executability``: sizes via RiskPolicy + binds a paper quote."""

    def __init__(self, quote_provider: PaperQuoteProvider) -> None:
        self._quote_provider = quote_provider

    def prepare_order(self, *, setup, market, risk_state, risk_policy) -> PreparedOrder:
        return prepare_exact_order(
            setup=setup,
            market=market,
            risk_state=risk_state,
            risk_policy=risk_policy,
            quote_provider=self._quote_provider,
        )
