# src/magic_agent/cli.py
"""`magic-agent run` — wire context + scanner + decision + executor into the loop.

The live driver fetches OHLCV, drops the forming bar, dedupes on timestamp (the
new-closed-candle gate), and calls ``runner.on_candle`` per closed candle. ``paper``
is the default executor so a bare ``magic-agent run`` never touches funds.
"""
from __future__ import annotations

import argparse
import os
from typing import Callable

from magic_agent.status_store import DEFAULT_STATUS_PATH

# Default model when MAGIC_AGENT_LLM_MODEL is unset. A current Claude 4-class id
# (verified against the live anthropic-sdk-python ModelParam list via context7).
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"


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
    # L5: optional bounded LLM advisor. OFF by default => pure deterministic path.
    # When on, an LlmAdvisor is wired in; the runner applies it ONLY via clamp_advice
    # (size-down / wait-only), so it can never invent, re-grade, flip, or up-size.
    run.add_argument("--advisor", dest="advisor", default=False,
                     action="store_true",
                     help="Enable the bounded LLM advisor (advisory size-down/wait; "
                          "default: off = deterministic)")
    # Decision log: the live audit trail AND the dashboard's /api/decisions feed
    # source. Default MUST match `serve --log` so both processes share one file.
    run.add_argument("--log", default=".magic_agent/decisions.jsonl",
                     help="Path to the JSONL decision log (default: .magic_agent/decisions.jsonl)")
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


def _make_anthropic_client(*, api_key: str, model: str) -> Callable[[str, str], str]:
    """Build the real Anthropic (Claude) Messages-API client callable.

    Returns ``client(system_prompt, user_prompt) -> str`` which issues a single
    structured-output request (``system`` = doctrine, one ``user`` message) and returns
    the model's TEXT (the JSON string the advisor parses). The SDK client is created
    here; the ACTUAL network call is ``# pragma: no cover`` (no API key in unit tests).
    The advisor wraps this in its own try/except, so any error here surfaces as
    ``None`` advice (deterministic fallback) — never an exception out of the runner.
    """
    from anthropic import Anthropic

    sdk = Anthropic(api_key=api_key)

    def _client(system_prompt: str, user_prompt: str) -> str:  # pragma: no cover - network
        message = sdk.messages.create(
            model=model,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # message.content is a list of content blocks; concatenate the text blocks.
        return "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", None) == "text"
        )

    return _client


def _default_advisor_factory(
    *, client_maker: Callable[..., Callable[[str, str], str]] = _make_anthropic_client,
):
    """Construct the REAL bounded LLM advisor from environment config.

    Config (env):
      * ``MAGIC_AGENT_LLM_API_KEY`` — Anthropic API key. If UNSET/empty, returns
        ``None`` (no advisor → pure deterministic path). This is the SAFE default.
      * ``MAGIC_AGENT_LLM_MODEL`` — model id (defaults to ``DEFAULT_LLM_MODEL``).

    When a key is present, returns ``LlmAdvisor(client_maker(api_key=..., model=...))``
    using the operating-doctrine system prompt. ``client_maker`` is injectable so tests
    can supply a fake client builder and assert the wiring WITHOUT any network/SDK call.
    The advisor's own parse/clamp/None-on-failure contract is unchanged.
    """
    from magic_agent.advisor import LlmAdvisor

    api_key = os.environ.get("MAGIC_AGENT_LLM_API_KEY", "")
    if not api_key:
        # No credentials → no advisor → deterministic path (safe default, not a stub).
        return None

    model = os.environ.get("MAGIC_AGENT_LLM_MODEL") or DEFAULT_LLM_MODEL
    client = client_maker(api_key=api_key, model=model)
    return LlmAdvisor(client)


def build_run_kwargs(args: argparse.Namespace, *, executor, gateway, context, feed,
                     advisor_factory=_default_advisor_factory,
                     agent_id: str | None = None) -> dict:
    """Assemble the kwargs for ``run_live(...)`` from parsed CLI ``args`` + the
    injected execution units (``executor``/``gateway``/``context``/``feed``).

    This is the TESTABLE wiring extracted out of the ``# pragma: no cover``
    live driver: it builds the fail-closed ``PolicyConfig`` from the flags AND
    ``log=AgentLog(args.log)`` — the previously-missing decision log that feeds the
    dashboard's ``/api/decisions`` endpoint. No network here; only the real ccxt
    feed/executor *construction* (in ``_cmd_run``) stays uncovered.

    ``agent_id`` (the ERC-8004 on-chain identity, ``None`` when unregistered) is threaded
    through into ``run_live`` so the live status snapshot carries a real id or honest
    ``None`` — never a fabricated placeholder.
    """
    from magic_agent.log import AgentLog
    from magic_agent.policy import PolicyConfig

    # L5: honor --advisor. OFF (default) => advisor=None => pure deterministic path.
    # ON => construct the advisor via the (injectable) factory. The runner only ever
    # applies it through clamp_advice, so it is advisory (size-down/wait) by construction.
    advisor = advisor_factory() if getattr(args, "advisor", False) else None

    # Fail-closed: a NON-EMPTY PolicyConfig from the CLI flags. run_policies RAISES on
    # an empty config, so every live entry is gated (this closes the "policy_config
    # never passed live" gap).
    policy_config = PolicyConfig(
        max_daily_loss=args.max_daily_loss,
        max_leverage=args.max_leverage,
        max_concurrent=1,
        require_stop=args.require_stop,
        cooldown_seconds=args.cooldown_seconds,
    )

    return {
        "executor": executor,
        "gateway": gateway,
        "context": context,
        "feed": feed,
        "symbol": args.symbol,
        "policy_config": policy_config,
        "risk_pct": args.risk_pct,
        "leverage": args.leverage,
        # L5: advisory-only LLM advisor (None when --advisor is off = deterministic).
        "advisor": advisor,
        # The decision log: live audit trail + the dashboard's /api/decisions source.
        "log": AgentLog(args.log),
        # F4: write a live status snapshot each candle so `serve` (a separate
        # process) reflects this loop's latest state via /api/status. Default path
        # matches build_serve_app's default reader so both processes share one file.
        "snapshot_path": DEFAULT_STATUS_PATH,
        "mode": "paper" if args.executor == "paper" else "live",
        "venue": "binance",  # OHLCV feed source
        # P1.7-1: ERC-8004 identity (None when unregistered — never a fabricated id).
        # Surfaced in the live status snapshot so the dashboard badge is honest.
        "agent_id": agent_id,
    }


