"""Append-only JSONL journals for the spot runtime loop.

``ExclusionJournal`` records symbols excluded during each cycle —
from CMC fetches, candidate enumeration, or ad-hoc runtime checks.
``DecisionJournal`` records the ``PipelineDecision`` produced for each
candidate that reaches the decision pipeline.

Both journals write one JSON record per line (JSONL), use
``sort_keys=True`` for deterministic field order, and use
``separators=(",", ":")`` for compact serialization. Appends are
atomic at the OS level: each line is flushed to the file handle in a
single ``write`` call; the file is opened in append mode so no prior
content is overwritten (no torn-write risk for individual lines).

``now`` parameters must be ``datetime`` objects; they are serialized
via ``isoformat()``.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magic_agent.candidate_source import CandidateExclusion
    from magic_agent.cmc_source import CmcExclusion
    from magic_agent.decision_pipeline import PipelineDecision


def _dumps(obj: dict) -> str:
    """Serialize *obj* to a compact, deterministic JSON string."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class ExclusionJournal:
    """Append-only JSONL writer for per-cycle exclusion records.

    Three flavours of record can be appended:

    * **cmc** — a :class:`~magic_agent.cmc_source.CmcExclusion` produced
      by ``CmcCandidateSource.snapshot``; fields: ``eligibility_id``,
      ``symbol``, ``reason_code``, ``source="cmc"``, ``observed_at``.
    * **candidate** — a
      :class:`~magic_agent.candidate_source.CandidateExclusion` produced
      by ``CandidateSource.enumerate``; same fields, ``source="candidate"``.
    * **runtime** — an ad-hoc ``(symbol, reason_code)`` pair appended via
      :meth:`append_code`; no ``eligibility_id``, ``source="runtime"``.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    # ------------------------------------------------------------------
    # Public API (matches runner.run_cycle call-sites)
    # ------------------------------------------------------------------

    def append_many(
        self,
        rows: tuple,  # tuple[CmcExclusion | CandidateExclusion, ...]
        now: datetime,
    ) -> None:
        """Append one JSONL record per entry in *rows*.

        Rows may be any mix of
        :class:`~magic_agent.cmc_source.CmcExclusion` and
        :class:`~magic_agent.candidate_source.CandidateExclusion`.
        An empty tuple is a no-op (the file is not created/touched).

        Args:
            rows: Exclusion rows to persist.
            now: Observation timestamp; serialized as ``isoformat()``.
        """
        if not rows:
            return
        lines = [self._serialise_row(row, now) for row in rows]
        self._append_lines(lines)

    def append_code(self, symbol: str, reason: str, now: datetime) -> None:
        """Append a single ad-hoc runtime exclusion record.

        Used for inline guards such as ``identity_not_gold`` and
        ``cmc_stale_or_vetoed`` that are not backed by a structured
        exclusion dataclass.

        Args:
            symbol: The token symbol being excluded.
            reason: The reason code string.
            now: Observation timestamp; serialized as ``isoformat()``.
        """
        record = {
            "observed_at": now.isoformat(),
            "reason_code": reason,
            "source": "runtime",
            "symbol": symbol,
        }
        self._append_lines([_dumps(record)])

    def records(self) -> list[dict]:
        """Return all records written to this journal as parsed dicts.

        Returns an empty list if the backing file does not yet exist.
        """
        if not self._path.exists():
            return []
        return [
            json.loads(line)
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _serialise_row(self, row: object, now: datetime) -> str:
        """Dispatch to the correct serialiser based on the row's type."""
        # Import here to avoid circular imports; these are lightweight dataclasses.
        from magic_agent.candidate_source import CandidateExclusion
        from magic_agent.cmc_source import CmcExclusion

        if isinstance(row, CmcExclusion):
            record = {
                "eligibility_id": row.eligibility_id,
                "observed_at": now.isoformat(),
                "reason_code": row.reason_code,
                "source": "cmc",
                "symbol": row.symbol,
            }
        elif isinstance(row, CandidateExclusion):
            record = {
                "eligibility_id": row.eligibility_id,
                "observed_at": now.isoformat(),
                "reason_code": row.reason_code,
                "source": "candidate",
                "symbol": row.symbol,
            }
        else:
            raise TypeError(
                f"ExclusionJournal.append_many: unexpected row type {type(row).__name__!r}"
            )
        return _dumps(record)

    def _append_lines(self, lines: list[str]) -> None:
        """Atomically append *lines* to the backing JSONL file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")


def _to_float(value: Decimal | None) -> float | None:
    """Convert a Decimal display value to a JSON number (``None`` passes through).

    This is a *display* projection for the dashboard, not money math: the
    string round-trip (``float(str(value))``) keeps the shortest exact decimal
    representation without re-introducing binary float artefacts beyond the
    final ``float`` cast.
    """
    if value is None:
        return None
    return float(str(value))


class DecisionJournal:
    """Append-only JSONL writer for ``PipelineDecision`` records.

    Each call to :meth:`append` writes one JSON line. The record carries two
    overlapping families of fields:

    * **Back-compat audit fields** — ``action``, ``reason``,
      ``reason_codes`` (list), ``exit_quantity`` (string for Decimal
      exactness), ``intent_id`` (string when present, ``null`` when
      ``intent`` is ``None``), ``symbol`` (from ``intent.setup.symbol``
      when present, else ``null``), and ``observed_at``.
    * **Dashboard-renderable fields** — the exact field NAMES the frontend
      ``Decision`` interface reads (``ts``, ``setup_ref``, ``allow``,
      ``qty``, ``entry``, ``stop_loss``, ``take_profit``, ``regime``,
      ``gate_reason``, ``reasoning``) plus scanner provenance
      (``raw_grade``, ``effective_grade``, ``grade_promotion_reason``).
      Display numbers are JSON numbers (Decimal projected to float); setup-
      derived fields are ``null`` for non-entry/hold decisions.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, decision: PipelineDecision, now: datetime) -> None:
        """Append one JSONL record for *decision*.

        Args:
            decision: The resolved :class:`~magic_agent.decision_pipeline.PipelineDecision`.
            now: Observation timestamp; serialized as ``isoformat()`` (emitted
                under both ``observed_at`` and the frontend's ``ts``).
        """
        intent_id: str | None = None
        symbol: str | None = None
        setup_ref: str | None = None
        qty: float | None = None
        entry: float | None = None
        stop_loss: float | None = None
        take_profit: float | None = None
        regime: str | None = None
        raw_grade: str | None = None
        effective_grade: str | None = None
        grade_promotion_reason: str | None = None

        if decision.intent is not None:
            intent = decision.intent
            setup = intent.setup
            intent_id = intent.intent_id
            symbol = setup.symbol
            # Prefer the structural setup id; fall back to the QML id.
            setup_ref = setup.setup_id or setup.qml_id
            qty = _to_float(intent.quantity)
            entry = _to_float(setup.entry)
            stop_loss = _to_float(setup.structural_stop)
            take_profit = _to_float(setup.campaign_dol)
            regime = setup.bias_alignment
            raw_grade = setup.raw_grade
            effective_grade = setup.grade
            grade_promotion_reason = setup.grade_promotion_reason

        timestamp = now.isoformat()
        reasoning = ", ".join((decision.reason, *decision.reason_codes))

        record = {
            # Back-compat audit fields.
            "action": decision.action,
            "exit_quantity": str(decision.exit_quantity),
            "intent_id": intent_id,
            "observed_at": timestamp,
            "reason": decision.reason,
            "reason_codes": list(decision.reason_codes),
            "symbol": symbol,
            # Dashboard-renderable fields (frontend Decision interface names).
            "allow": decision.intent is not None,
            "effective_grade": effective_grade,
            "entry": entry,
            "gate_reason": decision.reason,
            "grade_promotion_reason": grade_promotion_reason,
            "qty": qty,
            "raw_grade": raw_grade,
            "reasoning": reasoning,
            "regime": regime,
            "setup_ref": setup_ref,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "ts": timestamp,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(_dumps(record) + "\n")

    def records(self) -> list[dict]:
        """Return all records written to this journal as parsed dicts.

        Returns an empty list if the backing file does not yet exist.
        """
        if not self._path.exists():
            return []
        return [
            json.loads(line)
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
