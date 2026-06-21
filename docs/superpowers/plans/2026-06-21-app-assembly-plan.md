# MIDAS Production App Assembly Plan (#65)

> Makes `magic-agent run --executor paper` actually complete a cycle. The Plan-2
> safety loop (`runner.run_cycle` / `live.run_live`) is unit-tested against a fake
> `app`; this plan builds the REAL `app` + the missing collaborators it calls, plus
> a paper-mode market-data feed and the `cli._cmd_run` entrypoint.

**Goal:** A runnable, paper-default spot runtime: assemble the production `App`, the
missing journals/state/recovery, a frame source + quote provider behind injected
ports, and wire `cli._cmd_run` to drive `run_live`. No funds, no TWAK signing in paper.

**Architecture:** `App` is an immutable container of injected ports + journals + a
mutable `RuntimeState`. `build_app(config, *, mode)` wires concrete adapters (paper
vs live). Side effects (market data, quotes, execution) stay behind ports so the
whole loop stays testable with deterministic fakes; a real gate.io frame source +
mid-price paper quote provider make it run live-data paper.

**The `app` contract (authoritative — from `runner.run_cycle`):** `app` must expose:
`reconcile_unfinished()`, `position_manager` (`.process_exits(now)`), `state`
(`.blocks_new_exposure`, `.canary_mode`, `.risk_state()->PortfolioRiskState`,
`.as_dict()`), `risk_policy`, `cmc_source` (`.snapshot(now)`), `exclusion_journal`
(`.append_many(rows,now)`, `.append_code(symbol,reason,now)`), `candidate_source`
(`.enumerate(now=,watchlist=,snapshots=)`), `watchlist` (`.state.discovery_due(now)`,
`.promote(symbol)`, `.mark_discovery(now)`), `scanner_gateway` (`.scan(candidate)`),
`executability` (`.prepare_order(setup=,market=,risk_state=,risk_policy=)->PreparedOrder`),
`lifecycle_evaluator`, `observe_entry(setup,risk,now)->LifecycleObservation`,
`pipeline`, `decision_journal` (`.append(decision,now)`), `execution_coordinator`
(`.submit(intent,quote=,policy=)`), `compliance` (`.observe(records,now)`),
`execution_journal` (`.confirmed_records()`), `state_journal` (`.save(payload)`).

---

### Task A: Exclusion + Decision journals
**Files:** Create `src/magic_agent/runtime_journals.py`, `tests/test_runtime_journals.py`.
Build `ExclusionJournal` (`append_many(rows, now)`, `append_code(symbol, reason, now)`)
and `DecisionJournal` (`append(decision, now)`), both JSONL append-only (mirror
`execution_journal`/`state_journal` idiom: atomic line append, deterministic
serialization, tmp_path tests). RED→GREEN. Model: sonnet.

### Task B: RuntimeState
**Files:** Create `src/magic_agent/runtime_state.py`, `tests/test_runtime_state.py`.
Mutable runtime state exposing: `blocks_new_exposure: bool`, `canary_mode: bool`,
`risk_state() -> PortfolioRiskState` (built from tracked equity / open positions /
daily anchor / consecutive stops / open+bucket stressed loss / equity freshness),
`as_dict()` + `from_dict()` (round-trips through `state_journal`). Default
`canary_mode=True` (first live order uses the 0.25% canary) and one concurrent
position until promoted. `blocks_new_exposure` is set by `reconcile_unfinished`
(Task C) when an unreconciled/broadcast-unknown execution exists. RED→GREEN.
Model: opus (risk-state correctness).

### Task C: reconcile_unfinished + observe_entry
**Files:** Create `src/magic_agent/recovery.py`, `tests/test_recovery.py`.
`reconcile_unfinished(execution_journal, coordinator, state)`: on startup/each cycle,
find executions in non-terminal states (SUBMITTED / MINED / BROADCAST_UNKNOWN) in the
execution journal and re-run reconciliation through the coordinator; if any cannot be
resolved to RECONCILED/terminal, set `state.blocks_new_exposure = True` (fail closed —
never open new exposure while a prior swap's fate is unknown). `observe_entry(setup,
risk, now) -> LifecycleObservation`: build the lifecycle observation for an entry
candidate from the authorized setup + risk decision (no position yet, no exit). RED→
GREEN with: an unfinished broadcast-unknown blocks new exposure; a fully-terminal
journal does not. Model: opus (crash-safety).

### Task D: Executability adapter + paper quote provider
**Files:** Create `src/magic_agent/quotes.py`, `tests/test_quotes.py`.
`ExecutabilityAdapter.prepare_order(setup, market, risk_state, risk_policy)` wraps
`executability.prepare_exact_order(..., quote_provider=self.quote_provider)` (binds
the injected quote provider). `PaperQuoteProvider`: returns a fresh two-sided quote at
the candidate's mid (derived from the latest frame close) bound to the requested qty —
deterministic, no network, no funds. (Live TWAK quote provider is a thin port stub
flagged for live wiring, out of scope here.) RED→GREEN. Model: opus.

### Task E: Frame source (paper market data)
**Files:** Create `src/magic_agent/frames.py`, `tests/test_frames.py`.
`FrameSource.closed_frames(candidate) -> dict[str, DataFrame]` returning closed
1w/12h/1h frames for the candidate's `market_data_symbol`. Two adapters: `FixtureFrameSource`
(deterministic, from committed test fixtures — used by tests + offline runs) and
`GateioFrameSource` (real read-only OHLCV via the same approach as the scanner's
`scripts/scan_universe_bias.py`; drops the forming bar so frames are closed-only). No
keys, read-only. RED→GREEN with the fixture source; the gate.io source gets a guarded
live-smoke check, not a unit test. Model: opus.

### Task F: App container + build_app factory + cli._cmd_run
**Files:** Create `src/magic_agent/app.py`, `tests/test_app.py`; Modify `src/magic_agent/cli.py`.
`App` (frozen container) holds every collaborator above + `RuntimeState`. `build_app(config,
*, mode="paper")` wires concrete adapters: paper -> `PaperExecutionAdapter` + `PaperQuoteProvider`
+ `FixtureFrameSource`-or-`GateioFrameSource`; live -> TWAK coordinator + TWAK quote + gate.io
(live opt-in only). Implement `cli._cmd_run` to `build_app(..., mode=<paper|twak>)` then
`run_live(app, clock=..., max_iters=...)`, paper default. RED→GREEN: `build_app(mode="paper")`
+ a bounded `run_live` over a deterministic fixture feed completes >=1 cycle and books a
paper position only through the reconcile path; `magic-agent run --executor paper --max-iters N`
returns 0 (no crash). Model: opus (integration).

### Task G: End-to-end paper smoke + final review
`uv run magic-agent run --executor paper --max-iters 1` completes a cycle (documented
runbook command). Update README "Current status" to reflect paper is now runnable.
Final opus review (general-review-protocol) over the assembly: no funds path in paper,
fail-closed recovery, ports injected, no hand-built DecisionInputs, no secret. Model: opus review.

## Invariants (carried from Plan 2 — must survive assembly)
Paper default; no hand-built DecisionInputs; scanner sole authority; no-DOL/no-stop->
no order; gold identity for execution; RiskPolicy mandatory + fresh exact-size quote
before submit; no optimistic booking (book only via coordinator reconcile path); entry
halts never block protective exits; reconcile-unfinished fails closed; no secret in any diff.
