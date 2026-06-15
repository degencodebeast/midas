# tests/test_api.py
import json
from fastapi.testclient import TestClient
from magic_agent.api import create_app


def _seed(tmp_path):
    p = tmp_path / "decisions.jsonl"
    p.write_text("\n".join(json.dumps({"ts": f"t{i}", "action": "enter_long",
                  "qty": i, "baseline_qty": i, "outcome": "opened"}) for i in range(5)) + "\n")
    return p


def test_status_endpoint(tmp_path):
    app = create_app(log_path=_seed(tmp_path),
                     status_fn=lambda: {"mode": "paper", "equity": 1000.0, "halted": False})
    c = TestClient(app)
    r = c.get("/api/status")
    assert r.status_code == 200 and r.json()["mode"] == "paper"


def test_decisions_returns_last_n(tmp_path):
    app = create_app(log_path=_seed(tmp_path), status_fn=lambda: {})
    c = TestClient(app)
    r = c.get("/api/decisions?take=2")
    rows = r.json()
    assert len(rows) == 2 and rows[-1]["ts"] == "t4"  # newest last
