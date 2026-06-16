# tests/test_live.py
"""Unit tests for the live poll loop (``run_live``) — injected fake feed, no network.

The loop logic (new-closed-candle gate, dedupe on ts, forwarding policy_config into
``on_candle``) is fully exercised here with a fake ``feed`` and a real ``PaperExecutor``.
Only the real ccxt/scanner feed construction in ``_cmd_run`` is left uncovered.
"""
from __future__ import annotations

from magic_agent.context import CmcContextAdapter
from magic_agent.executor import PaperExecutor
from magic_agent.live import run_live
from magic_agent.models import Candle, Outcome, Setup, Side
from magic_agent.policy import PolicyConfig


def _setup(direction=Side.LONG, rating="A"):
    return Setup("BNB/USDT", direction, rating, "risk_on", 600.0, 588.0, 636.0, "chained_scob")


class _FakeGateway:
    """Stand-in for ScannerGateway — returns a fixed setup, records scan calls."""

    def __init__(self, setup: Setup | None = None):
        self._setup = setup if setup is not None else _setup()
        self.scanned: list[str] = []

    def scan(self, symbol: str) -> Setup | None:
        self.scanned.append(symbol)
        return self._setup


def _fake_feed(pairs):
    """Build a feed callable yielding the given ``(candle, ts)`` pairs in order."""
    it = iter(pairs)

    def feed(symbol):
        return next(it)

    return feed


def test_run_live_processes_three_increasing_candles():
    ex = PaperExecutor(1000.0)
    # A vetoed (below-threshold) setup never opens a position, so the one-position
    # in-position guard never short-circuits: every closed candle reaches setup_fn
    # and logs once, isolating the loop's "one on_candle per new candle" behavior.
    gw = _FakeGateway(_setup(rating="C"))
    records: list[dict] = []
    pairs = [
        (Candle(600, 601, 599, 600), 1000),
        (Candle(601, 602, 600, 601), 2000),
        (Candle(602, 603, 601, 602), 3000),
    ]
    count = run_live(
        ex,
        gateway=gw,
        context=CmcContextAdapter(None),
        feed=_fake_feed(pairs),
        symbol="BNB/USDT",
        policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
        log=records.append,
        max_iters=3,
    )
    assert count == 3
    # on_candle called once per closed candle (each scan() invocation = one processed candle).
    assert gw.scanned == ["BNB/USDT", "BNB/USDT", "BNB/USDT"]
    assert len(records) == 3  # one decision record per processed candle


def test_run_live_skips_duplicate_or_older_ts():
    ex = PaperExecutor(1000.0)
    gw = _FakeGateway(_setup(rating="C"))  # vetoed -> stays flat, every candle scans+logs
    records: list[dict] = []
    pairs = [
        (Candle(600, 601, 599, 600), 1000),
        (Candle(600, 601, 599, 600), 1000),  # duplicate ts -> skipped
        (Candle(600, 601, 599, 600), 900),   # older ts -> skipped
        (Candle(601, 602, 600, 601), 2000),  # new ts -> processed
    ]
    count = run_live(
        ex,
        gateway=gw,
        context=CmcContextAdapter(None),
        feed=_fake_feed(pairs),
        symbol="BNB/USDT",
        policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
        log=records.append,
        max_iters=4,
    )
    # only ts=1000 and ts=2000 are new -> 2 processed (the duplicate/older are skipped).
    assert count == 2
    assert len(gw.scanned) == 2
    assert len(records) == 2


def test_run_live_forwards_policy_config_into_on_candle():
    # A denying policy_config (daily-loss kill-switch tripped) must reach on_candle and
    # block the open -> SKIPPED_POLICY, never an OPENED.
    ex = PaperExecutor(1000.0)
    gw = _FakeGateway()
    records: list[dict] = []
    pairs = [(Candle(600, 601, 599, 600), 1000)]
    count = run_live(
        ex,
        gateway=gw,
        context=CmcContextAdapter(None),
        feed=_fake_feed(pairs),
        symbol="BNB/USDT",
        policy_config=PolicyConfig(max_daily_loss=50.0),
        realized_pnl_today=-100.0,  # kill-switch tripped
        log=records.append,
        max_iters=1,
    )
    assert count == 1
    assert ex.get_position().side is Side.FLAT  # policy blocked the open
    assert records[0]["outcome"] == Outcome.SKIPPED_POLICY.value
