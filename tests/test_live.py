"""Unit tests for the spot live driver (``run_live``) — injected clock, no network.

The live driver is a thin poll loop around the shared :func:`magic_agent.runner.run_cycle`:
it owns only the cadence (an injected ``clock`` supplies ``now``; ``max_iters`` bounds the
loop so tests terminate). Every decision input still flows through the shared
LifecycleEvaluator -> DecisionPipeline inside ``run_cycle`` — the live path NEVER
hand-builds DecisionInputs and never calls the scanner/risk/coordinator directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from magic_agent.live import run_live


class _RecordingApp:
    """Minimal app stand-in that records each ``run_cycle`` invocation's ``now``."""

    def __init__(self) -> None:
        self.cycles: list = []


def test_run_live_calls_run_cycle_once_per_tick(monkeypatch):
    import magic_agent.live as live_mod

    calls: list = []
    monkeypatch.setattr(live_mod, "run_cycle", lambda app, now: calls.append((app, now)))

    app = _RecordingApp()
    base = datetime(2026, 6, 21, tzinfo=timezone.utc)
    ticks = iter([base, base + timedelta(minutes=5), base + timedelta(minutes=10)])

    processed = run_live(app, clock=lambda: next(ticks), max_iters=3)

    assert processed == 3
    assert [now for _, now in calls] == [
        base, base + timedelta(minutes=5), base + timedelta(minutes=10),
    ]
    assert all(a is app for a, _ in calls)


def test_run_live_stops_at_max_iters(monkeypatch):
    import magic_agent.live as live_mod

    calls: list = []
    monkeypatch.setattr(live_mod, "run_cycle", lambda app, now: calls.append(now))

    app = _RecordingApp()
    base = datetime(2026, 6, 21, tzinfo=timezone.utc)
    counter = {"n": 0}

    def clock():
        counter["n"] += 1
        return base + timedelta(minutes=counter["n"])

    processed = run_live(app, clock=clock, max_iters=2)
    assert processed == 2
    assert len(calls) == 2


def test_run_live_stops_on_stop_iteration(monkeypatch):
    import magic_agent.live as live_mod

    monkeypatch.setattr(live_mod, "run_cycle", lambda app, now: None)

    base = datetime(2026, 6, 21, tzinfo=timezone.utc)
    ticks = iter([base])

    def clock():
        return next(ticks)

    # No max_iters: the loop runs until the clock raises StopIteration (one tick here).
    processed = run_live(_RecordingApp(), clock=clock)
    assert processed == 1
