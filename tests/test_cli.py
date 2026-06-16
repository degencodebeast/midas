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
