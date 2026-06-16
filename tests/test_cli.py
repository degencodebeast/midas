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


# ---------------------------------------------------------------------------
# F1: `magic-agent run` writes the decision log (live audit + dashboard feed)
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


def test_build_run_kwargs_wires_log_and_policy_config(tmp_path):
    """build_run_kwargs returns the run_live kwargs with `log` = AgentLog at
    args.log and a NON-EMPTY (fail-closed) PolicyConfig — proving the live path
    actually wires a decision log (was the hole `/api/decisions` couldn't see)."""
    from magic_agent.cli import build_parser, build_run_kwargs
    from magic_agent.context import CmcContextAdapter
    from magic_agent.executor import PaperExecutor
    from magic_agent.log import AgentLog
    from magic_agent.policy import PolicyConfig

    log_path = str(tmp_path / "decisions.jsonl")
    args = build_parser().parse_args(["run", "--symbol", "BNB/USDT", "--log", log_path])

    executor = PaperExecutor(starting_equity=1000.0)
    kwargs = build_run_kwargs(
        args,
        executor=executor,
        gateway=object(),
        context=CmcContextAdapter(None),
        feed=lambda symbol: (None, None),
    )

    # log is a real AgentLog pointing at args.log
    assert isinstance(kwargs["log"], AgentLog)
    assert kwargs["log"]._path == __import__("pathlib").Path(log_path)
    # policy_config is a NON-EMPTY PolicyConfig (fail-closed: run_policies won't raise)
    assert isinstance(kwargs["policy_config"], PolicyConfig)
    assert kwargs["policy_config"].active()  # non-empty
    # symbol/executor/feed are forwarded so run_live(**kwargs) is callable
    assert kwargs["symbol"] == "BNB/USDT"
    assert kwargs["executor"] is executor


def test_build_run_kwargs_run_live_writes_a_decision_record(tmp_path):
    """End-to-end-ish: feed build_run_kwargs's output (with a real AgentLog) into
    run_live with a fake feed yielding ONE allowed setup → a JSON line lands in the
    log file (proves the live path writes records `/api/decisions` can read)."""
    import json

    from magic_agent.cli import build_parser, build_run_kwargs
    from magic_agent.context import CmcContextAdapter
    from magic_agent.executor import PaperExecutor
    from magic_agent.live import run_live
    from magic_agent.models import Candle, Setup, Side

    log_path = tmp_path / "decisions.jsonl"
    args = build_parser().parse_args(["run", "--symbol", "BNB/USDT", "--log", str(log_path)])

    allowed_setup = Setup("BNB/USDT", Side.LONG, "A", "risk_on",
                          600.0, 588.0, 636.0, "chained_scob")

    class _FakeGateway:
        def scan(self, symbol):
            return allowed_setup

    pairs = iter([(Candle(600, 601, 599, 600), 1000)])

    def feed(symbol):
        return next(pairs)

    kwargs = build_run_kwargs(
        args,
        executor=PaperExecutor(starting_equity=1000.0),
        gateway=_FakeGateway(),
        context=CmcContextAdapter(None),
        feed=feed,
    )

    run_live(**kwargs, max_iters=1)

    # The log file now exists and holds at least one valid JSON decision line.
    assert log_path.exists()
    lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1
    rec = json.loads(lines[0])
    assert "action" in rec and "outcome" in rec
