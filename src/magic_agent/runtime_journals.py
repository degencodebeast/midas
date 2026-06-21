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


class DecisionJournal:
    """Append-only JSONL writer for ``PipelineDecision`` records.

    Each call to :meth:`append` writes one JSON line containing the
    decision's ``action``, ``reason``, ``reason_codes`` (list),
    ``exit_quantity`` (string for Decimal exactness), ``intent_id``
    (string when present, ``null`` when ``intent`` is ``None``),
    ``symbol`` (from ``intent.setup.symbol`` when present, else
    ``null``), and ``observed_at``.
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
            now: Observation timestamp; serialized as ``isoformat()``.
        """
        intent_id: str | None = None
        symbol: str | None = None
        if decision.intent is not None:
            intent_id = decision.intent.intent_id
            symbol = decision.intent.setup.symbol

        record = {
            "action": decision.action,
            "exit_quantity": str(decision.exit_quantity),
            "intent_id": intent_id,
            "observed_at": now.isoformat(),
            "reason": decision.reason,
            "reason_codes": list(decision.reason_codes),
            "symbol": symbol,
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