def _resolve_agent_id() -> str | None:  # pragma: no cover - on-chain wiring
    """Resolve the ERC-8004 on-chain agent id, or None when unregistered.

    Honest by construction: when no registrar/env is configured we return None
    (the dashboard then shows "unregistered") — we NEVER fabricate a placeholder.
    The actual on-chain ``register()`` is left uncovered (no chain in unit tests).
    """
    agent_uri = os.environ.get("MAGIC_AGENT_ERC8004_URI")
    if not agent_uri:
        return None  # not configured -> unregistered, honestly
    try:
        from magic_agent.identity import Erc8004Identity

        # The concrete registrar comes from the OPTIONAL `bnbagent` SDK
        # (install with `pip install 'magic-agent[identity]'`). It is imported
        # lazily and best-effort: if the extra is not installed, or the concrete
        # registrar is not yet wired, ANY error -> unregistered (None), never a
        # fake id. NOTE: the `bnbagent` SDK exposes `ERC8004Agent`
        # (`bnbagent.erc8004`), not an `Erc8004Contract`; the production wiring
        # to that real API is intentionally still pending, so identity stays
        # "unregistered" until it is completed — honest by construction.
        from bnbagent_sdk import Erc8004Contract  # type: ignore  # noqa: F401  # pending real ERC8004Agent wiring

        registrar = Erc8004Contract(
            rpc_url=os.environ["MAGIC_AGENT_ERC8004_RPC"],
            private_key=os.environ["MAGIC_AGENT_ERC8004_KEY"],
        )
        identity = Erc8004Identity(
            registrar,
            agent_uri=agent_uri,
            metadata={"name": "MIDAS", "track": "BNB-1"},
        )
        return str(identity.register())
    except Exception:
        # On-chain registration is best-effort: any failure -> unregistered, not a crash.
        return None


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

    # ERC-8004 identity: real agent id when configured/registered, else None
    # (the dashboard shows an honest "unregistered" badge — never a fake placeholder).
    agent_id = _resolve_agent_id()

    run_live(**build_run_kwargs(
        args, executor=executor, gateway=gateway, context=context, feed=feed,
        agent_id=agent_id))


def build_serve_app(*, log_path: str, snapshot_path: str = DEFAULT_STATUS_PATH,
                    status_provider=None):
    """Build and return the read-only FastAPI app for the dashboard.

    Parameters
    ----------
    log_path:
        Path to the JSONL decision log (passed to ``create_app``).
    snapshot_path:
        Path to the live status snapshot written by ``magic-agent run``
        (default ``status_store.DEFAULT_STATUS_PATH`` — same file ``run`` writes).
        The default provider READS this file so ``serve`` reflects the live
        loop's latest state (cross-process F4 fix). When the file is absent it
        returns a clearly-labelled demo fallback (mode ``"demo"``).
    status_provider:
        A zero-argument callable returning a ``build_status``-shaped dict.
        When ``None``, the snapshot-reading provider above is used.

    Returns
    -------
    FastAPI
        The constructed app — usable directly with ``TestClient`` (no server
        required) or passed to ``uvicorn.run``.
    """
    from magic_agent.api import create_app
    from magic_agent.status_store import read_status_snapshot

    if status_provider is None:
        def _provider() -> dict:
            # Cross-process: reflect the live `run` loop's latest snapshot. The
            # detached demo executor (the F4 bug) is only a fallback before `run`
            # starts writing — and it is clearly labelled mode "demo".
            snap = read_status_snapshot(snapshot_path)
            if snap is not None:
                return snap
            return {
                "mode": "demo",
                "venue": "demo",
                "halted": False,
                "equity": 0.0,
                "available": 0.0,
                "currency": "USDT",
                "realized_pnl": 0.0,
                "open_pnl": 0.0,
                # Full UI contract (page.tsx reads these) — kept in lock-step with
                # build_status so the demo fallback never drifts from the live
                # snapshot shape. None = "not reported" (honest placeholders).
                "daily_loss": None,
                "max_daily_loss": None,
                "agent_id": None,
                "positions": [],
            }

        status_provider = _provider

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
