"""Spot live driver — the poll loop around the shared :func:`runner.run_cycle`.

This module owns ONLY the cadence: it polls an injected ``clock`` for the current
``now`` and drives :func:`magic_agent.runner.run_cycle(app, now)` once per tick.
Every decision input still flows through the shared
LifecycleEvaluator -> DecisionPipeline inside ``run_cycle`` — the live path never
hand-builds DecisionInputs and never reaches the scanner / RiskPolicy / coordinator
directly. ``app`` carries the injected side-effect ports (scanner, executability,
execution coordinator, journals, compliance, state) so the whole loop runs in tests
with no network.

Paper is the default execution mode: the ``app`` passed in by ``cli`` wires the paper
adapter as the execution port unless live is explicitly opted into. ``max_iters`` bounds
the loop so unit tests terminate; ``None`` runs unbounded. The production clock built by
``cli`` (:func:`make_bar_aligned_clock`) paces each cycle to the next closed H1 bar, so
an unbounded run waits for closed bars instead of busy-spinning. A fake-clock test may
instead raise ``StopIteration`` to end the loop deterministically.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from magic_agent.runner import run_cycle

# Loop-boundary narrative (terse — one line per boundary, not per-cycle spam; the
# per-cycle play-by-play lives in run_cycle).
_log = logging.getLogger(__name__)

# One closed H1 bar per cycle — the scanner doctrine is H1-closed-bar driven, so the
# production clock paces to the top of each hour rather than busy-spinning.
_H1 = timedelta(hours=1)


def _floor_h1(now: datetime) -> datetime:
    """Return the close of the most recently completed H1 bar at or before ``now``.

    Args:
        now: The current timestamp.

    Returns:
        ``now`` floored to the top of the hour (minute/second/microsecond zeroed).
    """
    return now.replace(minute=0, second=0, microsecond=0)


def make_bar_aligned_clock(
    *,
    sleep: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = lambda: datetime.now(tz=timezone.utc),
) -> Callable[[], datetime]:
    """Build a clock that paces each cycle to a closed H1 bar.

    The scanner doctrine is H1-closed-bar driven, so this clock fires one cycle per
    closed bar instead of busy-spinning:

    - The FIRST call returns the close of the most recently completed H1 bar
      immediately (no sleep) — on startup we act on the latest closed bar, so a
      bounded smoke run (``--max-iters``) terminates without waiting a real hour.
    - Each SUBSEQUENT call sleeps (via the injected ``sleep``) until the next H1
      close and returns it.

    Both ``sleep`` and ``now_fn`` are injectable so tests pass a fake sleep and a
    controllable clock and assert the slept duration WITHOUT real waiting.

    Args:
        sleep: Callable invoked with a non-negative number of seconds to wait.
            Defaults to :func:`time.sleep`.
        now_fn: Callable returning the current timezone-aware ``datetime``. Defaults
            to :func:`datetime.now` in UTC.

    Returns:
        A zero-arg clock callable returning a strictly increasing sequence of
        H1-aligned bar closes, pacing to each next close.
    """
    state: dict[str, datetime | None] = {"last_close": None}

    def clock() -> datetime:
        now = now_fn()
        last_close = state["last_close"]
        if last_close is None:
            # First cycle: act on the most recently closed bar immediately.
            target = _floor_h1(now)
        else:
            # Subsequent cycles: wait for the next close after the one we returned.
            target = last_close + _H1
            wait_seconds = (target - now).total_seconds()
            if wait_seconds > 0:
                sleep(wait_seconds)
        state["last_close"] = target
        return target

    return clock


def run_live(app, *, clock: Callable[[], object], max_iters: int | None = None) -> int:
    """Drive ``run_cycle(app, clock())`` once per tick; return the cycles processed.

    Args:
        app: The assembled runtime with its injected ports (scanner, executability,
            execution coordinator, journals, compliance, state).
        clock: A zero-arg callable returning the current ``now`` for each cycle.
            Raising ``StopIteration`` ends the loop (used by the fake-clock tests).
        max_iters: Optional bound on the number of cycles (one per tick) so tests
            terminate; ``None`` runs until the clock raises ``StopIteration``.

    Returns:
        The number of cycles processed.
    """
    _log.info(
        "live loop: max_iters=%s, cadence=closed-H1-bar",
        "unbounded" if max_iters is None else max_iters,
    )
    processed = 0
    while max_iters is None or processed < max_iters:
        if processed > 0:
            # The clock blocks here until the next closed H1 bar; tell the operator
            # the loop is waiting (not hung) between cycles.
            _log.info("waiting for next closed H1 bar…")
        try:
            now = clock()
        except StopIteration:
            break
        run_cycle(app, now)
        processed += 1
    _log.info("live loop: clean shutdown after %d cycle(s)", processed)
    return processed
