"""The production runtime container (``App``) + the ``build_app`` factory.

``App`` is the assembled runtime object ``runner.run_cycle`` reads each cycle. It
holds the wired collaborators (scanner gateway, executability, the execution port,
journals, compliance, runtime state) and exposes EXACTLY the attribute/method
surface ``run_cycle`` touches — no more. ``build_app(mode="paper")`` wires the
fully offline, no-funds, no-network paper runtime; ``mode="twak"`` (live) is out of
scope here (a clearly-marked stub).

Execution-journal role split (the load-bearing binding)
-------------------------------------------------------
``run_cycle`` reads ``app.execution_journal`` for TWO different things:

* ``app.execution_journal.confirmed_records()`` — fed to the compliance ledger.
* ``app.execution_journal.records`` (a dict) — scanned by ``recovery.reconcile_unfinished``.

No single object has both members: the real :class:`ExecutionJournal` has ``.records``
but not ``.confirmed_records()``; :class:`PaperExecutionAdapter` has
``.confirmed_records()`` but not ``.records``. We resolve this cleanly WITHOUT bolting
methods onto either class:

* ``app.execution_journal`` is bound to the **paper adapter** (so ``confirmed_records()``
  works for compliance, counting the simulated reconciled swaps).
* ``App.reconcile_unfinished()`` closes over a **real ExecutionJournal** and passes IT
  to ``recovery.reconcile_unfinished``. In paper there is no on-chain swap, so this
  journal is empty / all-terminal and never blocks new exposure.

So each consumer sees an object that has exactly the member it needs, and neither
class is modified.

No-optimistic-booking invariant
-------------------------------
The paper execution port is :class:`PaperExecutionAdapter`, used as BOTH
``app.execution_coordinator`` (its ``.submit`` is called by ``run_cycle``) and
``app.execution_journal`` (its ``.confirmed_records()`` is read by compliance). It
books positions only through :meth:`PositionManager.open_from_reconciliation` — the
same reconcile path the live coordinator uses — never optimistically off the intent.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from magic_agent.agent_narrative import AgentNarrativeJournal, live_entry_reconciled_event
from magic_agent.candidate_source import CandidateSource
from magic_agent.cmc_source import CmcCandidateSource, CoinMarketCapClient, RawCmcQuote
from magic_agent.cost_viability import evaluate_cost_viability as _default_evaluate_cost_viability
from magic_agent.compliance import ComplianceLedger
from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.eligibility import EligibilityLedger
from magic_agent.execution_coordinator import ExecutionCoordinator
from magic_agent.execution_journal import ExecutionJournal, ExecutionState
from magic_agent.frames import FixtureFrameSource, GateioFrameSource
from magic_agent.live_balances import TwakBalanceReader
from magic_agent.live_exits import TwakSellPorts
from magic_agent.live_positions import (
    intents_from_journal,
    rebuild_positions_from_chain,
)
from magic_agent.live_quotes import TwakQuoteProvider
from magic_agent.identity_registry import IdentityRegistry
from magic_agent.lifecycle import LifecycleEvaluator
from magic_agent.live_rpc import BscRpcClient
from magic_agent.paper_adapter import PaperExecutionAdapter
from magic_agent.paper_exits import PaperExitPorts
from magic_agent.position_manager import PositionManager
from magic_agent.position_store import PositionStore
from magic_agent.qualification import QualificationConfig, evaluate_qualification_pace
from magic_agent.quotes import ExecutabilityAdapter, PaperQuoteProvider
from magic_agent.risk_policy import RiskConfig, RiskPolicy
from magic_agent import recovery
from magic_agent.runtime_journals import DecisionJournal, ExclusionJournal
from magic_agent.runtime_state import RuntimeState
from magic_agent.scanner_gateway import ScannerGateway
from magic_agent.state_journal import StateJournal
from magic_agent.twak import TwakRunner
from magic_agent.watchlist import WatchlistManager, WatchlistState

_log = logging.getLogger(__name__)

# Pinned scanner commit recorded on every authorized setup (replay provenance).
SCANNER_COMMIT = "5f92552e8fdd688808e2709eefc176ab681b7f4f"

# Repo root anchor (same technique as _DEFAULT_FIXTURE_DIR below).
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
# Default data inputs (committed, offline) — resolved absolutely so build_app
# works regardless of the process cwd (callers may still pass an explicit path).
_DEFAULT_ELIGIBILITY_PATH = _DATA_DIR / "track1_eligibility.json"
_DEFAULT_IDENTITY_PATH = _DATA_DIR / "track1_identities.json"
# Committed frame fixtures for offline paper runs (ZEC at minimum).
_DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "frames"

# Paper starting equity (no funds — this is a simulated book size).
_DEFAULT_STARTING_EQUITY = Decimal("10000")

# Live (twak) mode requires every one of these to be present and non-empty, or
# build_app fails closed BEFORE constructing any live port (no half-wired live runtime).
_LIVE_REQUIRED_ENV = (
    "TWAK_ACCESS_ID",
    "TWAK_HMAC_SECRET",
    "TWAK_WALLET_PASSWORD",
    "BSC_RPC_URL",
    "CMC_API_KEY",
    "WALLET_ADDRESS",
)


def _require_live_env() -> None:
    missing = [name for name in _LIVE_REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing live secret(s): {', '.join(missing)}")


# Relative tolerance for deciding a restored LIVE state's equity is "consistent" with
# the freshly-read wallet equity. A live wallet is read at most a cycle ago, so any
# meaningful divergence (let alone a paper $10k vs a $20 wallet) means the file is
# stale/contaminated and must be discarded. 1% is generous for the same-wallet case
# while still catching every cross-mode mismatch.
_LIVE_EQUITY_TOLERANCE = Decimal("0.01")


def _live_state_is_wallet_consistent(restored: RuntimeState, wallet_equity_usd: Decimal) -> bool:
    """True only when a restored LIVE state may safely keep its canary/gate fields.

    Requires BOTH: (1) the state was written by the LIVE (``"twak"``) run — a
    paper-written or legacy/untagged (``None``) state is treated as cross-mode and
    rejected; and (2) its equity is within :data:`_LIVE_EQUITY_TOLERANCE` of the real
    wallet equity. A $10k paper book against a $20 wallet fails on both counts. When
    in doubt (e.g. ``wallet_equity_usd == 0``) we reject and re-baseline — fail safe =
    size off real money.
    """
    if restored.mode != "twak":
        return False
    if wallet_equity_usd <= 0:
        return False
    diff = abs(restored.equity_usd - wallet_equity_usd)
    return diff <= wallet_equity_usd * _LIVE_EQUITY_TOLERANCE


def _rebaseline_live_state(
    restored: RuntimeState, wallet_equity_usd: Decimal, wallet_cash_usd: Decimal,
) -> RuntimeState:
    """Re-base a wallet-consistent LIVE state's money fields onto the real wallet.

    Preserves the operationally-meaningful restored fields (canary mode/completion,
    consecutive-stop count, exposure gate, promotion bookkeeping) but FORCES
    equity/cash AND the drawdown anchors (peak/daily) to the wallet, so the risk
    policy sizes off real money and never carries a stale peak that would fabricate a
    drawdown. Only reached when :func:`_live_state_is_wallet_consistent` is True.
    """
    from dataclasses import replace

    return replace(
        restored,
        equity_usd=wallet_equity_usd,
        cash_usd=wallet_cash_usd,
        peak_equity_usd=wallet_equity_usd,
        daily_anchor_usd=wallet_equity_usd,
        mode="twak",
    )


class FixtureCmcClient:
    """Deterministic, OFFLINE CMC client: no network, no keys, no x402.

    ``fetch`` returns one :class:`RawCmcQuote` per requested monitoring symbol it
    knows about (at least ZEC, to match the committed frame fixture). Momentum is
    positive so a counter-bias/aligned candidate can clear the 7d momentum gate;
    unknown symbols are simply omitted (the upstream source journals them as
    ``cmc_missing``), exactly as a sparse live response would behave.
    """

    # Pinned monitoring symbols with deterministic positive momentum. Only ZEC is
    # included offline: its frame fixture is the one committed under
    # tests/fixtures/frames, so it is the only symbol the offline scanner can scan.
    # Any other monitored symbol with no CMC quote here is journaled upstream as
    # cmc_missing and never reaches the scanner (which would otherwise raise on a
    # missing fixture). cmc_id matches the committed Track-1 identity for ZEC.
    _QUOTES: dict[str, tuple[int, str, str]] = {
        # symbol: (cmc_id, momentum_7d, momentum_30d)
        "ZEC": (1437, "0.12", "0.20"),
    }

    def fetch(self, symbols: tuple[str, ...], observed_at: datetime) -> tuple[RawCmcQuote, ...]:
        """Return offline quotes for the known monitoring ``symbols``."""
        quotes: list[RawCmcQuote] = []
        for symbol in symbols:
            spec = self._QUOTES.get(symbol)
            if spec is None:
                continue
            cmc_id, momentum_7d, momentum_30d = spec
            quotes.append(RawCmcQuote(cmc_id, symbol, momentum_7d, momentum_30d))
        return tuple(quotes)


class _GoldOverrideRegistry:
    """Wrap an :class:`IdentityRegistry`, forcing one symbol's verification to gold.

    Paper execution requires a GOLD identity (``execution_eligible``). The committed
    Track-1 dataset has a single gold identity (APE) but no APE frame fixture, so for
    a self-contained offline paper run that actually books we may promote the
    fixtured symbol (ZEC) to gold WITHOUT mutating the committed data file. Every
    other lookup delegates unchanged.
    """

    def __init__(self, registry: IdentityRegistry, gold_symbol: str) -> None:
        self._registry = registry
        self._gold_symbol = gold_symbol

    def _promote(self, record: Any) -> Any:
        if record is not None and record.competition_symbol == self._gold_symbol:
            from dataclasses import replace

            return replace(record, verification_status="gold")
        return record

    def by_symbol(self, symbol: str) -> Any:
        return self._promote(self._registry.by_symbol(symbol))

    def get_by_symbol(self, symbol: str) -> Any:
        return self._promote(self._registry.get_by_symbol(symbol))

    def by_contract(self, address: str) -> Any:
        return self._promote(self._registry.by_contract(address))

    def by_contract_key(self, identity_key: str) -> Any:
        return self._promote(self._registry.by_contract_key(identity_key))


class _LiveExecutionView:
    """Expose ``confirmed_records()`` over the live chain ExecutionJournal.

    ``run_cycle`` feeds ``app.execution_journal.confirmed_records()`` to the
    compliance ledger. In live mode the execution port is the ExecutionCoordinator
    (which has no ``confirmed_records()``); this view reads the RECONCILED records
    from the SAME chain journal the coordinator writes to and ``reconcile_unfinished``
    scans, so all three consumers share one source of execution truth.
    """

    def __init__(self, journal: ExecutionJournal) -> None:
        self._journal = journal

    def confirmed_records(self) -> list:
        return [
            record for record in self._journal.records.values()
            if record.state == ExecutionState.RECONCILED
        ]


@dataclass
class App:
    """The assembled runtime ``runner.run_cycle`` reads each cycle.

    Exposes EXACTLY the surface ``run_cycle`` touches. ``reconcile_unfinished`` and
    ``observe_entry`` delegate to :mod:`magic_agent.recovery`; ``reconcile_unfinished``
    is bound to the real :class:`ExecutionJournal` (``.records``) while
    ``execution_journal`` is the paper adapter (``.confirmed_records()``) — see the
    module docstring for the role-split rationale.
    """

    position_manager: PositionManager
    state: RuntimeState
    risk_policy: RiskPolicy
    cmc_source: CmcCandidateSource
    exclusion_journal: ExclusionJournal
    candidate_source: CandidateSource
    watchlist: WatchlistManager
    scanner_gateway: Any
    executability: ExecutabilityAdapter
    lifecycle_evaluator: LifecycleEvaluator
    pipeline: DecisionPipeline
    decision_journal: DecisionJournal
    execution_coordinator: Any
    compliance: ComplianceLedger
    execution_journal: Any
    state_journal: StateJournal
    # Durable store for the open paper-position book, persisted by run_cycle so a
    # booked position survives a restart (the concurrency cap blocks re-entry and
    # process_exits can still manage it). PAPER-MODE mechanism only — in live the
    # open book is the chain's truth (rebuilt via reconcile/balances), a separate
    # follow-on.
    position_store: PositionStore
    # The run mode ("paper"/"twak"), surfaced verbatim as the dashboard status
    # ``mode`` so the live snapshot is never the "demo" fallback.
    mode: str = "paper"
    # Base dir for the .magic_agent journal tree. The live status snapshot is
    # published to ``status_dir / "status.json"`` each cycle — the SAME file the
    # ``serve`` /api/status reader loads (status_store.DEFAULT_STATUS_PATH).
    status_dir: Path | None = None
    # ERC-8004 on-chain agent identity, surfaced as the dashboard ``agent_id``.
    # ``None`` when unregistered — the UI shows an honest "unregistered" badge,
    # never a fabricated id. Not wired in paper yet (a deliberate follow-on).
    agent_id: str | None = None
    # Operator kill-switch: when this file exists, run_cycle halts NEW ENTRIES (but
    # NEVER protective exits — those run first, unconditionally). None disables it.
    kill_switch_path: Path | None = None
    # The real chain journal recovery scans (.records). Bound here so
    # reconcile_unfinished closes over it; never read by run_cycle directly.
    _chain_journal: ExecutionJournal = None  # type: ignore[assignment]
    # Cost-viability gate (injectable). When None, after_entry_submission falls back
    # to the module-level evaluate_cost_viability over the paper quote legs.
    cost_viability: Any = None
    # Narrative journal: appends a live-entry-reconciled event after each RECONCILED
    # entry. None on a hand-assembled test App (the journal append is then skipped).
    agent_narrative: AgentNarrativeJournal | None = None
    # Advisory minimum-trade-count pace. ADVISORY ONLY: surfaced for status/
    # observability and journaled when behind, never used to authorize/size/force a trade.
    qualification_config: QualificationConfig | None = None
    qualification_pace: dict | None = None
    # LIVE-ONLY: produces a REAL token->USDC sell quote (normalized to the live
    # cost-viability shape) for the just-reconciled canary position, so the
    # round-trip cost uses buy_spread + the REAL sell_spread instead of double-
    # counting the buy quote. ``None`` in paper (and on hand-built test Apps): the
    # paper path duplicates the deterministic paper quote, which is harmless (gas/
    # fee 0). A callable ``(intent) -> dict | None``; returning ``None`` means the
    # live sell quote could not be obtained -> after_entry_submission fails closed.
    live_sell_quote: Any = None

    def update_qualification_pace(self, now) -> None:
        """Recompute advisory minimum-trade-count pace. ADVISORY ONLY — it records a
        warning but cannot authorize, size, or force a trade."""
        if self.qualification_config is None:
            self.qualification_pace = None
            return
        completed = len(self.execution_journal.confirmed_records())
        self.qualification_pace = evaluate_qualification_pace(
            completed_trade_count=completed,
            now=now,
            config=self.qualification_config,
        ).as_dict()
        if self.qualification_pace["behind_pace"]:
            self.exclusion_journal.append_code("TRACK1", "minimum_trade_count_behind_pace", now)

    def publish_status(self) -> None:
        """Publish a live status snapshot for the dashboard's /api/status reader.

        Builds the ``build_status``-shaped dict from the LIVE runtime (equity/cash
        from :class:`RuntimeState`, the open longs from the position-manager book,
        the kill-switch from :func:`status.derive_spot_halted`) and writes it to
        ``status_dir / "status.json"`` — the same file ``serve`` reads. A no-op when
        ``status_dir`` is unset (e.g. a hand-assembled test App), so the publish is
        purely additive and never breaks an App that did not opt into it.
        """
        if self.status_dir is None:
            return
        from magic_agent.status import (
            SpotStatusExecutor,
            build_status,
            derive_spot_halted,
        )
        from magic_agent.status_store import write_status_snapshot

        book = self.position_manager.book
        executor = SpotStatusExecutor(
            equity_usd=self.state.equity_usd,
            cash_usd=self.state.cash_usd,
            book=book,
        )
        # Label the position with the open long's symbol when one is booked, else a
        # neutral "spot" placeholder (no symbol to surface when flat).
        symbol = book[0].symbol if book and book[0].symbol else "spot"
        # Realized-cash baseline = current cash: the spot book tracks no separate
        # realized-PnL series yet, so realized_pnl derives to 0.0 (a truthful zero,
        # not a fabricated figure). open_pnl is 0.0 too (no MTM on the book).
        snapshot = build_status(
            executor,
            symbol=symbol,
            mode=self.mode,
            venue=self.mode,
            mark_price=0.0,
            starting_equity=float(self.state.cash_usd),
            halted=derive_spot_halted(self.state, self.risk_policy),
            daily_loss=None,
            max_daily_loss=None,
            agent_id=self.agent_id,
            live_mode=self.state.live_mode_state(),
            qualification=self.qualification_pace,
        )
        write_status_snapshot(self.status_dir / "status.json", snapshot)

    def reconcile_unfinished(self) -> None:
        """Fail closed if a prior on-chain swap survived a restart unfinished.

        Scans the real chain :class:`ExecutionJournal` (``.records``) — NOT the
        paper adapter bound to ``execution_journal``. In paper the chain journal is
        empty, so this never blocks; in live it raises the exposure gate exactly as
        ``recovery.reconcile_unfinished`` specifies.
        """
        recovery.reconcile_unfinished(self._chain_journal, self.state)

    def observe_entry(self, setup: Any, risk: Any, now: Any) -> Any:
        """Build the entry :class:`LifecycleObservation` via ``recovery.observe_entry``."""
        return recovery.observe_entry(setup, risk, now)

    def evaluate_cost_viability(self, *, buy_quote: dict | None, sell_quote: dict | None, intended_risk_fraction, now: datetime) -> Any:
        if self.cost_viability is not None:
            return self.cost_viability.evaluate(
                buy_quote=buy_quote,
                sell_quote=sell_quote,
                intended_risk_fraction=intended_risk_fraction,
                now=now,
            )
        # The module gate parses a string, so it receives now.isoformat() (a str) here,
        # whereas an injected gate (above) receives the datetime directly.
        return _default_evaluate_cost_viability(
            buy_quote=buy_quote,
            sell_quote=sell_quote,
            intended_risk_fraction=intended_risk_fraction,
            now=now.isoformat(),
        )

    def after_entry_submission(self, *, intent, result: str, quote: dict | None, risk, now: datetime) -> None:
        if result != "RECONCILED":
            return
        if self.agent_narrative is not None:
            try:
                self.agent_narrative.append(live_entry_reconciled_event(
                    now=now,
                    setup=intent.setup,
                    risk=risk,
                    mode="canary" if self.state.canary_mode else "normal_scoring",
                    execution_state=result,
                    tx_hash=None,
                    reason="supervised_canary_reconciled" if self.state.canary_mode else "live_entry_reconciled",
                ))
            except Exception:
                # Narrative journaling is observability only — a write failure must
                # NEVER abort the post-submit promotion/persistence path.
                _log.exception("agent narrative append failed (non-fatal)")
        if not self.state.canary_mode:
            return
        # SELL QUOTE: in LIVE mode, the round-trip cost must use a REAL token->USDC
        # sell quote for the just-reconciled position — NOT a duplicate of the BUY
        # quote (which double-counts the per-leg spread and spuriously blocks
        # promotion). PAPER keeps the existing behavior (the deterministic paper
        # quote duplicated as buy+sell is harmless: gas/fee 0, fixed paper cost).
        sell_quote = quote
        if self.mode == "twak" and self.live_sell_quote is not None:
            try:
                sell_quote = self.live_sell_quote(intent)
            except Exception:
                # A transient twak probe error (or registry miss) must NEVER crash the
                # live poll loop AFTER the buy is booked. Treat it as "no quote" and
                # fail closed (stay canary), consistent with the rest of the live path.
                _log.exception("live sell-quote probe failed; not promoting (fail closed)")
                sell_quote = None
            if sell_quote is None:
                # The live sell quote could not be obtained (port error / rejected).
                # FAIL CLOSED: never auto-promote without a real sell-leg cost.
                self.state.canary_completed = True
                self.state.canary_intent_id = intent.intent_id
                self.state.canary_reconciled_at = now.isoformat()
                self.state.cost_viability_evidence = {
                    "approved": False,
                    "denied_by": ("missing_sell_quote",),
                }
                self.state.promotion_reason = "cost_viability_failed"
                return
        decision = self.evaluate_cost_viability(
            buy_quote=quote,
            sell_quote=sell_quote,
            intended_risk_fraction=risk.risk_fraction,
            now=now,
        )
        self.state.canary_completed = True
        self.state.canary_intent_id = intent.intent_id
        self.state.canary_reconciled_at = now.isoformat()
        self.state.cost_viability_evidence = decision.evidence
        if decision.approved and self.state.auto_promote_after_canary:
            self.state.canary_mode = False
            self.state.promotion_reason = "canary_reconciled_cost_viable"
            self.state.normal_scoring_started_at = now.isoformat()
        else:
            self.state.promotion_reason = "cost_viability_failed" if not decision.approved else "manual_promotion_required"


def build_app(
    *,
    mode: str = "paper",
    root_dir: str | Path | None = None,
    starting_equity: Decimal = _DEFAULT_STARTING_EQUITY,
    eligibility_path: str | Path = _DEFAULT_ELIGIBILITY_PATH,
    identity_path: str | Path = _DEFAULT_IDENTITY_PATH,
    fixture_dir: str | Path = _DEFAULT_FIXTURE_DIR,
    use_live_frames: bool = False,
    use_live_cmc: bool = False,
    cmc_client: Any = None,
    scanner_gateway: Any = None,
    gold_candidate_symbol: str | None = None,
    frame_source: Any = None,
    twak_runner: Any = None,
    live_rpc: Any = None,
    live_balances: Any = None,
    live_executability: Any = None,
    live_execution_coordinator: Any = None,
) -> App:
    """Assemble the runtime :class:`App`.

    Args:
        mode: ``"paper"`` (default, offline, no funds) or ``"twak"`` (live — wires
            the TWAK execution coordinator + quote provider + live balance/RPC ports;
            requires all live secrets, fails closed otherwise).
        root_dir: Directory for the ``.magic_agent`` journal tree (defaults to CWD).
        starting_equity: Paper session starting equity (a simulated book size).
        eligibility_path: Track-1 eligibility ledger JSON.
        identity_path: Track-1 identity registry JSON.
        fixture_dir: Directory of committed OHLCV frame fixtures (offline scanner).
        use_live_frames: When ``True`` in paper mode, swap the offline fixture
            frame source for the read-only :class:`GateioFrameSource` (still no funds).
        use_live_cmc: When ``True``, swap the offline :class:`FixtureCmcClient` for
            the live :class:`~magic_agent.cmc_source.CoinMarketCapClient` (read-only
            rank/momentum; reads ``CMC_API_KEY`` from env, fails closed if absent).
            Ignored when an explicit ``cmc_client`` is injected.
        cmc_client: Override the CMC client (defaults to the offline
            :class:`FixtureCmcClient`); injectable for tests.
        scanner_gateway: Override the scanner gateway (defaults to the real
            :class:`ScannerGateway`); injectable for tests that need a deterministic
            authorized setup without hand-crafting OHLC frames.
        gold_candidate_symbol: When set, promote that symbol's identity to GOLD so a
            self-contained offline paper run can actually book (the committed dataset's
            only gold identity has no frame fixture). The committed data file is NOT
            mutated.
        frame_source: Override the market-data :class:`FrameSource` used by both the
            scanner gateway and the paper-exit ``observe`` port (injectable for tests
            that drive exit prices). Defaults to the offline fixture source.

    Returns:
        The assembled :class:`App`.

    Raises:
        ValueError: For an unknown ``mode``.
        RuntimeError: In live mode, when a required live secret is missing (fail
            closed before any live port is constructed).
    """
    if mode not in ("paper", "twak"):
        raise ValueError(f"unknown mode {mode!r} (expected 'paper' or 'twak')")
    live_mode = mode == "twak"
    if live_mode:
        # Fail closed BEFORE constructing any live port: no half-wired live runtime.
        _require_live_env()

    journal_root = Path(root_dir) if root_dir is not None else Path.cwd()
    # PART B — per-mode journal roots. Paper and live (twak) get DISTINCT journal
    # trees (`.magic_agent/paper/` vs `.magic_agent/twak/`) so a paper run can NEVER
    # write a state/journal/positions file that a live run reads (and vice-versa).
    # This is the structural half of the live-sizing fix: even before Part A's
    # wallet-truth re-baseline, a paper $10k state.json simply isn't on the live read
    # path anymore. NOTE (serve): the dashboard reader (status_store.DEFAULT_STATUS_PATH
    # = ".magic_agent/status.json") still points at the OLD shared location, so live
    # status now lands at `.magic_agent/<mode>/status.json`; `serve` must be pointed at
    # the per-mode path to reflect a live run (documented in the task report).
    base = journal_root / ".magic_agent" / mode

    # Data inputs (offline).
    eligibility = EligibilityLedger.load(eligibility_path)
    registry: Any = IdentityRegistry.load(identity_path)
    if gold_candidate_symbol is not None:
        registry = _GoldOverrideRegistry(registry, gold_candidate_symbol)

    # State + journals. Restore the persisted session if a journal exists so a
    # restart preserves equity/drawdown anchors, the canary flag, the consecutive-
    # stop count, and the exposure gate — an agent that has halted (or drawn down)
    # must NOT silently resume from a fresh session. ``state_journal.load`` returns
    # the inner ``as_dict()`` payload (sha256-verified), so it feeds ``from_dict``
    # directly. A corrupt journal raises ``IntegrityError`` and is allowed to
    # propagate: a tampered/damaged state file is an operator event, not a silent
    # reset that could resume trading.
    chain_journal = ExecutionJournal(base / "executions.json")
    exclusion_journal = ExclusionJournal(base / "exclusions.jsonl")
    decision_journal = DecisionJournal(base / "decisions.jsonl")
    state_journal = StateJournal(base / "state.json")
    # Durable open-position store (paper-mode mechanism — see PositionStore). Loaded
    # below, then the restored book is placed into the PositionManager BEFORE the
    # open_positions count is wired, so a position booked before a restart still
    # blocks re-entry and is exit-manageable. A corrupt store raises IntegrityError
    # and is allowed to propagate (fail closed — never start with an empty book that
    # would re-enter).
    position_store = PositionStore(base / "positions.json")
    # In LIVE mode the live balance reader (real TwakBalanceReader, or an injected
    # fake) is resolved up-front so a FRESH live session can size off the actual
    # wallet (below). Restart restores from state.json unchanged. Built lazily (no
    # chain call on construction).
    live_twak = None
    balances = None
    if live_mode:
        live_twak = twak_runner or TwakRunner()
        balances = live_balances or TwakBalanceReader(twak=live_twak, registry=registry)
    if live_mode:
        # PART A — LIVE (twak): equity/cash ALWAYS come from the REAL wallet at every
        # startup, NEVER inherited from a restored state file. The bug this fixes: a
        # paper-written state.json ($10k) restored into a live session sized a $250
        # trade off $10k instead of the ~$20 wallet (only an insufficient-balance
        # rejection stopped it). If the wallet read RAISES, it propagates (fail
        # closed) — we never silently fall back to a stale or paper default.
        equity = balances.wallet_equity()
        wallet_equity_usd = equity["equity_usd"]
        wallet_cash_usd = equity["cash_usd"]
        if state_journal.path.exists():
            restored = RuntimeState.from_dict(state_journal.load())
            # Preserve canary-completion + the consecutive-stop/exposure gates ONLY
            # when the restored state is itself LIVE-written AND its equity is
            # wallet-consistent (within a small tolerance). In EVERY other case
            # (paper-written, untagged/legacy, or an equity that drifted beyond
            # tolerance) DISCARD the restored state and re-baseline a fresh live
            # session off the wallet — fail safe = size off real money. Even in the
            # preserve path, equity/cash/peak/anchor are re-based to the wallet (we
            # MUST NOT keep a $10k peak against a $20 wallet — that would compute a
            # ~99% drawdown and instantly DQ-halt).
            if _live_state_is_wallet_consistent(restored, wallet_equity_usd):
                state = _rebaseline_live_state(restored, wallet_equity_usd, wallet_cash_usd)
            else:
                _log.warning(
                    "discarding stale/cross-mode state.json (mode=%s, restored_equity=%s) "
                    "and re-basing equity to the live wallet (equity=%s, cash=%s)",
                    restored.mode, restored.equity_usd, wallet_equity_usd, wallet_cash_usd,
                )
                state = RuntimeState.new_live_session(
                    wallet_equity_usd, wallet_cash_usd, mode="twak",
                )
        else:
            # FRESH LIVE session: size off the wallet (equity = native USD + USDC;
            # cash = the deployable USDC) — NOT the $10k paper default.
            state = RuntimeState.new_live_session(
                wallet_equity_usd, wallet_cash_usd, mode="twak",
            )
    elif state_journal.path.exists():
        # PAPER RESTART: restore the persisted session unchanged (equity/drawdown
        # anchors, canary mode, consecutive-stop count, exposure gate) so a halted or
        # drawn-down paper agent does not silently resume from a fresh session.
        state = RuntimeState.from_dict(state_journal.load())
    else:
        # FRESH PAPER session: the $10k simulated book size (unchanged).
        state = RuntimeState.new_session(starting_equity, mode="paper")

    # CMC candidate source. Selection precedence:
    #   1. an explicit injected ``cmc_client`` (tests / overrides) — always wins;
    #   2. ``use_live_cmc`` -> the live, read-only CoinMarketCapClient (reads
    #      CMC_API_KEY from env, fails closed if absent — NEVER a silent fixture
    #      fallback). Resolves competition symbols to CMC ids via the registry and
    #      batches ONE quotes/latest call. Supplies rank/momentum only (observe/
    #      veto/clamp) — the scanner remains the sole setup authority;
    #   3. otherwise the offline, deterministic FixtureCmcClient (default).
    if cmc_client is not None:
        resolved_cmc_client: Any = cmc_client
    elif use_live_cmc:
        # Fail CLOSED on a missing key — the live universe cannot be fetched without
        # it, and silently degrading to the fixture would mis-represent the scan.
        resolved_cmc_client = CoinMarketCapClient(
            registry=registry, api_key=os.environ.get("CMC_API_KEY", ""),
        )
    else:
        resolved_cmc_client = FixtureCmcClient()
    cmc_source = CmcCandidateSource(eligibility, registry, resolved_cmc_client)
    candidate_source = CandidateSource(eligibility, registry)
    # The offline paper watchlist is restricted to the symbols that HAVE a committed
    # frame fixture (the scanner gateway resolves a candidate to a fixture and would
    # raise on a missing one). A non-pinned symbol only reaches the scanner during a
    # due discovery sweep AND with a fresh CMC snapshot — neither holds offline for a
    # symbol the FixtureCmcClient/fixtures do not cover, so it is journaled and
    # skipped. Live wiring restores the full pinned set.
    watchlist = WatchlistManager(WatchlistState(_fixtured_symbols(fixture_dir, registry), None))

    # Market-data frame source: an explicit injection (tests drive paper-exit
    # prices through it), else offline fixtures by default, else read-only gate.io
    # if live data is explicitly requested (still no funds, no keys).
    if frame_source is not None:
        pass  # caller-injected source (used by both the scanner gateway and paper-exit observe)
    elif live_mode or use_live_frames:
        frame_source = GateioFrameSource(registry=registry)
    else:
        frame_source = FixtureFrameSource(registry=registry, fixture_dir=fixture_dir)

    gateway = scanner_gateway if scanner_gateway is not None else ScannerGateway(
        registry=registry, frame_source=frame_source, scanner_commit=SCANNER_COMMIT,
    )

    # Risk policy (shared by both modes). The executability port + execution port are
    # computed per-mode below, AFTER paper_adapter (the live coordinator needs the
    # position manager, and the per-mode split lives in one place).
    risk_policy = RiskPolicy(RiskConfig.defaults())

    # Position manager: paper books reconciled positions. The positions feed is
    # the manager's own reconcile book (the SINGLE source for both exits and the
    # concurrency count), so a booked position is fed back to the next cycle. The
    # observe / sell_probe / execute ports are bound below to the price-driven paper
    # exit ports so a booked position is a real ROUND-TRIP (stop / campaign-DOL hit
    # closes it and frees the slot). Booking flows ONLY through
    # open_from_reconciliation (the no-optimistic-booking invariant).
    position_manager = PositionManager(
        positions=lambda: [],  # rebound to the live book below (avoids the construction cycle).
        observe=lambda position, observed_at, reduction: None,  # rebound below.
        evaluator=LifecycleEvaluator(),
        pipeline=DecisionPipeline(),
        risk_policy=risk_policy,
        risk_state=state.risk_state,
        sell_probe=lambda position, quantity: None,  # rebound below.
        execute=lambda *args: None,  # rebound below.
    )
    # Restore the open position book BEFORE wiring open_positions below — so the
    # restored open count is visible to the next cycle's concurrency cap (blocks
    # re-entry) and the restored positions are a complete exit source (they carry
    # quantity + stressed_loss_per_unit + the exit geometry: symbol/stop/campaign_dol).
    #
    # LIVE (twak) mode: the open book is rebuilt from CHAIN TRUTH, NOT positions.json.
    # Trusting the local positions.json on a live restart (or a local->VPS handoff)
    # could re-enter or mismanage an already-held on-chain position. Instead we cross
    # the reconciled execution-journal buy records (which carry entry/stop/target/qty
    # + identity) against the CURRENT on-chain wallet balances: a position is rebuilt
    # only when a reconciled record exists AND the wallet still holds that token (using
    # the realized on-chain qty). Journal positions no longer held are dropped; held
    # tokens with no journal record are logged (unmanaged) and left out of the book.
    # The chain/balance read fails CLOSED (propagates) — NO positions.json fallback.
    # positions.json remains a per-cycle cache/audit (run_cycle still saves it), but it
    # is no longer the live startup source of truth.
    #
    # PAPER mode is unchanged: restore from positions.json (a corrupt store raises
    # IntegrityError here — fail closed, no empty-book re-entry).
    if live_mode:
        position_manager.book = rebuild_positions_from_chain(
            records=list(chain_journal.records.values()),
            intents=intents_from_journal(chain_journal.records.values()),
            balances=balances,
        )
    else:
        position_manager.book = position_store.load()

    # Bind the price-driven paper exit ports to the (now-restored) book and the frame
    # source. ``observe`` reads the position's current price bar to detect a stop /
    # campaign-DOL hit; ``execute`` removes the closed position from THIS book, so
    # positions() and the concurrency count drop and the close is persisted by
    # run_cycle's end-of-cycle save. PAPER-MODE mechanism only — live exits are
    # driven by real fills / chain truth (a deliberate follow-on).
    exit_ports = PaperExitPorts(frame_source=frame_source, book=position_manager.book)
    position_manager.observe = exit_ports.observe
    position_manager.sell_probe = exit_ports.sell_probe
    position_manager.execute = exit_ports.execute

    # Feed the open booked positions back as the exit source, and surface the open
    # count to the risk snapshot's concurrency cap. Both read the SAME book, so a
    # position booked this cycle denies a second entry next cycle, and a close/exit
    # that drops it from the book decrements the count.
    position_manager.positions = lambda: list(position_manager.book)
    state.open_positions = lambda: len(position_manager.book)

    # The paper execution port: it is BOTH the execution coordinator (.submit) and
    # the execution_journal compliance reads (.confirmed_records). Recovery reads
    # the separate chain journal (.records) via App.reconcile_unfinished.
    # NOTE: the exit ports above (PaperExitPorts) remain paper-driven for now; live
    # exits (real fills / chain truth) are wired in the next task (0D).
    paper_adapter = PaperExecutionAdapter(positions=position_manager)

    # Per-mode executability + execution port + execution journal. In paper the
    # PaperExecutionAdapter is both port and journal; in live the port is the real
    # ExecutionCoordinator and compliance reads RECONCILED records off the chain
    # journal via _LiveExecutionView (the SAME journal the coordinator writes to and
    # recovery scans — one source of execution truth).
    if live_mode:
        # live_twak + balances were resolved up-front (above) so a fresh live session
        # could size off the wallet; reuse the same instances here.
        wallet_address = os.environ.get("WALLET_ADDRESS", "")
        # Live RPC default: the real BscRpcClient (reads BSC_RPC_URL, guaranteed
        # present by _require_live_env). Construction is lazy (no connection), so the
        # assemble path never makes a chain call. It fails closed at call time: a
        # receipt timeout / RPC error RAISES, which the coordinator maps to a
        # BROADCAST_UNKNOWN (exposure-blocking) outcome rather than fabricating a result.
        rpc = live_rpc or BscRpcClient(
            rpc_url=os.environ["BSC_RPC_URL"],
            wallet_address=wallet_address,
        )
        quote_provider = TwakQuoteProvider(
            twak=live_twak,
            registry=registry,
            wallet_address=wallet_address,
            stable_symbol="USDC",
            chain="bsc",
            now=_iso(state),
        )
        executability = live_executability or ExecutabilityAdapter(quote_provider)
        execution_port = live_execution_coordinator or ExecutionCoordinator(
            twak=live_twak,
            rpc=rpc,
            balances=balances,
            journal=chain_journal,
            positions=position_manager,
            registry=registry,
        )
        execution_journal = _LiveExecutionView(chain_journal)
        # Live protective exits: real TWAK sells. Detection (observe) stays
        # price-driven via the live frame source; only the sell quote/execute
        # are swapped to the real TWAK sell ports.
        live_sell_ports = TwakSellPorts(twak=live_twak, book=position_manager.book, registry=registry)
        position_manager.sell_probe = live_sell_ports.sell_probe
        position_manager.execute = live_sell_ports.execute

        # Live canary cost-viability needs a REAL token->USDC sell quote for the
        # just-reconciled position (token + realized qty), normalized to the live
        # cost-viability shape (output_qty/minimum_output/price_impact). This closure
        # locates the reconciled position in the open book by intent_id and probes a
        # SELL of its realized qty. Returns None (fail closed) when the position is
        # absent or the sell probe is rejected — after_entry_submission then refuses
        # to auto-promote rather than trust a duplicated buy quote.
        def _live_sell_quote(intent: Any) -> dict | None:
            position = next(
                (p for p in position_manager.book if p.intent_id == intent.intent_id),
                None,
            )
            if position is None:
                return None
            probe = live_sell_ports.sell_probe(position, position.quantity)
            if not probe.approved or probe.quote is None:
                return None
            return probe.quote
    else:
        executability = ExecutabilityAdapter(PaperQuoteProvider(now=_iso(state)))
        execution_port = paper_adapter
        execution_journal = paper_adapter
        # PAPER: no real sell quote — after_entry_submission duplicates the
        # deterministic paper quote (harmless: gas/fee 0, fixed paper cost).
        _live_sell_quote = None

    return App(
        position_manager=position_manager,
        state=state,
        risk_policy=risk_policy,
        cmc_source=cmc_source,
        exclusion_journal=exclusion_journal,
        candidate_source=candidate_source,
        watchlist=watchlist,
        scanner_gateway=gateway,
        executability=executability,
        lifecycle_evaluator=LifecycleEvaluator(),
        pipeline=DecisionPipeline(),
        decision_journal=decision_journal,
        execution_coordinator=execution_port,
        compliance=ComplianceLedger(),
        execution_journal=execution_journal,
        state_journal=state_journal,
        position_store=position_store,
        mode=mode,
        # Publish the live status snapshot under the SAME .magic_agent base the
        # serve /api/status reader loads, so the dashboard reflects live paper data.
        status_dir=base,
        kill_switch_path=base / "HALT_NEW_ENTRIES",
        _chain_journal=chain_journal,
        agent_narrative=AgentNarrativeJournal(base / "agent_narrative.jsonl"),
        live_sell_quote=_live_sell_quote,
    )


def _fixtured_symbols(fixture_dir: str | Path, registry: Any) -> tuple[str, ...]:
    """Return the pinned competition symbols that have a committed frame fixture.

    Mirrors :meth:`FixtureFrameSource._load_fixture`'s filename mapping
    (``market_data_symbol`` lower-cased, ``/`` -> ``_``, ``.json``) so the offline
    watchlist only pins symbols the offline scanner can actually scan. Falls back to
    the full pinned set if the fixture directory is absent (e.g. live frames).
    """
    fixture_path = Path(fixture_dir)
    if not fixture_path.is_dir():
        return WatchlistState.initial().active_symbols
    available: list[str] = []
    for symbol in WatchlistState.initial().active_symbols:
        record = registry.get_by_symbol(symbol)
        if record is None:
            continue
        name = record.market_data_symbol.lower().replace("/", "_") + ".json"
        if (fixture_path / name).exists():
            available.append(symbol)
    return tuple(available)


def _iso(state: RuntimeState) -> str:
    """A fixed ISO timestamp seeding the deterministic paper quote provider.

    The provider stamps quote freshness off this value (and validates against it),
    so it is deterministic and independent of wall-clock — the paper quote never
    spuriously expires within a cycle.
    """
    # The provider only needs an internally-consistent clock for its TTL window; a
    # fixed reference makes paper quotes reproducible.
    return "2026-06-21T12:00:00Z"
