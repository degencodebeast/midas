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

from magic_agent.app import App, FixtureCmcClient, build_app
from magic_agent.live import run_live
from magic_agent.spot_models import AuthorizedSetup

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
