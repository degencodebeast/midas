"""UI ↔ status contract guard (Phase 1.7 — Task P1.7-2).

This test PARSES ``web/app/page.tsx`` (no node, no network) and pins the contract
between what the dashboard READS off the ``/api/status`` payload and what the
Python backend EMITS. It auto-fails on drift: if a future ``page.tsx`` edit reads
a ``status.<field>`` the backend never emits, or a P1.7-1 field is dropped from
``build_status``, this suite goes RED and names the missing field.

Two emit-sources are guarded against the SAME read-set:
  1. ``build_status(...)`` — the live snapshot writer (the real contract).
  2. ``build_serve_app(...)`` demo fallback — the hand-written dict returned by
     the default ``/api/status`` provider when no snapshot exists. This is the
     drift that the Phase 1.7 review flagged, so it gets its own assertion.

Direction asserted: ``read_set − allowlist ⊆ emit_set``. The inverse is NOT
required — the backend may emit extra fields the UI ignores.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from magic_agent.cli import build_serve_app
from magic_agent.executor import PaperExecutor
from magic_agent.models import Action, ExecutionIntent
from magic_agent.status import build_status

# ── locations ────────────────────────────────────────────────────────────────

# tests/ ‹—— repo root ——› web/app/page.tsx
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PAGE_TSX = _REPO_ROOT / "web" / "app" / "page.tsx"


# ── allowlists (explicit, documented) ────────────────────────────────────────

# Top-level ``status.<field>`` reads the UI performs that are intentionally NOT
# sourced from ``build_status``. None expected today — every status field the UI
# reads is emitted by build_status. Add here (WITH a reason) only if a future
# read is a genuine UI-internal/derived value, not a backend contract field.
UI_DERIVED_ALLOWLIST: set[str] = set()

# The per-position fields the UI reads off each ``positions[i]`` object. Taken
# from the ``Position`` interface / ``PositionsPanel`` table in page.tsx. The
# rendering uses ``p.qty ?? p.size`` (one OR the other is enough), so ``size`` is
# tolerated as an alternative and not required to be present in the emit-set.
POSITION_ALLOWLIST: set[str] = {"size"}


# ── UI read-set extraction (regex over page.tsx text) ────────────────────────

# Matches ``status.foo``, ``status?.foo`` and ``status["foo"]`` / ``status['foo']``.
# A leading negative-lookbehind for an identifier char ensures we don't match
# ``status`` embedded in a longer identifier (e.g. ``statusOk``, ``setStatus``,
# ``StatusData``). The trailing ``\b`` after the dotted id avoids partial words.
# LIMITATION: destructuring reads (``const { halted } = status``) are NOT matched
# — page.tsx uses none today (every read is ``status?.x``), and a wholesale switch
# would trip ``test_read_set_extraction_sanity`` (which pins known fields as
# must-be-present) rather than silently shrink the read-set. If page.tsx adopts
# destructuring, extend this extractor to cover it.
_STATUS_DOT = re.compile(r"(?<![A-Za-z0-9_$.])status\s*\??\.\s*([A-Za-z_$][\w$]*)")
_STATUS_IDX = re.compile(r"(?<![A-Za-z0-9_$.])status\s*\[\s*[\"']([^\"']+)[\"']\s*\]")


def _read_page_source() -> str:
    assert _PAGE_TSX.exists(), f"page.tsx not found at {_PAGE_TSX}"
    return _PAGE_TSX.read_text(encoding="utf-8")


def _extract_status_read_set(src: str) -> set[str]:
    """Top-level ``status`` field names the UI reads."""
    fields = set(_STATUS_DOT.findall(src)) | set(_STATUS_IDX.findall(src))
    return fields


# ── emit-set construction (call the real backend) ────────────────────────────

def _representative_emit() -> dict:
    """A representative ``build_status`` payload with a non-empty position.

    All P1.7-1 params are passed so the emit-set covers the full contract, and a
    long position is opened so ``positions`` is non-empty (per-position keys are
    only present when not flat).
    """
    executor = PaperExecutor(starting_equity=1000.0)
    executor.open_position(
        ExecutionIntent(
            symbol="BNB/USDT",
            action=Action.ENTER_LONG,
            qty=2.5,
            entry=300.0,
            stop_loss=285.0,
            take_profit=330.0,
            leverage=1.0,
        )
    )
    return build_status(
        executor,
        symbol="BNB/USDT",
        mode="paper",
        venue="binance",
        mark_price=310.0,
        starting_equity=1000.0,
        halted=False,
        daily_loss=12.5,
        max_daily_loss=50.0,
        agent_id="0xDEADBEEFCAFE",
    )


def _demo_fallback_status(tmp_path: Path) -> dict:
    """The hand-written demo dict from ``build_serve_app``'s default provider.

    Obtained the same way the dashboard would: a real GET ``/api/status`` against
    the app with NO snapshot present (absent path) and an empty log. No node, no
    network — ``TestClient`` drives the ASGI app in-process. ``tmp_path`` is
    pytest's per-test temp dir (auto-cleaned; never written into the repo).
    """
    from fastapi.testclient import TestClient

    empty_log = tmp_path / "decisions.jsonl"
    empty_log.write_text("", encoding="utf-8")
    absent_snapshot = tmp_path / "definitely-absent-snapshot.json"  # never created

    app = build_serve_app(log_path=str(empty_log), snapshot_path=str(absent_snapshot))
    with TestClient(app) as client:
        resp = client.get("/api/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("mode") == "demo", f"expected demo fallback, got {body!r}"
    return body


# ── tests ────────────────────────────────────────────────────────────────────

def test_read_set_extraction_sanity():
    """The regex must actually find the known status reads (guards self-rot)."""
    read_set = _extract_status_read_set(_read_page_source())
    # A representative subset the UI demonstrably reads — if extraction silently
    # returns {} (e.g. the regex broke), this catches it so the ⊆ checks below
    # can't pass vacuously.
    for expected in {"halted", "mode", "venue", "agent_id", "daily_loss",
                     "max_daily_loss", "positions", "equity"}:
        assert expected in read_set, (
            f"expected page.tsx to read status.{expected}; "
            f"extraction returned {sorted(read_set)}"
        )
    # Must NOT have matched non-status identifiers (statusOk / StatusData / setStatus).
    for noise in {"Ok", "Data"}:
        assert noise not in read_set, f"regex over-matched: caught {noise!r}"


def test_top_level_reads_subset_of_build_status_emits():
    """Every top-level status field page.tsx reads is emitted by build_status."""
    read_set = _extract_status_read_set(_read_page_source())
    emit_set = set(_representative_emit().keys())

    required = read_set - UI_DERIVED_ALLOWLIST
    missing = sorted(required - emit_set)
    assert not missing, (
        "page.tsx reads status fields that build_status() does not emit: "
        + ", ".join(
            f"page.tsx reads status.{f} but build_status() does not emit it"
            for f in missing
        )
    )


def test_position_reads_subset_of_build_status_position_emits():
    """Per-position fields page.tsx reads are emitted on each position dict."""
    positions = _representative_emit()["positions"]
    assert positions, "fixture must open a position so positions[] is non-empty"
    position_emit_set = set(positions[0].keys())

    # The known read-set off a Position object in page.tsx (the Position interface
    # + PositionsPanel table). Hardcoded per task spec for robustness; ``size`` is
    # an OR-alternative to ``qty`` (allowlisted).
    position_read_set = {
        "symbol", "side", "qty", "entry_price",
        "stop_loss", "take_profit", "pnl", "size",
    }
    required = position_read_set - POSITION_ALLOWLIST
    missing = sorted(required - position_emit_set)
    assert not missing, (
        "page.tsx reads position fields not emitted by build_status: "
        + ", ".join(
            f"page.tsx reads position.{f} but build_status() does not emit it"
            for f in missing
        )
    )


def test_demo_fallback_satisfies_ui_contract(tmp_path):
    """The serve demo fallback covers the same top-level read-set (drift guard).

    This closes the flagged drift: the hand-written demo dict in build_serve_app
    must satisfy the identical UI contract as the real snapshot, so the dashboard
    renders honestly (None placeholders) before `run` writes a snapshot.
    """
    read_set = _extract_status_read_set(_read_page_source())
    demo_keys = set(_demo_fallback_status(tmp_path).keys())

    required = read_set - UI_DERIVED_ALLOWLIST
    missing = sorted(required - demo_keys)
    assert not missing, (
        "serve demo fallback is missing UI-contract fields: "
        + ", ".join(
            f"page.tsx reads status.{f} but the demo fallback does not emit it"
            for f in missing
        )
    )


def test_build_status_and_demo_fallback_agree_on_contract_keys(tmp_path):
    """Both emit-sources cover the read-set — neither may drift independently."""
    read_set = _extract_status_read_set(_read_page_source()) - UI_DERIVED_ALLOWLIST
    emit_set = set(_representative_emit().keys())
    demo_keys = set(_demo_fallback_status(tmp_path).keys())
    assert read_set <= emit_set
    assert read_set <= demo_keys
