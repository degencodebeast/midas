# src/magic_agent/setup_view.py
"""Adapt a scanner ``ScanResult`` into the agent's ``Setup``.

Stop derivation: the scanner's ``EntrySetup`` exposes ``entry`` + ``qml_key_level``
(the protective level) but no explicit stop. The agent places the stop just beyond
``qml_key_level`` (``stop_buffer_pct``) and the target at ``min_rr`` × risk. Typed
loosely (duck-typed) so this module doesn't import the scanner's concrete classes.
"""
from __future__ import annotations

from typing import Any

from magic_agent.models import Setup, Side

_DIRECTION = {"Long": Side.LONG, "Short": Side.SHORT}


def from_scan_result(
    scan: Any,
    *,
    stop_buffer_pct: float = 0.005,
    min_rr: float = 3.0,
) -> Setup | None:
    side = _DIRECTION.get(getattr(scan.inputs, "trade_direction", "—"))
    entry_report = getattr(scan, "entry", None)
    if side is None or entry_report is None:
        return None
    entry = getattr(entry_report, "entry", None)
    qml = getattr(entry_report, "qml_key_level", None)
    if entry is None or qml is None:
        return None

    if side is Side.LONG:
        stop = qml * (1.0 - stop_buffer_pct)
        take_profit = entry + min_rr * (entry - stop)
    else:
        stop = qml * (1.0 + stop_buffer_pct)
        take_profit = entry - min_rr * (stop - entry)

    return Setup(
        symbol=scan.symbol,
        direction=side,
        rating=scan.result.rating,
        regime=str(scan.result.regime),
        entry=float(entry),
        stop_loss=float(stop),
        take_profit=float(take_profit),
        confirmation_kind=getattr(scan, "confirmation_kind", "none"),
    )
