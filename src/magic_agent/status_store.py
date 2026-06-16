"""Cross-process live-status snapshot store (F4).

``run`` and ``serve`` are separate processes; they share the latest live status
via a single JSON file under ``.magic_agent/``. ``run`` writes a fresh
``build_status`` snapshot each candle; ``serve`` reads it for ``/api/status``.

The write is atomic-ish (write a temp file in the same dir, then ``os.replace``)
so ``serve`` never reads a half-written file. The read returns ``None`` when the
file is absent or unreadable (corrupt / partial), so callers fall back gracefully.
Stdlib only — no new deps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_status_snapshot(path: str | os.PathLike[str], status: dict) -> None:
    """Atomically write ``status`` (a JSON-able dict) to ``path``.

    Creates parent dirs as needed. Writes to a sibling temp file then
    ``os.replace``s it into place, so a concurrent reader sees either the old
    file or the fully-written new one — never a partial write.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(status), encoding="utf-8")
    os.replace(tmp, p)


def read_status_snapshot(path: str | os.PathLike[str]) -> dict | None:
    """Return the parsed snapshot dict, or ``None`` if the file is missing or
    unreadable (absent / corrupt / not valid JSON)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        return json.loads(text)
    except (OSError, ValueError):
        return None
