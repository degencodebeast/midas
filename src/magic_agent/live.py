# src/magic_agent/live.py
"""Live poll loop — the new-closed-candle gate around ``runner.on_candle``.

Pure loop logic only: the ``feed`` and ``gateway`` are INJECTED, so the whole loop
runs in tests with no network (a fake feed yields a fixed list of ``(candle, ts)``).
The real ccxt/scanner feed (drop the forming bar, fetch the latest CLOSED bar) is built
in ``cli._cmd_run`` as thin wiring — this module never imports ccxt.

The gate mirrors the llm-trading-bot loop: dedupe on the candle timestamp so each closed
bar is processed exactly once (``ts <= last_ts`` is skipped). ``policy_config`` is
forwarded into ``on_candle`` on every iteration, so the live path is fail-closed.
"""
from __future__ import annotations

from collections.abc import Callable

from magic_agent.context import CmcContextAdapter
from magic_agent.executor import PerpExecutor
from magic_agent.log import AgentLog
from magic_agent.policy import PolicyConfig
from magic_agent.runner import on_candle


def run_live(
    executor: PerpExecutor,
    *,
    gateway,
    context: CmcContextAdapter,
    feed: Callable[[str], tuple],
    symbol: str,
    policy_config: PolicyConfig,
    advisor: Callable | None = None,
    log: AgentLog | None = None,
    now_fn: Callable[[], str] | None = None,
    realized_pnl_today: float = 0.0,
    max_iters: int | None = None,
) -> int:
    """Poll ``feed(symbol)`` for the latest closed candle and drive ``on_candle``.

    Returns the number of NEW closed candles processed (duplicate/older ts skipped).
    ``max_iters`` bounds the loop (each iteration = one feed poll) so tests terminate;
    when ``None`` the loop runs until the feed raises ``StopIteration`` (live feeds
    block instead, so the bound is only needed for the fake-feed unit tests).
    """
    last_ts: object | None = None
    processed = 0
    iters = 0
    while max_iters is None or iters < max_iters:
        iters += 1
        try:
            candle, ts = feed(symbol)
        except StopIteration:
            break

        # New-closed-candle gate: dedupe on ts so each closed bar runs exactly once.
        if last_ts is not None and ts <= last_ts:
            continue
        last_ts = ts

        setup_fn = lambda: gateway.scan(symbol)  # noqa: E731 — scanner is the sole signal
        now = now_fn() if now_fn is not None else str(ts)
        on_candle(
            executor,
            setup_fn=setup_fn,
            context=context,
            candle=candle,
            policy_config=policy_config,
            advisor=advisor,
            log=log,
            now=now,
            realized_pnl_today=realized_pnl_today,
        )
        processed += 1

    return processed
