# tests/test_cli.py
from magic_agent.cli import build_parser


# ---------------------------------------------------------------------------
# Spot `run` parser: paper is the DEFAULT; twak is the only live executor choice
# ---------------------------------------------------------------------------

def test_run_defaults_to_paper_executor():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT"])
    assert args.symbol == "BNB/USDT"
    assert args.executor == "paper"  # safe default — never live without opt-in


def test_run_accepts_twak_live_executor():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT", "--executor", "twak"])
    assert args.executor == "twak"


def test_run_rejects_unknown_executor():
    import pytest

    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["run", "--executor", "aster"])  # perp venue removed


def test_run_has_no_leverage_or_short_options():
    # Spot longs only: no leverage / short / aster configuration remains.
    import pytest

    p = build_parser()
    args = p.parse_args(["run"])
    assert not hasattr(args, "leverage")
    assert not hasattr(args, "max_leverage")
    # The perp leverage flag no longer parses at all.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--leverage", "5"])


def test_run_subcommand_func_is_set():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT"])
    assert callable(args.func)


# ---------------------------------------------------------------------------
# serve + ASGI entry (dashboard — unchanged across the spot migration)
# ---------------------------------------------------------------------------

def test_serve_parses_port_and_log():
    """serve --port 9000 --log x.jsonl => correct port + log attrs."""
    p = build_parser()
    args = p.parse_args(["serve", "--port", "9000", "--log", "x.jsonl"])
    assert args.port == 9000
    assert args.log == "x.jsonl"


def test_serve_host_default():
    """--host defaults to 0.0.0.0."""
    p = build_parser()
    args = p.parse_args(["serve"])
    assert args.host == "0.0.0.0"


def test_serve_port_default():
    """--port defaults to 8000."""
    p = build_parser()
    args = p.parse_args(["serve"])
    assert args.port == 8000


def test_serve_log_default():
    """--log has a non-empty default path."""
    p = build_parser()
    args = p.parse_args(["serve"])
    assert args.log  # non-empty string


def test_serve_subcommand_func_is_set():
    """serve subcommand sets args.func (dispatches to the serve handler)."""
    p = build_parser()
    args = p.parse_args(["serve"])
    assert callable(args.func)


def test_build_serve_app_status_wired(tmp_path):
    """build_serve_app returns a FastAPI app whose /api/status returns a
    build_status-shaped dict (mode, equity, positions present)."""
    from fastapi.testclient import TestClient
    from magic_agent.cli import build_serve_app

    log_file = tmp_path / "decisions.jsonl"
    log_file.write_text("")  # empty but present

    app = build_serve_app(log_path=str(log_file), status_provider=None)
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert "equity" in data
    assert "positions" in data


def test_build_serve_app_decisions_endpoint(tmp_path):
    """build_serve_app wires /api/decisions to the log file."""
    import json
    from fastapi.testclient import TestClient
    from magic_agent.cli import build_serve_app

    log_file = tmp_path / "decisions.jsonl"
    log_file.write_text(json.dumps({"action": "HOLD"}) + "\n")

    app = build_serve_app(log_path=str(log_file), status_provider=None)
    client = TestClient(app)
    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["action"] == "HOLD"


# ---------------------------------------------------------------------------
# `magic-agent run` log path matches `serve` log path (shared audit trail)
# ---------------------------------------------------------------------------

def test_run_log_default_matches_serve_default():
    """`run --log` default MUST match `serve --log` default so both processes
    read/write the same `.magic_agent/decisions.jsonl`."""
    p = build_parser()
    run_args = p.parse_args(["run"])
    serve_args = p.parse_args(["serve"])
    assert run_args.log == serve_args.log
    assert run_args.log == ".magic_agent/decisions.jsonl"


def test_run_log_overridable(tmp_path):
    p = build_parser()
    log_path = str(tmp_path / "custom.jsonl")
    args = p.parse_args(["run", "--log", log_path])
    assert args.log == log_path


# ---------------------------------------------------------------------------
# serve reads the live status snapshot (run writes, serve reads)
# ---------------------------------------------------------------------------

def test_build_serve_app_reads_seeded_snapshot_not_demo(tmp_path):
    """When a status snapshot file is present, build_serve_app's default provider
    returns THAT snapshot (the live `run` process's state) — NOT a detached demo.
    This is the cross-process fix."""
    import json
    from fastapi.testclient import TestClient
    from magic_agent.cli import build_serve_app

    log_file = tmp_path / "decisions.jsonl"
    log_file.write_text("")
    snapshot_path = tmp_path / "status.json"
    seeded = {
        "mode": "live",
        "venue": "binance",
        "halted": False,
        "equity": 1234.5,
        "available": 1200.0,
        "currency": "USDT",
        "realized_pnl": 200.0,
        "open_pnl": 34.5,
        "positions": [{"symbol": "BNB/USDT", "side": "long", "qty": 2.0, "size": 2.0,
                       "entry_price": 600.0, "stop_loss": 588.0, "take_profit": 636.0,
                       "pnl": 34.5}],
    }
    snapshot_path.write_text(json.dumps(seeded))

    app = build_serve_app(log_path=str(log_file), snapshot_path=str(snapshot_path))
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json() == seeded  # the seeded snapshot, not the demo executor


def test_build_serve_app_absent_snapshot_falls_back_to_demo(tmp_path):
    """No snapshot file present → the default provider returns a clearly-labelled
    demo fallback dict (mode 'demo') so serve still responds before `run` starts."""
    from fastapi.testclient import TestClient
    from magic_agent.cli import build_serve_app

    log_file = tmp_path / "decisions.jsonl"
    log_file.write_text("")
    snapshot_path = tmp_path / "status.json"  # not created

    app = build_serve_app(log_path=str(log_file), snapshot_path=str(snapshot_path))
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "demo"
    assert "equity" in data
    assert "positions" in data


def test_cli_twak_mode_passes_executor_to_build_app(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from magic_agent import cli

    captured = {}

    def fake_build_app(*, mode, root_dir):
        captured["mode"] = mode
        captured["root_dir"] = root_dir
        return SimpleNamespace()

    def fake_run_live(app, *, clock, max_iters):
        captured["max_iters"] = max_iters
        return 1

    monkeypatch.setattr("magic_agent.app.build_app", fake_build_app)
    monkeypatch.setattr("magic_agent.live.run_live", fake_run_live)
    args = SimpleNamespace(executor="twak", max_iters=1, root_dir=tmp_path)

    cli._cmd_run(args)

    assert captured == {"mode": "twak", "root_dir": tmp_path, "max_iters": 1}
