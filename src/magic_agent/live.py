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
from magic_agent.decision import LlmAdvice, RISK_PCT_DEFAULT
from magic_agent.executor import PerpExecutor
from magic_agent.log import AgentLog
from magic_agent.models import ContextSnapshot, Setup
from magic_agent.policy import PolicyConfig
from magic_agent.runner import on_candle
from magic_agent.status import build_status
from magic_agent.status_store import write_status_snapshot


def run_live(
    executor: PerpExecutor,
    *,
    gateway,
    context: CmcContextAdapter,
    feed: Callable[[str], tuple],
    symbol: str,
    policy_config: PolicyConfig,
    risk_pct: float = RISK_PCT_DEFAULT,
    leverage: float = 1.0,
    advisor: Callable[[Setup, ContextSnapshot], LlmAdvice | None] | None = None,
    log: AgentLog | None = None,
    now_fn: Callable[[], str] | None = None,
    realized_pnl_today: float = 0.0,
    snapshot_path: str | None = None,
    mode: str = "paper",
    venue: str = "binance",
    agent_id: str | None = None,
    max_iters: int | None = None,
) -> int:
    """Poll ``feed(symbol)`` for the latest closed candle and drive ``on_candle``.

    Returns the number of NEW closed candles processed (duplicate/older ts skipped).
    ``max_iters`` bounds the loop (each iteration = one feed poll) so tests terminate;
    when ``None`` the loop runs until the feed raises ``StopIteration`` (live feeds
    block instead, so the bound is only needed for the fake-feed unit tests).

    Daily-loss kill-switch (F2): ``realized_pnl_today`` forwarded to ``on_candle`` is
    computed LIVE from the executor's realized cash. On the first processed candle we
    snapshot ``session_start_available = executor.get_account(...).available``; thereafter
    each candle forwards ``executor.get_account(close).available - session_start_available``
    (in ``PaperExecutor`` ``available`` == realized cash, so this delta is the session's
    realized PnL). The ``realized_pnl_today`` parameter is now a BASELINE seam added on top
    of the live delta (default ``0.0`` = pure live behavior); tests can inject a non-zero
    baseline to simulate a pre-existing daily loss without scripting the executor.

    Shared live status (F4): when ``snapshot_path`` is set, after each processed candle
    a fresh ``build_status(...)`` snapshot is written (atomically) to that path so a
    separate ``serve`` process can read the live loop's latest state via
    ``/api/status``. ``mode``/``venue`` label the snapshot; ``starting_equity`` reuses
    the F2 ``session_start_available`` baseline. ``snapshot_path=None`` (default) keeps
    the loop backward-compatible — no file is written.

    Trust-surface contract (P1.7-1): the snapshot's ``halted``/``daily_loss``/
    ``max_daily_loss`` reflect the REAL live daily-loss kill-switch state (no longer a
    hardcoded ``halted=False``) — ``halted`` tracks the same condition ``run_policies``
    uses to deny entries. ``agent_id`` (the ERC-8004 identity, ``None`` when unregistered)
    is forwarded into the snapshot so the dashboard badge shows the real id or an honest
    "unregistered".
    """
    last_ts: object | None = None
    processed = 0
    iters = 0
    baseline_pnl = realized_pnl_today
    session_start_available: float | None = None
    while max_iters is None or iters < max_iters:
        iters += 1
        try:
            candle, ts = feed(symbol)
        except StopIteration:
            break

        # New-closed-candle gate: dedupe on ts so each closed bar runs exactly once.
        # ``ts is None`` is a feed "no candle this poll" signal (e.g. a transient fetch
        # error) — skip without advancing ``last_ts``.
        if ts is None or (last_ts is not None and ts <= last_ts):
            continue
        last_ts = ts

        # Realized-PnL delta since session start (the live kill-switch input). Snapshot the
        # starting realized cash on the first processed candle, then forward the delta
        # (+ any injected baseline) each iteration.
        available = executor.get_account(mark_price=candle.close).available
        if session_start_available is None:
            session_start_available = available
        live_realized_pnl_today = baseline_pnl + (available - session_start_available)

        setup_fn = lambda: gateway.scan(symbol)  # noqa: E731 — scanner is the sole signal
        now = now_fn() if now_fn is not None else str(ts)
        on_candle(
            executor,
            setup_fn=setup_fn,
            context=context,
            candle=candle,
            risk_pct=risk_pct,
            leverage=leverage,
            policy_config=policy_config,
            advisor=advisor,
            log=log,
            now=now,
            realized_pnl_today=live_realized_pnl_today,
        )
        processed += 1

        # F4: publish the live status snapshot so a separate `serve` process can read
        # it. starting_equity reuses the F2 session-start realized cash baseline.
        if snapshot_path is not None:
            # Compute the REAL safety state for the snapshot (no hardcoded halted=False):
            # the daily-loss kill-switch trips exactly as run_policies evaluates it.
            max_daily_loss = policy_config.max_daily_loss
            daily_loss = max(0.0, -live_realized_pnl_today)
            halted = (
                max_daily_loss is not None
                and live_realized_pnl_today <= -abs(max_daily_loss)
            )
            write_status_snapshot(
                snapshot_path,
                build_status(
                    executor,
                    symbol=symbol,
                    mode=mode,
                    venue=venue,
                    mark_price=candle.close,
                    starting_equity=session_start_available,
                    halted=halted,
                    daily_loss=daily_loss,
                    max_daily_loss=max_daily_loss,
                    agent_id=agent_id,
                ),
            )

    return processed
