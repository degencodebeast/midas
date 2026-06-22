"""Tests for the production App container + ``build_app`` factory + ``cli._cmd_run``.

These are the assembly tests for the paper runtime: ``build_app(mode="paper")``
wires every collaborator ``runner.run_cycle`` reads into one offline, no-funds,
no-network App, and the CLI ``run --executor paper`` drives the shared
``live.run_live`` loop over it. Paper is the default; nothing here touches a
network, a key, or funds.

The booking test proves the load-bearing invariant: a paper cycle books a
position ONLY through the reconcile path (``PositionManager.open_from_reconciliation``
via ``PaperExecutionAdapter.submit``) — never optimistically — and the resulting
swap is a confirmed (``RECONCILED``) record the compliance ledger can count.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from magic_agent.app import App, FixtureCmcClient, build_app
from magic_agent.live import run_live
from magic_agent.runtime_state import RuntimeState
from magic_agent.spot_models import AuthorizedSetup
from magic_agent.state_journal import IntegrityError, StateJournal
from magic_agent.status_store import read_status_snapshot


def _fake_live_balances(*, equity_usd=Decimal("999"), cash_usd=Decimal("999")):
    """A live-balances stub providing both the snapshot and wallet_equity surface
    build_app(mode="twak") now reads (equity = native + USDC, cash = USDC)."""
    return SimpleNamespace(
        snapshot=lambda identity_key: {"stable": "999", "token": "1"},
        wallet_equity=lambda: {"equity_usd": equity_usd, "cash_usd": cash_usd},
    )


class _ControllableFrameSource:
    """A :class:`FrameSource` whose H1 low/high is settable per cycle.

    Drives the paper-exit ``observe`` deterministically: the test mutates ``low``
    and ``high`` between cycles to place the position in range or onto its stop /
    campaign-DOL. ``closed_frames`` returns a single closed H1 bar carrying the
    current low/high (and a matching close), in the feed schema the runtime reads.
    """

    def __init__(self, *, low: Decimal, high: Decimal) -> None:
        self.low = low
        self.high = high

    def _frame(self) -> pd.DataFrame:
        low = float(self.low)
        high = float(self.high)
        close = (low + high) / 2
        index = pd.DatetimeIndex(
            [pd.Timestamp("2026-06-21T11:00:00Z")], name="open_time"
        )
        return pd.DataFrame(
            {
                "open": [close],
                "high": [high],
                "low": [low],
                "close": [close],
                "volume": [1.0],
                "close_time": [pd.Timestamp("2026-06-21T12:00:00Z")],
            },
            index=index,
        )

    def closed_frames(self, candidate) -> dict[str, pd.DataFrame]:
        frame = self._frame()
        return {"1w": frame, "12h": frame, "1h": frame}

# Every attribute/method ``runner.run_cycle`` reads off ``app``. The App must
# expose EXACTLY these (the fake ``app`` in test_spot_runtime is the contract).
_RUN_CYCLE_MEMBERS = (
    "reconcile_unfinished",
    "position_manager",
    "state",
    "risk_policy",
    "cmc_source",
    "exclusion_journal",
    "candidate_source",
    "watchlist",
    "scanner_gateway",
    "executability",
    "lifecycle_evaluator",
    "observe_entry",
    "pipeline",
    "decision_journal",
    "execution_coordinator",
    "compliance",
    "execution_journal",
    "state_journal",
    "publish_status",
)


def _clock(now: datetime):
    ticks = iter([now])

    def tick():
        return next(ticks)

    return tick


def test_build_app_paper_exposes_every_run_cycle_member(tmp_path):
    app = build_app(mode="paper", root_dir=tmp_path)
    assert isinstance(app, App)
    for member in _RUN_CYCLE_MEMBERS:
        assert hasattr(app, member), f"App is missing run_cycle member {member!r}"


def test_paper_cycle_completes_without_raising(tmp_path):
    app = build_app(mode="paper", root_dir=tmp_path)
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    processed = run_live(app, clock=_clock(now), max_iters=1)
    assert processed == 1


def test_paper_cycle_books_only_via_reconcile_path(tmp_path):
    # Inject an authorizing scanner + a single gold candidate so the cycle
    # reaches execution; everything else (risk policy, executability, paper
    # adapter, position manager, recovery) is the real production wiring.
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    setup = AuthorizedSetup.example()
    scanner_gateway = SimpleNamespace(scan=lambda candidate: setup)
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
    )
    run_live(app, clock=_clock(now), max_iters=1)

    # Booked exactly once, and ONLY through the reconcile path: the position
    # appears in the manager's reconcile book, and the paper adapter recorded a
    # confirmed (RECONCILED) swap.
    assert len(app.position_manager.book) == 1
    confirmed = app.execution_coordinator.confirmed_records()
    assert len(confirmed) == 1
    assert confirmed[0].state == "RECONCILED"
    # The booked quantity is the realized (quote) fill, not the raw intent size.
    assert app.position_manager.book[0].quantity > 0


def test_paper_two_cycles_book_one_position_concurrency_cap_denies_second(tmp_path):
    # An authorizing scanner + a single gold candidate so each cycle reaches
    # execution; everything else is the real production wiring. With
    # max_concurrent_positions == 1, booking ONE position must make the second
    # cycle deny entry via concurrency_cap — never a second booking.
    now1 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    now2 = now1 + timedelta(minutes=5)
    setup = AuthorizedSetup.example()
    scanner_gateway = SimpleNamespace(scan=lambda candidate: setup)
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
    )

    ticks = iter([now1, now2])
    run_live(app, clock=lambda: next(ticks), max_iters=2)

    # Exactly ONE position booked across two authorized cycles: the second cycle
    # is denied by the concurrency cap, not booked again.
    assert len(app.position_manager.book) == 1
    confirmed = app.execution_coordinator.confirmed_records()
    assert len(confirmed) == 1
    # The open booked position is visible to the exit feed and the risk snapshot,
    # so the concurrency cap can see it on the next cycle.
    assert len(app.position_manager.positions()) == 1
    assert app.state.risk_state().open_strategy_positions == 1


def test_cmd_run_paper_completes_without_not_implemented(tmp_path):
    from magic_agent import cli

    args = SimpleNamespace(
        executor="paper",
        max_iters=1,
        symbol="BNB/USDT",
        log=str(tmp_path / "decisions.jsonl"),
        root_dir=tmp_path,
    )
    # Must not raise NotImplementedError (the old stub); a paper cycle runs.
    cli._cmd_run(args)


def test_fixture_cmc_client_is_offline_and_returns_zec(tmp_path):
    client = FixtureCmcClient()
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    quotes = client.fetch(symbols=("ZEC",), observed_at=now)
    by_symbol = {q.symbol: q for q in quotes}
    assert "ZEC" in by_symbol
    # Positive 7d momentum so a counter-bias/aligned candidate can pass the gate.
    assert Decimal(by_symbol["ZEC"].momentum_7d) > 0


def test_build_app_paper_works_from_non_repo_root_cwd(tmp_path, monkeypatch):
    """build_app must not raise FileNotFoundError when the process cwd is NOT the repo root.

    The default eligibility_path and identity_path were relative strings, so they
    resolved against cwd and crashed when launched from any directory other than the
    repo root.  After the fix they resolve via the repo anchor embedded in app.py
    (Path(__file__).resolve().parents[N] / "data" / ...) and therefore work
    regardless of cwd.
    """
    # Move the process cwd away from the repo root — reproduces the crash.
    monkeypatch.chdir(tmp_path)

    # Must not raise FileNotFoundError for data/track1_eligibility.json or
    # data/track1_identities.json.
    app = build_app(mode="paper", root_dir=tmp_path)

    # Sanity-check: the eligibility ledger loaded (non-empty).
    assert app.candidate_source is not None


def test_build_app_restores_persisted_runtime_state_on_restart(tmp_path):
    """A restart MUST restore the persisted RuntimeState, not reset to new_session.

    Runtime state (equity, drawdown anchors, canary mode, consecutive-stop count,
    exposure gate) is journaled so an agent that has e.g. drawn down or halted does
    NOT silently resume from a fresh session after a restart. This builds an app,
    persists a distinctive state, then rebuilds against the SAME root and asserts the
    persisted values survive.
    """
    base = tmp_path / ".magic_agent"
    journal = StateJournal(base / "state.json")

    # A distinctive, NON-default state: equity drawn down to 1234, two consecutive
    # stops, canary promoted off, and new exposure blocked (the halted condition).
    persisted = RuntimeState(
        equity_usd=Decimal("1234"),
        cash_usd=Decimal("1234"),
        peak_equity_usd=Decimal("5000"),
        daily_anchor_usd=Decimal("5000"),
        consecutive_stops=2,
        canary_mode=False,
        blocks_new_exposure=True,
    )
    journal.save(persisted.as_dict())

    # Rebuild against the same root — must LOAD the journal, not new_session.
    app = build_app(mode="paper", root_dir=tmp_path, starting_equity=Decimal("5000"))

    assert app.state.equity_usd == Decimal("1234")
    assert app.state.peak_equity_usd == Decimal("5000")
    assert app.state.consecutive_stops == 2
    assert app.state.canary_mode is False
    assert app.state.blocks_new_exposure is True
    # The non-persisted open_positions provider must still be re-wired to the live
    # book so the restored state reflects concurrency correctly.
    assert app.state.open_positions is not None
    assert app.state.risk_state().open_strategy_positions == 0


def test_build_app_fails_closed_on_corrupt_state_file(tmp_path):
    """A corrupt state.json is an operator event: fail closed, never silently reset.

    Falling back to a fresh session on corruption would let a halted agent resume
    trading. build_app must surface the IntegrityError instead.
    """
    base = tmp_path / ".magic_agent"
    base.mkdir(parents=True, exist_ok=True)
    # A wrapper whose sha256 does not match its payload -> IntegrityError on load.
    (base / "state.json").write_text(
        '{"payload": {"equity_usd": "1234"}, "sha256": "deadbeef"}', encoding="utf-8"
    )

    with pytest.raises(IntegrityError):
        build_app(mode="paper", root_dir=tmp_path)


def test_paper_round_trip_stop_hit_closes_position_and_frees_slot(tmp_path):
    """Paper is a real ROUND-TRIP: a booked position whose stop is hit CLOSES.

    On the inert no-op wiring a booked position is never evaluated for exit, so it
    never closes -> ``open_strategy_positions`` stays 1 forever -> the concurrency
    cap blocks every future entry (RED). This drives the full lifecycle through the
    injected frame source:

    * Cycle 1: price in range -> book exactly one position.
    * Cycle 2: H1 low hits the structural stop (90) -> the position CLOSES (book
      empties, ``open_strategy_positions == 0``), freeing the concurrency slot.
    * Cycle 3: price back in range, still authorized -> a NEW position books
      (slot freed).
    """
    now1 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    now2 = now1 + timedelta(minutes=5)
    now3 = now2 + timedelta(minutes=5)
    setup = AuthorizedSetup.example()  # entry 100, stop 90, campaign_dol 120
    # Realistic scanner: a stop-breached structure is no longer a valid setup, so the
    # scanner authorizes ONLY while the H1 low holds above the structural stop. This
    # keeps the close and the next entry in SEPARATE cycles (a stopped-out structure
    # does not immediately re-authorize at the price that just broke it).
    frame_source = _ControllableFrameSource(low=Decimal("95"), high=Decimal("105"))

    def scan(candidate):
        return setup if frame_source.low > setup.structural_stop else None

    scanner_gateway = SimpleNamespace(scan=scan)
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        frame_source=frame_source,
    )

    # Cycle 1: price in range -> book exactly one position.
    run_live(app, clock=_clock(now1), max_iters=1)
    assert len(app.position_manager.book) == 1
    assert app.state.risk_state().open_strategy_positions == 1

    # Cycle 2: H1 low hits the stop (90) -> the position CLOSES and the slot frees.
    # The scanner no longer authorizes (broken structure), so the book ends EMPTY.
    frame_source.low = Decimal("89")
    run_live(app, clock=_clock(now2), max_iters=1)
    assert app.position_manager.book == []
    assert len(app.position_manager.positions()) == 0
    assert app.state.risk_state().open_strategy_positions == 0
    # The close was real: the exited position is gone, not assumed-held.
    assert all(p.intent_id != "intent:setup-1" for p in app.position_manager.book)

    # Cycle 3: price back in range, authorized again -> a NEW position books (the
    # freed slot is reusable — proving the concurrency cap no longer blocks entry).
    frame_source.low = Decimal("95")
    run_live(app, clock=_clock(now3), max_iters=1)
    assert len(app.position_manager.book) == 1


def test_paper_exit_cycle_blocks_same_cycle_re_entry_even_when_scanner_authorizes(tmp_path):
    """An exit cycle is an EXIT-ONLY cycle: a stop-close must not re-enter same-cycle.

    Adversarial: the scanner ALWAYS authorizes (it returns a setup every cycle, even on
    the bar that just stopped the position out). The runtime must NOT rely on the scanner
    going silent on the stop bar to avoid churn — when ``process_exits`` closes a position
    this cycle, ``run_cycle`` must defer any new entry to the NEXT cycle. Without the
    guard, the freed concurrency slot is re-used in the SAME cycle: the close cycle books a
    fresh position (instant re-entry / churn), so ``confirmed_records`` jumps from 1 to 2.

    * Cycle 1: price in range -> book exactly one position (1 confirmed record).
    * Cycle 2: H1 low hits the structural stop (90) -> the position CLOSES. Even though the
      scanner still authorizes, NO new entry is booked this cycle: the book ends EMPTY and
      ``confirmed_records`` stays at 1 (no churn).
    * Cycle 3: no exit this cycle, still authorized -> a NEW entry IS allowed (2 confirmed).
    """
    now1 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    now2 = now1 + timedelta(minutes=5)
    now3 = now2 + timedelta(minutes=5)
    setup = AuthorizedSetup.example()  # entry 100, stop 90, campaign_dol 120
    # ALWAYS-AUTHORIZING scanner: returns a setup every cycle, even on the stop bar. The
    # runtime must own the no-churn guard, not lean on the scanner going silent.
    scanner_gateway = SimpleNamespace(scan=lambda candidate: setup)
    frame_source = _ControllableFrameSource(low=Decimal("95"), high=Decimal("105"))
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        frame_source=frame_source,
    )

    # Cycle 1: price in range -> book exactly one position.
    run_live(app, clock=_clock(now1), max_iters=1)
    assert len(app.position_manager.book) == 1
    assert len(app.execution_coordinator.confirmed_records()) == 1

    # Cycle 2: H1 low hits the stop (90) -> the position CLOSES. The scanner STILL
    # authorizes, but this is an exit-only cycle: no new entry is booked same-cycle.
    frame_source.low = Decimal("89")
    run_live(app, clock=_clock(now2), max_iters=1)
    assert app.position_manager.book == []
    assert len(app.position_manager.positions()) == 0
    assert app.state.risk_state().open_strategy_positions == 0
    # The load-bearing no-churn assert: the stop cycle did NOT open a fresh position.
    # On buggy code this is 2 (close cycle re-entered same-cycle); the guard keeps it 1.
    assert len(app.execution_coordinator.confirmed_records()) == 1

    # Cycle 3: price back in range, no exit this cycle, still authorized -> a NEW entry
    # IS allowed (deferred re-entry on a later, non-exit cycle is correct).
    frame_source.low = Decimal("95")
    run_live(app, clock=_clock(now3), max_iters=1)
    assert len(app.position_manager.book) == 1
    assert len(app.execution_coordinator.confirmed_records()) == 2


def test_paper_round_trip_close_is_durable_across_restart(tmp_path):
    """After a stop-hit CLOSE, a restart must NOT resurrect the closed position.

    The close drops the position from the book, and ``run_cycle`` persists the
    (now empty) book to ``positions.json``. A ``build_app`` restart over the same
    base must restore an EMPTY book — never the closed position.
    """
    now1 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    now2 = now1 + timedelta(minutes=5)
    setup = AuthorizedSetup.example()
    frame_source = _ControllableFrameSource(low=Decimal("95"), high=Decimal("105"))

    def scan(candidate):
        return setup if frame_source.low > setup.structural_stop else None

    scanner_gateway = SimpleNamespace(scan=scan)
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        frame_source=frame_source,
    )

    # Cycle 1: book; cycle 2: stop hit -> close (persisted by run_cycle).
    run_live(app, clock=_clock(now1), max_iters=1)
    assert len(app.position_manager.book) == 1
    frame_source.low = Decimal("89")
    run_live(app, clock=_clock(now2), max_iters=1)
    assert app.position_manager.book == []

    # RESTART over the same base: the closed position must NOT be resurrected. The
    # restart frame holds the breached price so no fresh entry confounds the assert.
    app2 = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        frame_source=frame_source,
    )
    assert app2.position_manager.book == []
    assert app2.state.risk_state().open_strategy_positions == 0


def test_build_app_fresh_base_still_new_sessions(tmp_path):
    """No state.json present -> a fresh new_session from starting_equity."""
    app = build_app(mode="paper", root_dir=tmp_path, starting_equity=Decimal("7777"))
    assert app.state.equity_usd == Decimal("7777")
    assert app.state.peak_equity_usd == Decimal("7777")
    assert app.state.canary_mode is True
    assert app.state.blocks_new_exposure is False
    assert app.state.open_positions is not None


def test_paper_booked_position_survives_restart_and_blocks_re_entry(tmp_path):
    """A booked paper position is DURABLE across a restart (no cross-restart re-entry).

    The same-process concurrency cap is already enforced (#65-H) and runtime state
    restores on restart (#65-I), but the open POSITION itself was not persisted: the
    PositionManager.book started EMPTY on restart, so the next authorized cycle
    re-entered the same setup (cross-restart double-entry). This is the missing
    combined test: book exactly one position, let run_cycle persist, REBUILD over the
    same base (restart), and assert the restored book carries the position, the risk
    snapshot reflects the open count, the position is exit-manageable, and a second
    cycle does NOT book again (denied by the concurrency cap).
    """
    now1 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    now2 = now1 + timedelta(minutes=5)
    setup = AuthorizedSetup.example()
    scanner_gateway = SimpleNamespace(scan=lambda candidate: setup)

    # Cycle 1: book exactly one position, run_cycle persists at the end.
    app1 = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
    )
    run_live(app1, clock=_clock(now1), max_iters=1)
    assert len(app1.position_manager.book) == 1
    booked_intent_id = app1.position_manager.book[0].intent_id
    booked_quantity = app1.position_manager.book[0].quantity

    # RESTART: rebuild over the SAME base. The durable position store must restore
    # the open position into the book.
    app2 = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
    )

    # The restored book is NON-EMPTY and carries the same position.
    assert len(app2.position_manager.book) == 1
    restored = app2.position_manager.book[0]
    assert restored.intent_id == booked_intent_id
    assert restored.quantity == booked_quantity
    # A complete exit-source citizen: it carries its quantity + stressed_loss_per_unit
    # (entry 100 - structural_stop 90 == 10) so process_exits can order/manage it.
    assert restored.stressed_loss_per_unit == Decimal("10")
    # The exit feed and the risk snapshot both see the restored open position, so the
    # concurrency cap is live on the next cycle.
    assert len(app2.position_manager.positions()) == 1
    assert app2.state.open_positions is not None
    assert app2.state.risk_state().open_strategy_positions == 1

    # Second cycle after restart: the concurrency cap denies a SECOND booking.
    run_live(app2, clock=_clock(now2), max_iters=1)
    assert len(app2.position_manager.book) == 1
    assert len(app2.execution_coordinator.confirmed_records()) == 0


def test_build_app_fails_closed_on_corrupt_positions_store(tmp_path):
    """A corrupt positions store is an operator event: fail closed, never empty-book.

    Starting with an empty book when a persisted position cannot be cleanly restored
    would re-enter the same setup. Consistent with #65-I's corrupt-state behavior,
    build_app must surface the IntegrityError instead of silently dropping positions.
    """
    base = tmp_path / ".magic_agent"
    base.mkdir(parents=True, exist_ok=True)
    # A wrapper whose sha256 does not match its payload -> IntegrityError on load.
    (base / "positions.json").write_text(
        '{"payload": [{"intent_id": "x", "quantity": "1", "stressed_loss_per_unit": "0"}],'
        ' "sha256": "deadbeef"}',
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError):
        build_app(mode="paper", root_dir=tmp_path)


def test_paper_cycle_publishes_live_status_snapshot(tmp_path):
    """Each paper cycle must publish a LIVE status snapshot the dashboard reads.

    The ``magic-agent serve`` ``/api/status`` reader loads ``<base>/status.json`` and
    only falls back to a hardcoded ``mode="demo"`` placeholder when that file is
    absent. The spot loop never wrote it, so the dashboard was permanently in demo
    mode. After a paper ``run_cycle`` over a temp base, ``status.json`` must EXIST and
    reflect the live runtime — NOT the demo fallback:

    * ``mode`` is the run mode (``"paper"``), never ``"demo"``.
    * ``equity`` matches the live ``RuntimeState`` equity (a float, JSON-safe).
    * ``positions`` count matches the open position book.
    * ``halted`` is a real bool derived from the live halt state.
    """
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    setup = AuthorizedSetup.example()
    scanner_gateway = SimpleNamespace(scan=lambda candidate: setup)
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        starting_equity=Decimal("10000"),
    )

    run_live(app, clock=_clock(now), max_iters=1)

    # The cycle booked exactly one position (the live book the snapshot must reflect).
    assert len(app.position_manager.book) == 1

    # The snapshot file exists under the App's base dir and is readable.
    status_path = tmp_path / ".magic_agent" / "status.json"
    assert status_path.exists(), "run_cycle did not publish a status snapshot"
    snapshot = read_status_snapshot(status_path)
    assert snapshot is not None

    # It reflects the LIVE runtime — never the demo fallback.
    assert snapshot["mode"] == "paper", f"expected live paper mode, got {snapshot!r}"
    assert snapshot["mode"] != "demo"
    assert snapshot["equity"] == float(app.state.equity_usd)
    assert snapshot["available"] == float(app.state.cash_usd)
    assert len(snapshot["positions"]) == len(app.position_manager.book)
    assert isinstance(snapshot["halted"], bool)
    assert snapshot["halted"] is False  # a fresh, fully-funded session is not halted.


def test_paper_status_snapshot_projects_real_entry_and_take_profit(tmp_path):
    """An OPEN position's status projection must show the REAL entry/stop/take-profit.

    The dashboard renders ``entry_price``/``stop_loss``/``take_profit`` from this
    snapshot directly. A freshly-booked gold position (entry 100, stop 90, campaign
    DOL 120) must surface those levels — NOT a hardcoded ``entry_price=0.0`` or a
    ``take_profit=None`` (which left the dashboard showing "Entry 0.00 / TP —").
    """
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    setup = AuthorizedSetup.example()  # entry 100, stop 90, campaign_dol 120
    scanner_gateway = SimpleNamespace(scan=lambda candidate: setup)
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        starting_equity=Decimal("10000"),
    )

    run_live(app, clock=_clock(now), max_iters=1)
    assert len(app.position_manager.book) == 1

    status_path = tmp_path / ".magic_agent" / "status.json"
    snapshot = read_status_snapshot(status_path)
    assert snapshot is not None
    assert len(snapshot["positions"]) == 1
    position = snapshot["positions"][0]
    assert position["entry_price"] == 100.0, f"entry must be the setup entry, got {position!r}"
    assert position["stop_loss"] == 90.0
    assert position["take_profit"] == 120.0, f"take_profit must be campaign DOL, got {position!r}"


def test_restarted_position_still_projects_real_entry_and_take_profit(tmp_path):
    """A RESTARTED position must STILL project its real entry/take-profit.

    Proves persistence carries ``entry`` (and ``campaign_dol``) across a restart: after
    booking + persisting and rebuilding over the same base, the restored position's
    status projection still shows entry 100 / stop 90 / take-profit 120 — not 0.0/None.
    """
    now1 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    now2 = now1 + timedelta(minutes=5)
    setup = AuthorizedSetup.example()  # entry 100, stop 90, campaign_dol 120
    scanner_gateway = SimpleNamespace(scan=lambda candidate: setup)

    app1 = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        starting_equity=Decimal("10000"),
    )
    run_live(app1, clock=_clock(now1), max_iters=1)
    assert len(app1.position_manager.book) == 1

    # RESTART over the same base; the durable store must restore ``entry`` too.
    app2 = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=scanner_gateway,
        gold_candidate_symbol="ZEC",
        starting_equity=Decimal("10000"),
    )
    assert len(app2.position_manager.book) == 1
    assert app2.position_manager.book[0].entry == Decimal("100")

    # Republish status over the restored book and assert the projection is intact.
    app2.publish_status()
    snapshot = read_status_snapshot(tmp_path / ".magic_agent" / "status.json")
    assert snapshot is not None
    assert len(snapshot["positions"]) == 1
    position = snapshot["positions"][0]
    assert position["entry_price"] == 100.0
    assert position["stop_loss"] == 90.0
    assert position["take_profit"] == 120.0


def _twak_env(monkeypatch):
    for name in (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_WALLET_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    ):
        monkeypatch.setenv(name, "test-secret")


def _live_after_entry_app(tmp_path, monkeypatch, *, sell_quote, twak_json=None):
    """A twak-mode App ready to drive after_entry_submission with the REAL module
    cost gate (cost_viability=None) and an injected live_sell_quote producer."""
    from magic_agent.spot_models import ActionPurpose, SpotIntent

    _twak_env(monkeypatch)
    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=SimpleNamespace(scan=lambda candidate: None),
        cmc_client=FixtureCmcClient(),
        frame_source=SimpleNamespace(),
        twak_runner=SimpleNamespace(json=twak_json or (lambda args, timeout=60: {})),
        live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
        live_balances=_fake_live_balances(),
    )
    # Use the REAL module cost gate (not the injected fake) so we exercise the
    # buy_spread + sell_spread accounting.
    app.cost_viability = None
    # Narrative journaling is orthogonal to the cost-viability path under test.
    app.agent_narrative = None
    # Inject the live sell-quote producer under test.
    app.live_sell_quote = sell_quote
    setup = AuthorizedSetup.example(identity_key="zec-bsc")
    intent = SpotIntent("intent-1", setup, Decimal("5"), "buy", ActionPurpose.STRATEGY)
    risk = SimpleNamespace(risk_fraction=Decimal("0.0025"), final_qty=Decimal("5"))
    return app, intent, risk


# A buy quote with a SMALL spread (output 100, min 99.5 => 50 bps).
def _buy_live_quote():
    return {
        "output_qty": "100",
        "minimum_output": "99.5",
        "price_impact": "0",
    }


def _sell_live_quote(min_output):
    # Sell: output is USDC out, minimum_output is the min USDC out.
    return {
        "output_qty": "100",
        "minimum_output": str(min_output),
        "price_impact": "0",
    }


def test_live_after_entry_uses_real_sell_quote_not_duplicated_buy(tmp_path, monkeypatch):
    # buy_spread = 50 bps; sell quote has its OWN spread (output 100, min 99.0 =>
    # 100 bps). Round-trip = 50 + 100 = 150 bps == cap => cost-viable, PROMOTES.
    # If the buy quote were DUPLICATED as the sell quote, the round-trip would be
    # 50 + 50 = 100 bps too (still <=150) — so to PROVE we used the real sell quote,
    # the evidence must show sell_spread_bps == "100.00", not "50.00".
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    captured = {}

    def sell_quote(intent):
        captured["called"] = True
        return _sell_live_quote("99.0")

    app, intent, risk = _live_after_entry_app(tmp_path, monkeypatch, sell_quote=sell_quote)
    app.state.canary_mode = True
    app.state.auto_promote_after_canary = True

    app.after_entry_submission(intent=intent, result="RECONCILED", quote=_buy_live_quote(), risk=risk, now=now)

    assert captured.get("called") is True
    ev = app.state.cost_viability_evidence
    assert ev["buy_spread_bps"] == "50.00"
    assert ev["sell_spread_bps"] == "100.00"  # the REAL sell leg, not 2x buy
    assert ev["estimated_round_trip_cost_bps"] == "150.00"
    assert app.state.canary_mode is False
    assert app.state.promotion_reason == "canary_reconciled_cost_viable"


def test_live_after_entry_too_wide_round_trip_stays_canary(tmp_path, monkeypatch):
    # Sell spread is huge (output 100, min 90 => 1000 bps). Round-trip well over the
    # 150 bps cap => cost_viability_failed, stays canary.
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app, intent, risk = _live_after_entry_app(
        tmp_path, monkeypatch, sell_quote=lambda intent: _sell_live_quote("90"),
    )
    app.state.canary_mode = True
    app.state.auto_promote_after_canary = True

    app.after_entry_submission(intent=intent, result="RECONCILED", quote=_buy_live_quote(), risk=risk, now=now)

    assert app.state.canary_mode is True
    assert app.state.promotion_reason == "cost_viability_failed"


def test_live_after_entry_missing_sell_quote_fails_closed(tmp_path, monkeypatch):
    # The live sell quote can't be obtained (port error / rejected) => fail closed:
    # do NOT promote, stay canary with a clear reason. A missing sell quote must
    # NEVER auto-promote.
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app, intent, risk = _live_after_entry_app(
        tmp_path, monkeypatch, sell_quote=lambda intent: None,
    )
    app.state.canary_mode = True
    app.state.auto_promote_after_canary = True

    app.after_entry_submission(intent=intent, result="RECONCILED", quote=_buy_live_quote(), risk=risk, now=now)

    assert app.state.canary_mode is True
    assert app.state.promotion_reason == "cost_viability_failed"


def test_build_app_twak_fresh_session_sizes_off_wallet_equity(tmp_path, monkeypatch):
    # FIX A: a fresh live session must size off the REAL wallet, NOT the $10k paper
    # default. equity = native USD + USDC; cash = USDC (deployable stable).
    _twak_env(monkeypatch)
    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=SimpleNamespace(scan=lambda candidate: None),
        cmc_client=FixtureCmcClient(),
        frame_source=SimpleNamespace(),
        twak_runner=SimpleNamespace(json=lambda args, timeout=60: {}),
        live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
        live_balances=_fake_live_balances(
            equity_usd=Decimal("10.62") + Decimal("10.034925928487288539"),
            cash_usd=Decimal("10.034925928487288539"),
        ),
    )

    assert app.state.equity_usd == Decimal("10.62") + Decimal("10.034925928487288539")
    assert app.state.cash_usd == Decimal("10.034925928487288539")
    # Peak / daily anchor seed to equity; the canary stays armed.
    assert app.state.peak_equity_usd == app.state.equity_usd
    assert app.state.daily_anchor_usd == app.state.equity_usd
    assert app.state.canary_mode is True
    # NOT the paper default.
    assert app.state.equity_usd != Decimal("10000")


def test_build_app_paper_still_sizes_off_default_equity(tmp_path):
    # PAPER is unchanged: fresh session still seeds the $10k simulated book size.
    app = build_app(mode="paper", root_dir=tmp_path)
    assert app.state.equity_usd == Decimal("10000")
    assert app.state.cash_usd == Decimal("10000")


def test_build_app_twak_fails_closed_when_wallet_equity_read_raises(tmp_path, monkeypatch):
    # FIX A fail-closed: if the live wallet read RAISES at startup, build_app must
    # propagate (operator sees the error), NOT silently fall back to the $10k paper
    # default (which would mis-size).
    from magic_agent.twak import TwakError

    _twak_env(monkeypatch)

    def _raise():
        raise TwakError("wallet read failed")

    bad_balances = SimpleNamespace(
        snapshot=lambda identity_key: {"stable": "999", "token": "1"},
        wallet_equity=_raise,
    )
    with pytest.raises(TwakError):
        build_app(
            mode="twak",
            root_dir=tmp_path,
            scanner_gateway=SimpleNamespace(scan=lambda candidate: None),
            cmc_client=FixtureCmcClient(),
            frame_source=SimpleNamespace(),
            twak_runner=SimpleNamespace(json=lambda args, timeout=60: {}),
            live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
            live_balances=bad_balances,
        )


def test_build_app_twak_restart_keeps_persisted_equity_not_wallet(tmp_path, monkeypatch):
    # On RESTART (an existing state.json) build_app restores the persisted equity and
    # does NOT overwrite it from the wallet (continuous live-equity tracking is a
    # documented follow-up).
    _twak_env(monkeypatch)
    base = tmp_path / ".magic_agent"
    base.mkdir(parents=True, exist_ok=True)
    persisted = RuntimeState.new_live_session(Decimal("123.45"), Decimal("50.00"))
    StateJournal(base / "state.json").save(persisted.as_dict())

    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=SimpleNamespace(scan=lambda candidate: None),
        cmc_client=FixtureCmcClient(),
        frame_source=SimpleNamespace(),
        twak_runner=SimpleNamespace(json=lambda args, timeout=60: {}),
        live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
        live_balances=_fake_live_balances(equity_usd=Decimal("999"), cash_usd=Decimal("999")),
    )

    # Restored from state.json, NOT re-read from the wallet (999).
    assert app.state.equity_usd == Decimal("123.45")
    assert app.state.cash_usd == Decimal("50.00")


def test_live_after_entry_sell_quote_probe_raise_is_crash_safe(tmp_path, monkeypatch):
    # FIX B: a transient twak error in the live sell-quote probe must NOT raise out of
    # after_entry_submission (which would crash the live poll loop AFTER the buy is
    # booked). Treated as "no quote" => fail closed: stay canary, no promotion.
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)

    def boom(intent):
        raise RuntimeError("transient probe failure")

    app, intent, risk = _live_after_entry_app(tmp_path, monkeypatch, sell_quote=boom)
    app.state.canary_mode = True
    app.state.auto_promote_after_canary = True

    # Must NOT raise.
    app.after_entry_submission(intent=intent, result="RECONCILED", quote=_buy_live_quote(), risk=risk, now=now)

    # Position stays in canary (no promotion), with a clear fail-closed reason.
    assert app.state.canary_mode is True
    assert app.state.promotion_reason == "cost_viability_failed"


def test_build_app_twak_wires_real_live_ports_when_injected(tmp_path, monkeypatch):
    from types import SimpleNamespace

    for name in (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_WALLET_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    ):
        monkeypatch.setenv(name, "test-secret")

    fake_scanner = SimpleNamespace(scan=lambda candidate: None)
    fake_cmc = FixtureCmcClient()
    fake_frame_source = SimpleNamespace()
    fake_twak = SimpleNamespace(json=lambda args, timeout=60: {"success": True, "data": {}})
    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=fake_scanner,
        cmc_client=fake_cmc,
        frame_source=fake_frame_source,
        twak_runner=fake_twak,
        live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
        live_balances=_fake_live_balances(),
    )

    assert app.mode == "twak"
    assert app.execution_coordinator.__class__.__name__ == "ExecutionCoordinator"
    assert app.state.canary_mode is True


def test_build_app_twak_rpc_default_is_real_bsc_client(tmp_path, monkeypatch):
    from types import SimpleNamespace
    for name in ("TWAK_ACCESS_ID","TWAK_HMAC_SECRET","TWAK_WALLET_PASSWORD","BSC_RPC_URL","CMC_API_KEY","WALLET_ADDRESS"):
        monkeypatch.setenv(name, "test-secret")
    fake_twak = SimpleNamespace(json=lambda args, timeout=60: {"success": True, "data": {}})
    # No live_rpc injected => the default is the real BscRpcClient (reads BSC_RPC_URL).
    # Construction is lazy (no network), so this assembles without any chain call.
    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=SimpleNamespace(scan=lambda candidate: None),
        frame_source=SimpleNamespace(),
        twak_runner=fake_twak,
        live_balances=_fake_live_balances(),
    )
    assert app.execution_coordinator.rpc.__class__.__name__ == "BscRpcClient"


def test_build_app_twak_still_fails_closed_without_required_secrets(tmp_path, monkeypatch):
    for name in (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_WALLET_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="missing live secret"):
        build_app(mode="twak", root_dir=tmp_path)


def test_build_app_twak_assembles_live_ports_and_canary_layer_with_fakes(tmp_path, monkeypatch):
    for name in (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_WALLET_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    ):
        monkeypatch.setenv(name, "test-secret")

    fake_scanner = SimpleNamespace(scan=lambda candidate: None)
    fake_cmc = FixtureCmcClient()
    fake_frame_source = SimpleNamespace()
    fake_twak = SimpleNamespace(json=lambda args, timeout=60: {"success": True, "data": {}})
    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=fake_scanner,
        cmc_client=fake_cmc,
        frame_source=fake_frame_source,
        twak_runner=fake_twak,
        live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
        live_balances=_fake_live_balances(),
    )

    assert app.mode == "twak"
    assert app.execution_coordinator.__class__.__name__ == "ExecutionCoordinator"
    assert app.executability.__class__.__name__ == "ExecutabilityAdapter"
    assert app.state.canary_mode is True
    assert app.agent_narrative is not None
    assert hasattr(app, "evaluate_cost_viability")
    assert hasattr(app, "update_qualification_pace")
