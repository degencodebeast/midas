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
