# src/magic_agent/api.py
"""Read-only FastAPI for the mission-control dashboard. Two endpoints: /api/status
(injected snapshot fn) and /api/decisions (last N from the JSONL decision log). No
trading — strictly read. A /ws/decisions live stream is an optional enhancement."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI


def create_app(*, log_path: str | Path, status_fn: Callable[[], dict[str, Any]]) -> FastAPI:
    app = FastAPI(title="magic-agent mission control", docs_url=None)
    path = Path(log_path)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return status_fn()

    @app.get("/api/decisions")
    def decisions(take: int = 100) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(ln) for ln in lines[-take:]]

    return app
