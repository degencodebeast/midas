# tests/test_cli.py
from magic_agent.cli import build_parser


def test_run_parses_symbol_executor_and_risk_defaults():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT"])
    assert args.symbol == "BNB/USDT"
    assert args.executor == "paper"      # safe default
    assert args.risk_pct == 0.01
    assert args.leverage == 1.0


def test_run_accepts_aster_executor_and_overrides():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT", "--executor", "aster",
                         "--risk-pct", "0.005", "--leverage", "5"])
    assert args.executor == "aster" and args.risk_pct == 0.005 and args.leverage == 5.0


def test_run_policy_flag_defaults_assemble_non_empty_config():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT"])
    # Fail-closed defaults -> a non-empty PolicyConfig in _cmd_run.
    assert args.max_daily_loss == 50.0
    assert args.max_leverage == 5.0
    assert args.require_stop is True
    assert args.cooldown_seconds is None


def test_run_policy_flags_overridable():
    p = build_parser()
    args = p.parse_args(["run", "--max-daily-loss", "25", "--max-leverage", "3",
                         "--no-require-stop", "--cooldown-seconds", "30"])
    assert args.max_daily_loss == 25.0
    assert args.max_leverage == 3.0
    assert args.require_stop is False
    assert args.cooldown_seconds == 30.0


# ---------------------------------------------------------------------------
# L4: magic-agent serve + ASGI entry
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
