"""Tests for the production bar-close-aligned clock (no busy-spin on the default run).

The default ``magic-agent run`` is H1-closed-bar driven: cycles fire once per closed
H1 bar, NOT continuously. The pacing lives in the production clock the CLI builds
(``make_bar_aligned_clock``), driven by an INJECTED ``sleep`` so tests assert the
sleep duration WITHOUT real waiting. ``run_live`` stays a thin driver around the
injected clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from magic_agent.live import make_bar_aligned_clock, run_live


class _RecordingApp:
    def __init__(self) -> None:
        self.cycles: list = []


def test_bar_aligned_clock_paces_to_h1_closes_with_injected_sleep(monkeypatch):
    """The first cycle acts on the latest closed bar immediately; each subsequent
    cycle sleeps a positive, bar-aligned duration. Returned closes strictly increase
    by one hour."""
    import magic_agent.live as live_mod

    monkeypatch.setattr(live_mod, "run_cycle", lambda app, now: None)

    # A controllable wall clock that advances by exactly the slept duration so the
    # NEXT clock() call sees time has reached the prior bar close.
    state = {"now": datetime(2026, 6, 21, 9, 17, 30, tzinfo=timezone.utc)}
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        state["now"] = state["now"] + timedelta(seconds=seconds)

    def now_fn() -> datetime:
        return state["now"]

    clock = make_bar_aligned_clock(sleep=fake_sleep, now_fn=now_fn)

    nows: list[datetime] = []
    original_clock = clock

    def recording_clock() -> datetime:
        ts = original_clock()
        nows.append(ts)
        return ts

    run_live(_RecordingApp(), clock=recording_clock, max_iters=3)

    # The first cycle does not sleep (it acts on the already-closed 09:00 bar). The
    # second cycle waits the 42m30s remaining to the 10:00 close (2550s), and after
    # the fake sleep advances `now` to 10:00 the third waits a full hour (3600s).
    assert len(slept) == 2
    assert all(s > 0 for s in slept)
    assert slept == [2550.0, 3600.0]

    # The returned `now`s are H1-bar-aligned (top of the hour, zero min/sec/micro).
    assert all(
        ts.minute == 0 and ts.second == 0 and ts.microsecond == 0 for ts in nows
    ), nows

    # ...and strictly increasing by exactly one hour.
    assert nows == sorted(nows)
    assert len(set(nows)) == 3
    for earlier, later in zip(nows, nows[1:]):
        assert later - earlier == timedelta(hours=1)

    # First close is the latest CLOSED bar at/below 09:17:30 -> 09:00 (no wait).
    assert nows[0] == datetime(2026, 6, 21, 9, 0, 0, tzinfo=timezone.utc)
    assert nows[-1] == datetime(2026, 6, 21, 11, 0, 0, tzinfo=timezone.utc)


def test_bar_aligned_clock_first_call_does_not_sleep():
    """The first cycle returns the most recently closed bar with NO sleep, so a
    bounded smoke run never waits a real wall-hour to start."""
    state = {"now": datetime(2026, 6, 21, 9, 0, 0, tzinfo=timezone.utc)}
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        state["now"] = state["now"] + timedelta(seconds=seconds)

    clock = make_bar_aligned_clock(sleep=fake_sleep, now_fn=lambda: state["now"])

    first = clock()
    assert first == datetime(2026, 6, 21, 9, 0, 0, tzinfo=timezone.utc)
    assert slept == []  # no sleep on startup

    # The next call paces one full hour to the following close.
    second = clock()
    assert second == datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
    assert slept == [3600.0]


def test_bounded_run_terminates_without_sleeping(monkeypatch):
    """Bounded mode (`--max-iters N`) terminates after N cycles and needs no real
    sleep — a fake clock with a no-op-free injected sleep proves no busy reliance."""
    import magic_agent.live as live_mod

    monkeypatch.setattr(live_mod, "run_cycle", lambda app, now: None)

    base = datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
    slept: list[float] = []

    # Drive a bar-aligned clock anchored to a frozen `now` that the sleep advances;
    # with max_iters the loop still terminates deterministically.
    state = {"now": base}

    def now_fn() -> datetime:
        # Advance now to the just-returned close so the next call moves forward.
        return state["now"]

    def advancing_sleep(seconds: float) -> None:
        slept.append(seconds)
        state["now"] = state["now"] + timedelta(seconds=seconds)

    clock = make_bar_aligned_clock(sleep=advancing_sleep, now_fn=now_fn)
    processed = run_live(_RecordingApp(), clock=clock, max_iters=2)
    assert processed == 2
    # No real time elapsed — only fake sleeps were recorded, and the first cycle did
    # not sleep at all (it acts on the latest closed bar immediately).
    assert len(slept) == 1
