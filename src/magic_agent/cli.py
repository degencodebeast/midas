# src/magic_agent/cli.py
"""`magic-agent run` — wire context + scanner + decision + executor into the loop.

The live driver fetches OHLCV, drops the forming bar, dedupes on timestamp (the
new-closed-candle gate), and calls ``runner.on_candle`` per closed candle. ``paper``
is the default executor so a bare ``magic-agent run`` never touches funds.
"""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="magic-agent",
                                     description="Bounded autonomous trading agent (BNB Track 1)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the autonomous loop")
    run.add_argument("--symbol", default="BNB/USDT", help="Pair to trade")
    run.add_argument("--executor", choices=["paper", "aster"], default="paper",
                     help="Execution backend (default: paper — no real funds)")
    run.add_argument("--risk-pct", dest="risk_pct", type=float, default=0.01,
                     help="Fraction of equity risked per trade (default 0.01)")
    run.add_argument("--leverage", type=float, default=1.0, help="Leverage (default 1.0)")
    run.set_defaults(func=_cmd_run)
    return parser


def _cmd_run(args: argparse.Namespace) -> None:  # pragma: no cover - live loop
    # Wiring only; exercised manually / on testnet, not in unit tests.
    from magic_agent.context import CmcContextAdapter
    from magic_agent.executor import PaperExecutor

    context = CmcContextAdapter(client=None)  # TODO(cli): wire CMC client per spike findings
    from magic_agent.scanner_gateway import ScannerGateway
    gateway = ScannerGateway()                # sole scanner seam; setup_fn = lambda: gateway.scan(args.symbol)
    if args.executor == "paper":
        executor = PaperExecutor(starting_equity=1000.0)
    else:
        import os

        import ccxt  # noqa: F401
        from magic_agent.executors.aster import AsterRestExecutor

        exchange = ccxt.aster({
            "privateKey": os.environ["ASTER_PRIVATE_KEY"],
            "walletAddress": os.environ.get("ASTER_WALLET_ADDRESS"),
        })
        if os.environ.get("ASTER_TESTNET"):
            # ccxt ships urls['test']=None for aster — override the perp base URL manually.
            # Testnet base URL + chainId are in docs/superpowers/specs/2026-06-15-venue-spike-findings.md
            # (reconcile chainId 1666 vs 714 before signing on testnet).
            exchange.urls["api"] = "https://fapi.asterdex-testnet.com/fapi"
        executor = AsterRestExecutor(exchange, args.symbol)
    print(f"agent ready: symbol={args.symbol} executor={args.executor} "
          f"risk_pct={args.risk_pct} leverage={args.leverage}")
    # The full poll/new-candle-gate loop is added when wiring live data (post-spike).


def main(argv: list[str] | None = None) -> None:  # pragma: no cover
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
