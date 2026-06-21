# src/magic_agent/scanner_gateway.py
"""The scanner -> MIDAS authorization seam.

The scanner is the SOLE setup authority: it owns the authorization decision, the
effective grade, the structural stop / campaign-DOL levels, the QML lifecycle, and
the governing-POI identity. MIDAS *consumes* that authorization and never
reconstructs or recomputes any of it.

``authorized_setup_from_scan`` is the strict mapper: it returns an
:class:`AuthorizedSetup` only for an authorized Long scan that carries every
required field, and otherwise fails closed (``None``) or raises (unsupported
regime). ``ScannerGateway.scan`` is the single entry point used by every runtime
mode (live, paper, replay).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from magic_agent.spot_models import AuthorizedSetup

# Scanner regime values (the canonical hyphenated spellings) mapped onto MIDAS's
# internal ``bias_alignment`` enum. Any other value fails closed (see below).
_BIAS_ALIGNMENT = {
    "aligned": "aligned",
    "counter-bias": "counter_bias",
    "no-bias": "no_bias",
}


def normalize_scanner_regime(value: str) -> str:
    """Map a scanner regime string onto MIDAS's ``bias_alignment`` enum.

    MIDAS must NEVER infer the regime from ``ChecklistInputs`` (which has no
    regime field); the regime is read straight from the scanner result and
    normalized here.

    Args:
        value: The scanner-owned regime string (e.g. ``"counter-bias"``).

    Returns:
        The MIDAS ``bias_alignment`` value (``"aligned"``, ``"counter_bias"``,
        or ``"no_bias"``).

    Raises:
        ValueError: If ``value`` is not a recognized scanner regime (including
            ``"none"``). Fails closed rather than guessing.
    """
    try:
        return _BIAS_ALIGNMENT[value]
    except KeyError as exc:
        raise ValueError(f"unsupported scanner regime: {value!r}") from exc


def authorized_setup_from_scan(
    result: Any, *, identity_key: str, scanner_commit: str
) -> AuthorizedSetup | None:
    """Map a scanner result onto an :class:`AuthorizedSetup`, or fail closed.

    The scanner owns the setup decision. This mapper returns a setup ONLY when
    the scan is authorized for a Long and carries every required field; in every
    other case (monitor-only, no-trade, wrong direction, or any missing field —
    notably an unresolved campaign DOL or a missing structural stop) it returns
    ``None``. The grade is the scanner's ``effective_grade``; ``raw_grade`` and
    ``grade_promotion_reason`` are carried through as immutable audit provenance
    (MIDAS must not recompute the grade). The scanner's QML lifecycle disposition
    is preserved as-is — MIDAS never revives a rejected/superseded QML.

    Args:
        result: The scanner result object (duck-typed).
        identity_key: The MIDAS identity key for the scanned pair.
        scanner_commit: The scanner commit SHA, recorded for replay provenance.

    Returns:
        The mapped :class:`AuthorizedSetup`, or ``None`` when not authorized or
        when any required field is missing.

    Raises:
        ValueError: If the scanner regime is unsupported (fails closed).
    """
    auth = result.authorization
    levels = result.levels
    entry = result.entry
    if auth is None or auth.state != "authorized" or auth.authorized_direction != "Long":
        return None
    if (
        entry is None or entry.entry is None or entry.qml_id is None
        or auth.governing_poi is None or levels is None
        or levels.stop is None or levels.campaign_dol is None
        or not auth.raw_grade or not auth.effective_grade
    ):
        return None
    bias_alignment = normalize_scanner_regime(result.result.regime)
    return AuthorizedSetup(
        setup_id=f"{identity_key}:{entry.qml_reclaim_time.isoformat()}",
        identity_key=identity_key,
        symbol=result.symbol,
        grade=auth.effective_grade,
        raw_grade=auth.raw_grade,
        grade_promotion_reason=auth.grade_promotion_reason,
        entry=Decimal(str(entry.entry)),
        structural_stop=Decimal(str(levels.stop.level)),
        stop_source=levels.stop.source,
        stop_anchor=Decimal(str(levels.stop.anchor)),
        stop_anchor_bar=levels.stop.anchor_bar,
        campaign_dol=Decimal(str(levels.campaign_dol.level)),
        campaign_dol_source=levels.campaign_dol.source,
        checklist_dol=result.inputs.draw_on_liquidity,
        qml_id=entry.qml_id,
        qml_state=entry.qml_state,
        governing_poi_id=(
            f"12h:{auth.governing_poi.kind}:{auth.governing_poi.origin_bar}:"
            f"{auth.governing_poi.bottom:g}:{auth.governing_poi.top:g}"
        ),
        governing_poi_timeframe="12h",
        bias_alignment=bias_alignment,
        scanner_commit=scanner_commit,
        observed_at=entry.qml_reclaim_time.isoformat(),
    )


class ScannerGateway:
    """The single scanner entry point shared by every runtime mode.

    Live/paper omit ``frames`` and pull the latest closed frames from the
    injected ``frame_source``; replay supplies its causal
    ``HistoricalFrameAdapter.as_of(...)`` slice via ``frames``. Every mode runs
    the scanner in the same Track-1 aggressive, Long-only configuration and
    returns the mapped setup directly.
    """

    def __init__(self, *, registry: Any, frame_source: Any, scanner_commit: str) -> None:
        """Initialize the gateway.

        Args:
            registry: Identity registry resolving a candidate's contract key to
                its market-data symbol.
            frame_source: Closed-frame source for live/paper modes.
            scanner_commit: The pinned scanner commit SHA recorded on each setup.
        """
        self.registry = registry
        self.frame_source = frame_source
        self.scanner_commit = scanner_commit

    def scan(self, candidate: Any, frames: Any = None) -> AuthorizedSetup | None:
        """Scan a candidate and return its authorized setup, or ``None``.

        Args:
            candidate: The pair candidate (carries an ``identity_key``).
            frames: Causal frames for replay; ``None`` in live/paper, where the
                injected closed-frame source is used instead.

        Returns:
            The mapped :class:`AuthorizedSetup`, or ``None`` when the scanner
            does not authorize a setup.
        """
        from magic_scanner.scan import scan_pair  # the sole scanner import

        identity = self.registry.by_contract_key(candidate.identity_key)
        selected_frames = frames if frames is not None else self.frame_source.closed_frames(candidate)
        result = scan_pair(
            identity.market_data_symbol, selected_frames,
            execution_mode="track1_aggressive", allowed_side="Long",
        )
        return authorized_setup_from_scan(
            result, identity_key=candidate.identity_key, scanner_commit=self.scanner_commit,
        )
