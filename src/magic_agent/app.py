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

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from magic_agent.candidate_source import CandidateSource
from magic_agent.cmc_source import CmcCandidateSource, RawCmcQuote
from magic_agent.compliance import ComplianceLedger
from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.eligibility import EligibilityLedger
from magic_agent.execution_journal import ExecutionJournal
from magic_agent.frames import FixtureFrameSource, GateioFrameSource
from magic_agent.identity_registry import IdentityRegistry
from magic_agent.lifecycle import LifecycleEvaluator
from magic_agent.paper_adapter import PaperExecutionAdapter
from magic_agent.position_manager import PositionManager
from magic_agent.quotes import ExecutabilityAdapter, PaperQuoteProvider
from magic_agent.risk_policy import RiskConfig, RiskPolicy
from magic_agent import recovery
from magic_agent.runtime_journals import DecisionJournal, ExclusionJournal
from magic_agent.runtime_state import RuntimeState
from magic_agent.scanner_gateway import ScannerGateway
from magic_agent.state_journal import StateJournal
from magic_agent.watchlist import WatchlistManager, WatchlistState

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
    # The real chain journal recovery scans (.records). Bound here so
    # reconcile_unfinished closes over it; never read by run_cycle directly.
    _chain_journal: ExecutionJournal = None  # type: ignore[assignment]

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


def build_app(
    *,
    mode: str = "paper",
    root_dir: str | Path | None = None,
    starting_equity: Decimal = _DEFAULT_STARTING_EQUITY,
    eligibility_path: str | Path = _DEFAULT_ELIGIBILITY_PATH,
    identity_path: str | Path = _DEFAULT_IDENTITY_PATH,
    fixture_dir: str | Path = _DEFAULT_FIXTURE_DIR,
    use_live_frames: bool = False,
    cmc_client: Any = None,
    scanner_gateway: Any = None,
    gold_candidate_symbol: str | None = None,
) -> App:
    """Assemble the runtime :class:`App`.

    Args:
        mode: ``"paper"`` (default, offline, no funds) or ``"twak"`` (live — out of
            scope here, raises ``NotImplementedError``).
        root_dir: Directory for the ``.magic_agent`` journal tree (defaults to CWD).
        starting_equity: Paper session starting equity (a simulated book size).
        eligibility_path: Track-1 eligibility ledger JSON.
        identity_path: Track-1 identity registry JSON.
        fixture_dir: Directory of committed OHLCV frame fixtures (offline scanner).
        use_live_frames: When ``True`` in paper mode, swap the offline fixture
            frame source for the read-only :class:`GateioFrameSource` (still no funds).
        cmc_client: Override the CMC client (defaults to the offline
            :class:`FixtureCmcClient`); injectable for tests.
        scanner_gateway: Override the scanner gateway (defaults to the real
            :class:`ScannerGateway`); injectable for tests that need a deterministic
            authorized setup without hand-crafting OHLC frames.
        gold_candidate_symbol: When set, promote that symbol's identity to GOLD so a
            self-contained offline paper run can actually book (the committed dataset's
            only gold identity has no frame fixture). The committed data file is NOT
            mutated.

    Returns:
        The assembled :class:`App`.

    Raises:
        NotImplementedError: For ``mode="twak"`` (live execution is out of scope).
    """
    if mode == "twak":
        # LIVE is out of scope for this task. The live wiring would bind the TWAK
        # execution coordinator + TWAK-backed quote provider + GateioFrameSource +
        # the x402 CMC client (signing real swaps). Paper must fully work; live is
        # a deliberate follow-on.
        raise NotImplementedError(
            "live (twak) runtime assembly is out of scope; the TWAK coordinator, "
            "TWAK quote provider, GateioFrameSource, and x402 CMC client are wired "
            "on testnet as a follow-on. Use mode='paper' (the default)."
        )
    if mode != "paper":
        raise ValueError(f"unknown mode {mode!r} (expected 'paper' or 'twak')")

    journal_root = Path(root_dir) if root_dir is not None else Path.cwd()
    base = journal_root / ".magic_agent"

    # Data inputs (offline).
    eligibility = EligibilityLedger.load(eligibility_path)
    registry: Any = IdentityRegistry.load(identity_path)
    if gold_candidate_symbol is not None:
        registry = _GoldOverrideRegistry(registry, gold_candidate_symbol)

    # State + journals.
    state = RuntimeState.new_session(starting_equity)
    chain_journal = ExecutionJournal(base / "executions.json")
    exclusion_journal = ExclusionJournal(base / "exclusions.jsonl")
    decision_journal = DecisionJournal(base / "decisions.jsonl")
    state_journal = StateJournal(base / "state.json")

    # CMC candidate source (offline client by default).
    cmc_source = CmcCandidateSource(eligibility, registry, cmc_client or FixtureCmcClient())
    candidate_source = CandidateSource(eligibility, registry)
    # The offline paper watchlist is restricted to the symbols that HAVE a committed
    # frame fixture (the scanner gateway resolves a candidate to a fixture and would
    # raise on a missing one). A non-pinned symbol only reaches the scanner during a
    # due discovery sweep AND with a fresh CMC snapshot — neither holds offline for a
    # symbol the FixtureCmcClient/fixtures do not cover, so it is journaled and
    # skipped. Live wiring restores the full pinned set.
    watchlist = WatchlistManager(WatchlistState(_fixtured_symbols(fixture_dir, registry), None))

    # Market-data frame source: offline fixtures by default, read-only gate.io if
    # live data is explicitly requested (still no funds, no keys).
    if use_live_frames:
        frame_source: Any = GateioFrameSource(registry=registry)
    else:
        frame_source = FixtureFrameSource(registry=registry, fixture_dir=fixture_dir)

    gateway = scanner_gateway if scanner_gateway is not None else ScannerGateway(
        registry=registry, frame_source=frame_source, scanner_commit=SCANNER_COMMIT,
    )

    # Risk + executability (deterministic paper quotes, no network).
    risk_policy = RiskPolicy(RiskConfig.defaults())
    quote_provider = PaperQuoteProvider(now=_iso(state))
    executability = ExecutabilityAdapter(quote_provider)

    # Position manager: paper books reconciled positions; it does not simulate
    # protective exits (paper has no live price frame), so the positions feed is
    # empty and the exit ports are inert no-ops. Booking still flows ONLY through
    # open_from_reconciliation (the no-optimistic-booking invariant).
    position_manager = PositionManager(
        positions=lambda: [],
        observe=lambda position, observed_at, reduction: None,
        evaluator=LifecycleEvaluator(),
        pipeline=DecisionPipeline(),
        risk_policy=risk_policy,
        risk_state=state.risk_state,
        sell_probe=lambda position, quantity: None,
        execute=lambda *args: None,
    )

    # The paper execution port: it is BOTH the execution coordinator (.submit) and
    # the execution_journal compliance reads (.confirmed_records). Recovery reads
    # the separate chain journal (.records) via App.reconcile_unfinished.
    paper_adapter = PaperExecutionAdapter(positions=position_manager)

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
        execution_coordinator=paper_adapter,
        compliance=ComplianceLedger(),
        execution_journal=paper_adapter,
        state_journal=state_journal,
        _chain_journal=chain_journal,
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
