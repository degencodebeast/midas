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
the loop so unit tests terminate; ``None`` runs until the clock raises ``StopIteration``
(a real live clock blocks instead, so the bound is only needed for tests).
"""
from __future__ import annotations

from collections.abc import Callable

from magic_agent.runner import run_cycle


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
    processed = 0
    while max_iters is None or processed < max_iters:
        try:
            now = clock()
        except StopIteration:
            break
        run_cycle(app, now)
        processed += 1
    return processed
