from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class AgentNarrativeJournal:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, event: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

    def records(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip()]


def live_entry_reconciled_event(
    *,
    now: datetime,
    setup,
    risk,
    mode: str,
    execution_state: str,
    tx_hash: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "ts": now.isoformat(),
        "event": "live_entry_reconciled",
        "symbol": setup.symbol,
        "scanner": {
            "authorized": True,
            "raw_grade": setup.raw_grade,
            "effective_grade": setup.grade,
            "entry": str(setup.entry),
            "structural_stop": str(setup.structural_stop),
            "campaign_dol": str(setup.campaign_dol),
        },
        "risk": {
            "mode": mode,
            "risk_fraction": str(risk.risk_fraction),
            "final_qty": str(risk.final_qty),
            "denied_by": [] if risk.denied_by is None else [risk.denied_by],
        },
        "execution": {
            "signer": "twak",
            "quote_provider": "twak",
            "state": execution_state,
            "tx_hash": tx_hash,
        },
        "reason": reason,
    }
