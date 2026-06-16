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
from magic_agent.models import AccountState, Candle, Outcome, PositionState, Setup, Side
from magic_agent.policy import PolicyConfig


def _setup(direction=Side.LONG, rating="A"):
    return Setup("BNB/USDT", direction, rating, "risk_on", 600.0, 588.0, 636.0, "chained_scob")


class _FakeExecutor:
    """Injected executor (no network) whose ``available`` follows a scripted sequence.

    ``get_account`` returns the next value from ``availables`` on each call, holding the
    last value once exhausted. Stays FLAT (never opens) so the live loop reaches the
    policy gate on every candle — this isolates the realized-PnL-delta computation.
    """

    def __init__(self, availables: list[float]):
        self._availables = list(availables)
        self._idx = 0
        self.account_calls: list[float] = []  # mark_price per get_account call

    def get_position(self) -> PositionState:
        return PositionState()  # always FLAT

    def get_account(self, *, mark_price: float) -> AccountState:
        self.account_calls.append(mark_price)
        i = min(self._idx, len(self._availables) - 1)
        self._idx += 1
        avail = self._availables[i]
        return AccountState(equity=avail, available=avail)

    def open_position(self, intent):  # pragma: no cover - never reached (gate denies)
        return Outcome.OPENED

    def close_position(self, *, mark_price: float):  # pragma: no cover
        return Outcome.CLOSED

    def sync(self) -> None:  # pragma: no cover
        return None


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


def test_run_live_forwards_risk_pct_and_leverage_into_on_candle():
    # risk_pct/leverage must reach on_candle (CLI flags were silently no-op'd before).
    captured: list[dict] = []

    def spy_on_candle(executor, **kwargs):
        captured.append(kwargs)
        return None, None

    import magic_agent.live as live_mod

    orig = live_mod.on_candle
    live_mod.on_candle = spy_on_candle
    try:
        run_live(
            PaperExecutor(1000.0),
            gateway=_FakeGateway(),
            context=CmcContextAdapter(None),
            feed=_fake_feed([(Candle(600, 601, 599, 600), 1000)]),
            symbol="BNB/USDT",
            policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
            risk_pct=0.005,
            leverage=5.0,
            max_iters=1,
        )
    finally:
        live_mod.on_candle = orig

    assert captured[0]["risk_pct"] == 0.005
    assert captured[0]["leverage"] == 5.0


def test_run_live_computes_realized_pnl_today_from_executor_delta_trips_killswitch():
    # F2: the daily-loss kill-switch must trip LIVE off the executor's realized-PnL delta.
    # Executor's available is 1000 at session start, then DROPS to 900 (a realized $100 loss)
    # for all subsequent get_account calls. On the SECOND candle on_candle must receive
    # realized_pnl_today == -100, and with max_daily_loss=50 the entry is DENIED.
    ex = _FakeExecutor([1000.0, 900.0])  # 1000 first, 900 thereafter
    gw = _FakeGateway(_setup())  # an allowed A-rated setup (would open if not blocked)
    captured: list[float] = []

    import magic_agent.live as live_mod

    real_on_candle = live_mod.on_candle

    def spy_on_candle(executor, **kwargs):
        captured.append(kwargs["realized_pnl_today"])
        return real_on_candle(executor, **kwargs)

    records: list[dict] = []
    pairs = [
        (Candle(600, 601, 599, 600), 1000),
        (Candle(601, 602, 600, 601), 2000),
    ]
    live_mod.on_candle = spy_on_candle
    try:
        count = run_live(
            ex,
            gateway=gw,
            context=CmcContextAdapter(None),
            feed=_fake_feed(pairs),
            symbol="BNB/USDT",
            policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
            log=records.append,
            max_iters=2,
        )
    finally:
        live_mod.on_candle = real_on_candle

    assert count == 2
    # First candle: session start, no realized loss yet -> 0.0.
    assert captured[0] == 0.0
    # Second candle: available dropped 1000 -> 900 -> realized delta is -100 (the loss).
    assert captured[1] == -100.0
    # Kill-switch (max_daily_loss=50) trips on the second candle -> entry DENIED, stays FLAT.
    assert ex.get_position().side is Side.FLAT
    assert records[-1]["outcome"] == Outcome.SKIPPED_POLICY.value


