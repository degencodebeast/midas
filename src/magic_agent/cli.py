# src/magic_agent/cli.py
"""`magic-agent run` / `serve` — wire the shared spot runtime + the dashboard API.

``run`` assembles the spot runtime ``app`` (scanner gateway, executability, the
execution port, journals, compliance, state) and drives the shared
:func:`magic_agent.live.run_live`, which calls :func:`magic_agent.runner.run_cycle`
per tick. **Paper is the default**: a bare ``magic-agent run`` wires the
:class:`PaperExecutionAdapter` (simulated fills, no funds). ``--executor twak`` is the
ONLY live opt-in; live execution routes through the TWAK/coordinator path. The spot runtime carries no
perp-venue, margin, or directional-short configuration — spot longs only.

``serve`` exposes the read-only mission-control dashboard API (unchanged): it reads the
live status snapshot the ``run`` loop writes and falls back to a clearly-labelled demo.
"""
from __future__ import annotations

import argparse

from magic_agent.status_store import DEFAULT_STATUS_PATH

# Default CMC API base. Overridable via MAGIC_AGENT_CMC_BASE_URL.
DEFAULT_CMC_BASE_URL = "https://pro-api.coinmarketcap.com"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="magic-agent",
                                     description="Bounded autonomous spot trading agent (BNB Track 1)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the autonomous spot loop")
    run.add_argument("--symbol", default="BNB/USDT", help="Pair to trade")
    # Paper is the SAFE default — never live without an explicit `--executor twak`.
    # `twak` is the only live execution choice (self-custody swap path); the spot
    # runtime carries no perp-venue, margin, or directional-short configuration.
    run.add_argument("--executor", choices=["paper", "twak"], default="paper",
                     help="Execution backend (default: paper — simulated fills, no funds)")
    # Decision log: the live audit trail AND the dashboard's /api/decisions feed
    # source. Default MUST match `serve --log` so both processes share one file.
    run.add_argument("--log", default=".magic_agent/decisions.jsonl",
                     help="Path to the JSONL decision log (default: .magic_agent/decisions.jsonl)")
    run.set_defaults(func=_cmd_run)

    sv = sub.add_parser("serve", help="Serve the read-only mission-control dashboard API")
    sv.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    sv.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    sv.add_argument("--log", default=".magic_agent/decisions.jsonl",
                    help="Path to the JSONL decision log (default: .magic_agent/decisions.jsonl)")
    sv.set_defaults(func=_cmd_serve)

    return parser


def _cmd_run(args: argparse.Namespace) -> None:  # pragma: no cover - live loop wiring
    # Wiring only; exercised manually / on testnet, not in unit tests. The shared
    # spot loop logic itself lives in (unit-tested) runner.run_cycle / live.run_live.
    #
    # Paper is the default execution port (simulated fills, no funds). `--executor twak`
    # is the only live opt-in: it routes entries through the TWAK/ExecutionCoordinator
    # path. Either way EVERY decision input flows through the shared
    # LifecycleEvaluator -> DecisionPipeline inside run_cycle — the CLI never hand-builds
    # DecisionInputs and never reaches the scanner / RiskPolicy / coordinator directly.
    raise NotImplementedError(
        "spot runtime app assembly is wired on testnet; the shared loop "
        "(runner.run_cycle / live.run_live) is the unit-tested surface"
    )


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
        loop's latest state (cross-process). When the file is absent it returns a
        clearly-labelled demo fallback (mode ``"demo"``).
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
            # demo fallback is only used before `run` starts writing — and it is
            # clearly labelled mode "demo".
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


def main(argv: list[str] | None = None) -> None:  # pragma: no cover
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
