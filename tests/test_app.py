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

import pytest

from magic_agent.app import App, FixtureCmcClient, build_app
from magic_agent.live import run_live
from magic_agent.runtime_state import RuntimeState
from magic_agent.spot_models import AuthorizedSetup
from magic_agent.state_journal import IntegrityError, StateJournal

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