def test_run_live_no_loss_executor_yields_zero_realized_pnl_today():
    # No realized loss: available stays at the session-start value, so realized_pnl_today
    # is 0.0 on every candle and entries proceed normally (an allowed A-setup OPENS).
    # A real PaperExecutor with no realized loss: ``available`` (== realized cash) stays at
    # the 1000 starting equity even after an open (opens change equity, not available),
    # so the session realized-PnL delta is 0.0.
    ex = PaperExecutor(1000.0)
    gw = _FakeGateway(_setup())
    captured: list[float] = []

    import magic_agent.live as live_mod

    real_on_candle = live_mod.on_candle

    def spy_on_candle(executor, **kwargs):
        captured.append(kwargs["realized_pnl_today"])
        return real_on_candle(executor, **kwargs)

    records: list[dict] = []
    pairs = [(Candle(600, 601, 599, 600), 1000)]
    live_mod.on_candle = spy_on_candle
    try:
        run_live(
            ex,
            gateway=gw,
            context=CmcContextAdapter(None),
            feed=_fake_feed(pairs),
            symbol="BNB/USDT",
            policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
            log=records.append,
            max_iters=1,
        )
    finally:
        live_mod.on_candle = real_on_candle

    assert captured[0] == 0.0
    # No daily-loss veto -> the allowed setup opens normally.
    assert records[-1]["outcome"] == Outcome.OPENED.value


def test_run_live_writes_status_snapshot_per_candle(tmp_path):
    # F4: when a snapshot_path is injected, run_live writes a build_status-shaped
    # snapshot file after processing a candle (cross-process: serve reads this file).
    import json

    ex = PaperExecutor(1000.0)
    gw = _FakeGateway(_setup(rating="C"))  # vetoed -> stays flat, but candle is processed
    snapshot_path = tmp_path / "status.json"
    pairs = [(Candle(600, 601, 599, 600), 1000)]
    count = run_live(
        ex,
        gateway=gw,
        context=CmcContextAdapter(None),
        feed=_fake_feed(pairs),
        symbol="BNB/USDT",
        policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
        snapshot_path=str(snapshot_path),
        mode="paper",
        venue="binance",
        max_iters=1,
    )
    assert count == 1
    assert snapshot_path.exists()
    snap = json.loads(snapshot_path.read_text())
    assert snap["mode"] == "paper"
    assert "equity" in snap
    assert "positions" in snap


def test_run_live_no_snapshot_path_writes_nothing(tmp_path):
    # Backward compatible: snapshot_path=None (default) → no file written.
    ex = PaperExecutor(1000.0)
    gw = _FakeGateway(_setup(rating="C"))
    snapshot_path = tmp_path / "status.json"
    run_live(
        ex,
        gateway=gw,
        context=CmcContextAdapter(None),
        feed=_fake_feed([(Candle(600, 601, 599, 600), 1000)]),
        symbol="BNB/USDT",
        policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
        max_iters=1,
    )
    assert not snapshot_path.exists()


def test_run_live_skips_none_ts_without_advancing_gate():
    # A feed returning (None, None) (e.g. a transient fetch error) is skipped and does
    # NOT advance last_ts, so a subsequent real candle still processes.
    ex = PaperExecutor(1000.0)
    gw = _FakeGateway(_setup(rating="C"))
    records: list[dict] = []
    pairs = [
        (None, None),                         # transient error -> skipped
        (Candle(600, 601, 599, 600), 1000),   # real candle -> processed
    ]
    count = run_live(
        ex,
        gateway=gw,
        context=CmcContextAdapter(None),
        feed=_fake_feed(pairs),
        symbol="BNB/USDT",
        policy_config=PolicyConfig(max_daily_loss=50.0, max_leverage=5.0, require_stop=True),
        log=records.append,
        max_iters=2,
    )
    assert count == 1
    assert len(records) == 1
