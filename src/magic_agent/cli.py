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
    # Fail-closed policy flags -> a NON-EMPTY PolicyConfig so the live path always
    # gates through run_policies (which RAISES on an empty config).
    run.add_argument("--max-daily-loss", dest="max_daily_loss", type=float, default=50.0,
                     help="Daily-loss kill-switch (positive USDT; default 50.0)")
    run.add_argument("--max-leverage", dest="max_leverage", type=float, default=5.0,
                     help="Max leverage policy cap (default 5.0)")
    run.add_argument("--require-stop", dest="require_stop", default=True,
                     action=argparse.BooleanOptionalAction,
                     help="Reject intents without a stop (default: on)")
    run.add_argument("--cooldown-seconds", dest="cooldown_seconds", type=float, default=None,
                     help="Minimum seconds between trades (default: off)")
    run.set_defaults(func=_cmd_run)

    jt = sub.add_parser("judge-trace", help="Print a one-screen policy proof (zero funds)")
    jt.set_defaults(func=_cmd_judge_trace)

    sv = sub.add_parser("serve", help="Serve the read-only mission-control dashboard API")
    sv.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    sv.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    sv.add_argument("--log", default=".magic_agent/decisions.jsonl",
                    help="Path to the JSONL decision log (default: .magic_agent/decisions.jsonl)")
    sv.set_defaults(func=_cmd_serve)

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

    # Fail-closed: a NON-EMPTY PolicyConfig from the CLI flags. run_policies RAISES on
    # an empty config, so every live entry is gated (this closes the "policy_config
    # never passed live" gap).
    from magic_agent.policy import PolicyConfig
    policy_config = PolicyConfig(
        max_daily_loss=args.max_daily_loss,
        max_leverage=args.max_leverage,
        max_concurrent=1,
        require_stop=args.require_stop,
        cooldown_seconds=args.cooldown_seconds,
    )

    # Real closed-bar feed: fetch OHLCV, DROP the forming bar, return the latest CLOSED
    # candle + its open-time as the dedupe key. Thin ccxt wiring — the loop logic itself
    # lives in (unit-tested) run_live; this fetch is intentionally uncovered.
    import time

    import ccxt

    from magic_agent.models import Candle

    # enableRateLimit paces fetch_ohlcv so the poll loop can't hammer the public endpoint.
    market = ccxt.binance({"enableRateLimit": True})  # OHLCV source; execution uses `executor`

    def feed(symbol: str):
        # The unbounded live loop polls back-to-back; sleep + rate-limit set the cadence,
        # and a transient network error must not kill the loop (skip this poll instead).
        time.sleep(15)
        try:
            ohlcv = market.fetch_ohlcv(symbol, timeframe="5m", limit=2)
        except ccxt.NetworkError:
            return None, None  # gated out by the <= last_ts check (None never advances)
        ts, o, h, low, c, _v = ohlcv[-2]  # [-1] is the still-forming bar -> dropped
        return Candle(o, h, low, c), ts

    from magic_agent.live import run_live

    run_live(
        executor,
        gateway=gateway,
        context=context,
        feed=feed,
        symbol=args.symbol,
        policy_config=policy_config,
        risk_pct=args.risk_pct,
        leverage=args.leverage,
    )


def build_serve_app(*, log_path: str, status_provider=None):
    """Build and return the read-only FastAPI app for the dashboard.

    Parameters
    ----------
    log_path:
        Path to the JSONL decision log (passed to ``create_app``).
    status_provider:
        A zero-argument callable returning a ``build_status``-shaped dict.
        When ``None``, a demo ``PaperExecutor``-backed provider is built
        automatically (mode="paper", venue="demo", mark_price=0.0).

    Returns
    -------
    FastAPI
        The constructed app — usable directly with ``TestClient`` (no server
        required) or passed to ``uvicorn.run``.
    """
    from magic_agent.api import create_app
    from magic_agent.executor import PaperExecutor
    from magic_agent.status import build_status

    if status_provider is None:
        _executor = PaperExecutor(starting_equity=1000.0)

        def status_provider() -> dict:  # type: ignore[misc]
            return build_status(
                _executor,
                mode="paper",
                venue="demo",
                mark_price=0.0,
            )

    return create_app(log_path=log_path, status_fn=status_provider)


def _cmd_serve(args: argparse.Namespace) -> None:  # pragma: no cover
    import uvicorn

    app = build_serve_app(log_path=args.log)
    uvicorn.run(app, host=args.host, port=args.port)


def _cmd_judge_trace(args: argparse.Namespace) -> None:  # pragma: no cover
    from magic_agent.judge_trace import judge_trace_report
    from magic_agent.policy import PolicyConfig
    print(judge_trace_report(PolicyConfig(
        max_leverage=5.0, max_daily_loss=50.0, max_concurrent=1, require_stop=True)))


def main(argv: list[str] | None = None) -> None:  # pragma: no cover
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
