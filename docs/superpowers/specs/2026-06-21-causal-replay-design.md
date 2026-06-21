---
title: Track 1 Causal Portfolio Replay Design
version: 1.0
date_created: 2026-06-21
last_updated: 2026-06-21
owner: degencodebeast
tags: [research, backtest, causal-replay, trading, qml, portfolio, track1]
---

# Introduction

This specification defines the historical replay and forward-shadow validation
system for the MIDAS Track 1 spot agent. Its purpose is to measure what the
versioned live decision pipeline would have done using only information available
at each historical decision time, realistic portfolio constraints, and explicit
execution-cost assumptions.

The replay is evidence infrastructure, not a second strategy implementation. It
must reuse the same scanner authorization, aggressive Direct QML selection,
canonical stop, campaign draw-on-liquidity (DOL), macro clamp, RiskPolicy, and
position-lifecycle code used by live and paper operation.

## 1. Purpose & Scope

### 1.1 Purpose

The replay shall answer four distinct questions without conflating them:

1. Does the rules-faithful Direct QML setup produce favorable outcomes after
   realistic costs?
2. What would one constrained wallet have earned and drawn down?
3. How much incremental value comes from reconstructable momentum selection?
4. How does the live-only CMC Agent Hub selector behave when captured and
   forward-tested prospectively?

### 1.2 In scope

- Event-driven, closed-bar, as-of historical evaluation.
- Shared live/paper/replay `DecisionPipeline` behavior.
- Weekly, H12, and H1 scanner frames; M15 is not required by aggressive mode.
- Aggressive Direct QML entry, canonical scanner stop, and campaign DOL target.
- Fixed-fractional RiskPolicy and all portfolio caps.
- One-wallet spot-long accounting with stable and gas reserves.
- Versioned fill, fee, gas, slippage, impact, and capacity assumptions.
- Hourly equity and peak-to-equity drawdown compatible with Track 1 scoring.
- A predeclared ZEC diagnostic with three contrast pairs.
- Full-scannable-universe portfolio replay after the diagnostic.
- Historical reconstructable-momentum ablation.
- Forward capture and shadow validation of nonreconstructable CMC inputs.
- Reproducible run manifests, event journals, reports, and uncertainty labels.

### 1.3 Out of scope

- Reimplementing scanner, setup, risk, or lifecycle rules inside research scripts.
- Historical fabrication of Agent Hub narratives, macro recommendations, sector
  snapshots, social context, or skill outputs that were not recorded at the time.
- Retrofitting fixed `2R` targets when campaign DOL is absent.
- Optimizing parameters on the ZEC diagnostic window.
- Treating the current eligible list as point-in-time history without a
  survivorship warning.
- Claiming historical PancakeSwap depth when only CEX OHLC data is available.
- Automated compliance-only trades; the replay may measure qualification but may
  not invent trades to satisfy it.
- Repairing scanner supersession or adding an H1-local stop tier.

## 2. Definitions

- **As-of view**: The subset of each timeframe whose `close_time` is less than or
  equal to the decision timestamp.
- **Decision timestamp**: The close time of the event that makes a setup or state
  change knowable to the live pipeline.
- **DecisionPipeline**: The shared deterministic application service used by
  live, paper, and replay to convert data and state into no action or an intent.
- **Research adapter**: Replay-only data or execution input implementing a shared
  interface without changing decision semantics.
- **Campaign DOL**: The scanner-selected concrete directional move-completion
  target. A live or replay strategy intent requires it.
- **Checklist DOL**: The stricter scoring binary. It may be false while campaign
  DOL remains concrete.
- **Pending entry**: An authorized Direct QML intent that has not yet satisfied
  the versioned execution trigger or fill model.
- **Next-executable fill**: A fill occurring strictly after the decision becomes
  knowable, under a versioned bar and cost model.
- **Unresolved position**: A filled position that has reached neither a live-rule
  exit nor the end of the replay window.
- **Forward shadow**: Evaluation of recorded live CMC snapshots without sending
  funds or allowing the shadow result to authorize a trade.
- **Selection ablation**: A run holding the decision core fixed while changing
  only which candidates reach it.
- **Base/stress cost scenario**: Explicit execution assumptions calibrated from
  timestamped live quotes, with stress assumptions never lower than base.
- **Wilson interval**: A binomial confidence interval reported around win rate.
- **MAE/MFE**: Maximum adverse/favorable excursion after fill and before exit.

## 3. Requirements, Constraints & Guidelines

### 3.1 Authority and live parity

- **REQ-001**: The active strategy authority is
  `2026-06-21-track1-spot-agent-design.md` plus its synchronized workspace rules.
- **REQ-002**: Live, paper, and replay shall invoke the same versioned
  `DecisionPipeline`; replay scripts shall not recode its setup or risk rules.
- **REQ-003**: Every run shall pin the scanner commit, MIDAS commit, configuration
  hashes, identity snapshot, data snapshot, cost model, and replay-engine version.
- **REQ-004**: The shared pipeline shall consume scanner-exported entry, canonical
  stop level/source/anchor/bar, campaign DOL level/source, checklist DOL, grade,
  lifecycle, and governing-POI evidence.
- **REQ-005**: Proxy QML selection, proxy stops, fixed-R targets, fixed-percent
  stops, and backtest-only authorization gates are prohibited.
- **REQ-006**: If live behavior is not deterministic or not represented by a
  shared interface, the replay shall fail validation rather than guess it.
- **CON-001**: Replay may substitute only historical data, clock, execution,
  wallet, and persistence adapters.

### 3.2 Causal clock and data integrity

- **REQ-010**: All timestamps shall be timezone-aware UTC and all frames shall
  carry explicit open and close times.
- **REQ-011**: At decision time `t`, each timeframe shall contain only bars with
  `close_time <= t`. A Weekly or H12 bar still forming at `t` is unavailable.
- **REQ-012**: Signal evaluation shall occur on the same refresh cadence as the
  versioned live profile. The initial profile evaluates after each closed H1 bar.
- **REQ-013**: Entry, exit, target, stop, and lifecycle events shall not execute on
  a price observation that occurred before the decision producing the action.
- **REQ-014**: Market data shall be audited for monotonic timestamps, duplicates,
  missing required fields, nonpositive prices, `low <= open/close <= high`, gaps,
  stale runs, and implausible returns.
- **REQ-015**: Missing or invalid required data shall produce an explicit
  `UNSCANNABLE` or `DATA_INVALID` event. Exceptions shall not silently remove a
  token or setup from denominators.
- **REQ-016**: Market-data symbols shall resolve through the same gold identity
  record used by live execution. Symbol-only joins are prohibited.
- **REQ-017**: If historical eligibility membership is unavailable, results shall
  carry `current_universe_survivorship_bias=true` and shall not claim a
  point-in-time eligible universe.
- **CON-002**: CEX OHLC may drive scanner structure but shall not be described as
  historical on-chain execution or pool-depth data.

### 3.3 Candidate-selection lanes

- **REQ-020**: The structural baseline shall evaluate every gold, scannable token
  for which required historical frames pass data validation.
- **REQ-021**: A historical momentum lane may reconstruct versioned 7-day and
  30-day returns, volume change, and deterministic volatility flags from as-of
  OHLCV data.
- **REQ-022**: Historical momentum features shall use only observations closed by
  the selection timestamp and shall be recomputed at the live selector cadence.
- **REQ-023**: Current CMC ranks, current narratives, or present-day sector labels
  shall never be joined backward onto historical decisions.
- **REQ-024**: Every nonreconstructable live CMC input shall be persisted as a
  forward snapshot containing raw response, normalized fields, observation time,
  TTL, provider/schema version, selector configuration, and output decision.
- **REQ-025**: Historical reports shall separate at least:
  - full-scannable-universe structural baseline;
  - reconstructable-momentum selection;
  - future forward-shadow Agent Hub selection.
- **REQ-026**: When historical macro context is unavailable, the historical lane
  shall use the explicit neutral clamp `1.0` and disclose that live macro behavior
  was not replayed.
- **REQ-027**: Selector failure and data unavailability shall be reported
  separately from a selector veto.
- **CON-003**: Forward-shadow results may evaluate ranking and veto quality but
  shall not be called historical out-of-sample evidence until enough snapshots
  and subsequent outcomes have accumulated.

### 3.4 Entry, order, and fill semantics

- **REQ-030**: The active strategy is spot-long aggressive Direct QML. M15 SCOB,
  M15 MSS, and the provisional four-hour M15 window are advisory or dormant and
  shall not gate replay entries.
- **REQ-031**: An order may be created only after the shared pipeline returns an
  A/B-family authorized setup with governing H12/D1 POI, valid H1 QML, canonical
  stop, and concrete campaign DOL.
- **REQ-032**: `checklist_dol=false` may lower grade but shall not block an order
  when campaign DOL is concrete and all other live requirements pass.
- **REQ-033**: `campaign_dol_level=None` shall create no order. The QML may remain
  monitorable, but replay shall not manufacture a fixed-R target.
- **REQ-034**: A Direct QML decision becomes executable strictly after its closed
  H1 decision bar. No decision-candle or earlier intrabar fill is permitted.
- **REQ-035**: Pending-entry trigger, time-to-live, cancellation, and repricing
  rules shall come from versioned live configuration. If the live profile has not
  defined them, the run shall be labeled `RESEARCH_EXECUTION_MODEL` rather than
  live-parity evidence.
- **REQ-036**: Under the initial conservative OHLC limit model, a long QML order
  fills on the first subsequent bar whose low reaches the limit. If that bar opens
  below the limit, the unadjusted base fill is the lower of open and limit; adverse
  execution costs are then applied. Otherwise the base fill is the limit.
- **REQ-037**: A bar that does not touch the entry cannot fill. Expired or
  invalidated pending orders are counted and reported, not converted to losses.
- **REQ-038**: Capacity, minimum-notional, reserve, and stressed-risk checks shall
  occur using the simulated executable fill and cost scenario before final fill.
- **REQ-039**: Simultaneous candidate intents shall be ordered by the same
  deterministic live priority. A missing live tie-breaker is a parity blocker.

### 3.5 Position lifecycle and exits

- **REQ-040**: Open positions shall be reevaluated through the same live lifecycle
  pipeline on every configured refresh.
- **REQ-041**: Exit reasons shall be limited to versioned live reasons: canonical
  stop, campaign DOL/move completion, opposing H12/D1 invalidation, deterministic
  RiskPolicy reduction, or another explicitly versioned live lifecycle exit.
- **REQ-042**: Fixed `2R`, arbitrary holding-period profit targets, and exclusion
  of unresolved positions are prohibited.
- **REQ-043**: If stop and target are both touched within one unresolved OHLC bar,
  the base and stress scenarios shall assume stop-first. A better outcome requires
  lower-timeframe evidence proving event order.
- **REQ-044**: Stop execution shall include adverse gap and sell-side cost. A stop
  price is an invalidation trigger, not a guaranteed fill price.
- **REQ-045**: At the replay boundary, every open position shall be marked to the
  last valid price, included in equity/drawdown, and reported as unresolved. A
  separate forced-liquidation scenario may be reported with explicit exit costs.
- **REQ-046**: Scanner-superseded QMLs shall remain rejected; replay shall not
  revive them.
- **REQ-047**: A macro-distant H12-pivot stop shall produce the same small size or
  skipped trade as live. Replay shall not tighten it to improve results.

### 3.6 Cost, liquidity, and execution scenarios

- **REQ-050**: Gross and net results shall both be reported; net is authoritative.
- **REQ-051**: Costs shall separately model competition-specified simulated cost,
  liquidity-provider/route fee, gas, spread, slippage, and market impact.
- **REQ-051A**: The replay shall publish separate real-wallet and
  competition-adjusted accounting views. Each cost component shall declare which
  view it affects, and no component may be charged twice in one view.
- **REQ-052**: Historical pool depth shall not be fabricated. When exact depth is
  unavailable, base and stress assumptions shall be calibrated from timestamped
  live TWAK/PancakeSwap quotes at representative notional tiers.
- **REQ-053**: Every published result shall include at least base-cost and
  stress-cost scenarios. A zero-cost run may appear only as a diagnostic upper
  bound and shall never be the headline result.
- **REQ-054**: The simulated trade shall be skipped when quote-calibrated impact,
  pool-share cap, minimum output, minimum notional, gas reserve, or fee viability
  would reject the corresponding live trade.
- **REQ-055**: Cost calibration shall be timestamped and versioned. Results shall
  disclose when present-day liquidity calibration is applied to older prices.
- **REQ-056**: Partial fills shall be disabled unless the live executor supports
  and reconciles them. The initial model is full fill or no fill.
- **CON-004**: Historical CEX volume may inform a coarse capacity warning but
  cannot override a stricter on-chain quote-calibrated cap.

### 3.7 Single-wallet portfolio simulation

- **REQ-060**: Replay shall maintain one wallet ledger containing stable balance,
  BNB gas reserve, token balances, pending intents, positions, realized PnL,
  unrealized PnL, fees, and x402 spend when applicable.
- **REQ-061**: The shared RiskPolicy shall size from current marked equity and
  stressed loss, then apply liquidity, pool-share, concentration, cash, reserve,
  correlation, daily-loss, and drawdown caps.
- **REQ-062**: Overlapping positions and intents shall compete for the same cash
  and risk budgets. Per-trade returns shall never be summed as if independently
  funded.
- **REQ-063**: Equity shall be marked at least hourly using prices available at
  that hour. Peak-to-current total drawdown shall include realized and unrealized
  PnL and all modeled costs.
- **REQ-064**: Missing a fresh mark shall block new exposure and flag equity as
  stale; it shall not silently value a position at zero or trigger liquidation.
- **REQ-065**: Qualification statistics shall count only transactions that satisfy
  the verified competition semantics. The replay shall not authorize compliance
  actions.
- **REQ-066**: Benchmarks shall use the same initial capital, date window, cost
  disclosure, and mark frequency as the strategy where applicable.

### 3.8 Predeclared ZEC diagnostic and wider validation

- **REQ-070**: The first diagnostic window is
  `2026-01-01T00:00:00Z` through `2026-06-20T23:59:59Z`, truncated to each feed's
  last fully closed bar.
- **REQ-071**: The predeclared diagnostic symbols are `ZEC/USDT` as the selected
  strong pair and `ETH/USDT`, `TRX/USDT`, and `APE/USDT` as contrast pairs. Missing
  data shall be reported; controls shall not be replaced after results are seen.
- **REQ-072**: The diagnostic is an exploratory signal-quality and implementation
  smoke test because ZEC was selected after observing strength. It is not
  out-of-sample evidence and cannot validate profitability.
- **REQ-073**: All strategy, risk, entry, exit, cost, and reporting parameters
  shall be frozen in the run manifest before the first outcome is calculated.
- **REQ-074**: No parameter shall be optimized on the diagnostic window. Any later
  change creates a new version and requires untouched validation data.
- **REQ-075**: After diagnostic correctness, the same engine shall run the full
  gold scannable universe and report coverage, missing-data, and survivorship
  limitations.
- **REQ-076**: Longer-history work shall use chronological train/validation/test
  or rolling walk-forward partitions. Training selects parameters, validation
  chooses among predeclared alternatives, and test is evaluated once.
- **REQ-077**: Fewer than 30 completed trades shall be labeled insufficient for a
  stable edge estimate regardless of win rate or headline return.
- **REQ-078**: Results shall be broken out by symbol, grade, stop source,
  checklist-DOL state, market regime where reconstructable, and cost scenario.
- **CON-005**: Multiple symbols, windows, thresholds, and variants shall be counted
  as multiple comparisons; the best result may not be presented alone.

### 3.9 Metrics, uncertainty, and benchmarks

- **REQ-080**: Signal-flow metrics shall include candidates scanned, authorized
  setups, pending orders, fills, expiries, vetoes, skips, completed trades,
  unresolved positions, and reason-code counts.
- **REQ-081**: Trade metrics shall include win rate with 95% Wilson interval,
  expectancy in R and USD after costs, average/median R, profit factor, payoff
  ratio, MAE, MFE, holding time, worst losing streak, and fill rate.
- **REQ-082**: Portfolio metrics shall include real-wallet and
  competition-adjusted total net return, hourly maximum drawdown, time under
  water, exposure time, turnover, cost drag by component, stable reserve,
  concentration, and qualification-day coverage.
- **REQ-083**: Sharpe, Sortino, and Calmar may be reported only with the sampling
  convention and small-sample caveat. Short-window annualized values shall be
  labeled extrapolations, not forecasts.
- **REQ-084**: Benchmarks shall include per-symbol buy-and-hold, equal-weight
  buy-and-hold for the evaluated basket, and the full-universe structural baseline
  versus reconstructable-momentum selection.
- **REQ-085**: Trade-return uncertainty shall use a dependence-aware method such
  as block bootstrap when sample size permits. An independence-based t-stat may
  appear only as an explicitly optimistic diagnostic.
- **REQ-086**: Reports shall lead with sample size, bias flags, cost scenario, and
  parity status before performance numbers.
- **CON-006**: Win rate alone is never sufficient evidence of profitability.

### 3.10 Reproducibility and audit artifacts

- **REQ-090**: A run shall emit immutable configuration, data-quality report,
  selection events, scanner outputs, intents, fills, exits, hourly equity,
  positions, metrics, and a machine-readable summary.
- **REQ-091**: Every event shall carry run ID, event time, decision time, symbol,
  identity key, source-data watermark, scanner/MIDAS commits, and reason codes.
- **REQ-092**: Repeating a run with identical inputs shall reproduce event order,
  quantities, fills, costs, and metrics within documented numeric tolerance.
- **REQ-093**: Report tables shall reconcile from raw events to trades, wallet
  ledger, and final equity. Unexplained accounting differences are validation
  failures.
- **REQ-094**: Research outputs shall never overwrite live state or be readable as
  executable intents by the live runner.

## 4. Interfaces & Data Contracts

### 4.1 ReplayConfig

```python
class ReplayConfig(TypedDict):
    run_id: str
    start_utc: str
    end_utc: str
    symbols: list[str]
    initial_equity_usd: str
    scanner_commit: str
    midas_commit: str
    decision_config_hash: str
    risk_config_hash: str
    selector_mode: Literal["full_universe", "historical_momentum", "forward_shadow"]
    execution_model: str
    entry_ttl_bars: int
    cost_scenario: Literal["base", "stress", "zero_cost_diagnostic"]
    identity_snapshot_hash: str
    data_snapshot_hash: str
```

### 4.2 ReplayEvent

```python
class ReplayEvent(TypedDict):
    run_id: str
    event_id: str
    event_type: str
    event_time_utc: str
    decision_time_utc: str | None
    identity_key: str | None
    symbol: str | None
    source_watermark: dict[str, str]
    reason_codes: list[str]
    payload: dict
```

### 4.3 SimulatedFill

```python
class SimulatedFill(TypedDict):
    intent_id: str
    side: Literal["buy", "sell"]
    quantity: str
    base_price: str
    fill_price: str
    lp_fee_usd: str
    gas_usd: str
    slippage_usd: str
    impact_usd: str
    competition_cost_usd: str
    fill_time_utc: str
    cost_scenario: str
```

### 4.4 ForwardCmcSnapshot

```python
class ForwardCmcSnapshot(TypedDict):
    snapshot_id: str
    observed_at_utc: str
    expires_at_utc: str
    provider: str
    skill_or_endpoint: str
    schema_version: str
    request_hash: str
    raw_response_hash: str
    normalized_payload: dict
    selector_config_hash: str
    rank_veto_clamp_output: dict
    x402_payment_id: str | None
```

### 4.5 ReplayReport

```python
class ReplayReport(TypedDict):
    run_manifest: ReplayConfig
    parity_status: Literal["live_parity", "research_execution_model", "invalid"]
    bias_flags: list[str]
    data_coverage: dict
    signal_flow: dict
    trade_metrics: dict
    portfolio_metrics: dict
    benchmark_metrics: dict
    uncertainty: dict
    accounting_reconciliation: dict
```

### 4.6 Component boundary

```text
HistoricalFrameAdapter / ForwardSnapshotAdapter
                -> Shared DecisionPipeline
                -> SimulatedExecutionAdapter
                -> Shared Portfolio/Risk/Lifecycle State
                -> Event Journal
                -> Metrics + Reconciliation + Report

Live adapters are never imported by replay to send transactions.
Replay adapters never contain alternate setup, stop, DOL, or sizing rules.
```

## 5. Acceptance Criteria

- **AC-001**: Given a decision at time `t`, when the scanner is called, then every
  supplied bar has `close_time <= t`.
- **AC-002**: Given a QML becomes known at an H1 close, when the decision bar had
  already touched its level, then no retroactive fill occurs.
- **AC-003**: Given a later bar never touches a pending QML entry, when the order
  expires, then it is counted as expired and not as a loss.
- **AC-004**: Given checklist DOL is false and campaign DOL is concrete, when the
  remaining live gates pass, then replay uses the campaign target without setting
  checklist DOL true.
- **AC-005**: Given campaign DOL is unresolved, when a QML is monitored, then no
  order or fixed-R target is created.
- **AC-006**: Given stop and target are touched in the same unresolved bar, when
  no lower-timeframe ordering evidence exists, then stop-first is recorded.
- **AC-007**: Given a position is open at the window boundary, when reporting
  runs, then it is marked to market, included in drawdown, and counted unresolved.
- **AC-008**: Given two simultaneous intents exceed cash or risk capacity, when
  deterministic priority runs, then only affordable intents proceed.
- **AC-009**: Given present-day CMC narrative data, when a historical run executes,
  then that data is absent from historical features.
- **AC-010**: Given no historical macro snapshot, when the historical lane runs,
  then macro clamp is `1.0` and the exclusion appears in bias flags.
- **AC-011**: Given base and stress costs, when the same trades are replayed, then
  stress net performance is not better solely because of lower modeled costs.
- **AC-012**: Given invalid OHLC or missing required frames, when data is audited,
  then the symbol receives an explicit failure event and remains in coverage
  denominators.
- **AC-013**: Given the ZEC diagnostic run, then the manifest pins the approved
  dates and four symbols before outcomes are calculated.
- **AC-014**: Given fewer than 30 completed trades, when the report renders, then
  it labels edge estimates insufficient and still reports the Wilson interval.
- **AC-015**: Given identical snapshots and configuration, when replay is repeated,
  then ordered events and final equity reconcile within numeric tolerance.
- **AC-016**: Given existing live strategy code changes, when parity tests run,
  then replay fails until the same behavior is available through the shared core.
- **AC-017**: Given current-universe membership is used historically, when results
  render, then survivorship bias is shown before performance metrics.
- **AC-018**: Given research execution semantics differ from live, when a report is
  produced, then parity status is `research_execution_model`, not `live_parity`.

## 6. Test Automation Strategy

- **Framework**: `pytest`, with deterministic decimal arithmetic for wallet and
  cost accounting where practical.
- **Method**: Test-driven development. Every behavior change begins with a failing
  unit or integration test demonstrating the intended causal contract.
- **Unit tests**:
  - as-of frame slicing and higher-timeframe closure;
  - data-audit reason codes;
  - no decision-bar fill and next-executable fill;
  - gap, same-bar stop/target, expiry, and unresolved-position behavior;
  - cost components and base/stress monotonicity;
  - Wilson interval and metric edge cases;
  - single-wallet cash, reserves, PnL, and drawdown math.
- **Contract tests**:
  - live/paper/replay call the same `DecisionPipeline` interface;
  - scanner exports canonical stop and campaign DOL fields;
  - no replay adapter imports or duplicates scanner/risk detector logic.
- **Integration tests**:
  - synthetic multi-timeframe fixtures with known event order;
  - overlapping intents and correlated-risk caps;
  - complete event-to-ledger-to-report reconciliation;
  - forward CMC snapshot persistence and replay ingestion.
- **Golden tests**:
  - one manually audited Direct QML winner;
  - one stop loss;
  - one unfilled expiry;
  - one unresolved boundary position;
  - one checklist-DOL-false setup with concrete campaign DOL;
  - one unresolved-campaign-DOL no-order case.
- **Property tests**:
  - future bars cannot alter an earlier decision record;
  - increasing costs cannot improve a fixed trade's net PnL;
  - position quantities cannot exceed balances or deterministic caps;
  - event accounting always reconciles to final equity.
- **Performance target**: The 2026 YTD four-symbol diagnostic shall complete fast
  enough for iterative verification; correctness takes precedence over vectorized
  shortcuts.

## 7. Rationale & Context

The existing research scripts are useful probes but do not represent the live
agent. They select QMLs independently, use fixed `2R`, omit true portfolio sizing,
and may discard unresolved trades. Those choices can inflate win rate, distort
return, and hide capital lock-up.

An event-driven replay is required because the strategy combines multi-timeframe
state, pending orders, limited capital, concurrent candidates, lifecycle changes,
and path-dependent drawdown. Vectorized per-trade return summation cannot model
those interactions faithfully.

ZEC is intentionally labeled exploratory because it was chosen after exhibiting
strength. ETH, TRX, and APE are frozen controls so weak results cannot be hidden by
post-hoc replacement. The wider-universe run is required before generalization.

CMC data has two categories. Multi-window momentum can be reconstructed from
historical OHLCV when coverage exists. Agent Hub narratives, skill outputs, macro
recommendations, and sector snapshots generally cannot be reconstructed unless
captured prospectively. Keeping historical and forward lanes separate prevents
current context from leaking backward while building data for future combined
replay.

## 8. Dependencies & External Integrations

### External Systems

- **EXT-001**: Historical OHLCV provider - closed Weekly, H12, and H1 bars with
  stable UTC timestamps and explicit provenance.
- **EXT-002**: CMC Agent Hub/API - forward selector/macro snapshots and any
  plan-available historical inputs whose point-in-time semantics are verified.
- **EXT-003**: TWAK/PancakeSwap quote path - timestamped live quote calibration
  for execution cost and capacity scenarios; no historical execution occurs.

### Infrastructure Dependencies

- **INF-001**: Immutable or content-addressed historical data snapshots.
- **INF-002**: Durable append-only replay event journal.
- **INF-003**: Deterministic UTC replay clock.
- **INF-004**: Artifact storage for manifests, reports, and forward CMC snapshots.

### Data Dependencies

- **DAT-001**: Gold identity snapshot and symbol-to-contract-to-data mapping.
- **DAT-002**: Required closed OHLC frames for each evaluated token.
- **DAT-003**: Versioned Track 1 cost and qualification assumptions.
- **DAT-004**: Timestamped live quote calibration at representative size tiers.
- **DAT-005**: Forward-captured raw and normalized CMC snapshots.

### Technology Platform Dependencies

- **PLT-001**: The pinned `magic-scanner` package and shared MIDAS decision core.
- **PLT-002**: Python event-replay and test framework using the repository's
  supported runtime and dependency manager.

### Compliance Dependencies

- **COM-001**: `TRACK1.MD` and later organizer clarifications for hourly equity,
  cost, trade-count, and qualification semantics.

## 9. Examples & Edge Cases

### 9.1 No retroactive QML fill

```text
10:00-11:00 H1 bar: trades through QML and closes to make QML valid
11:00 decision: Direct QML becomes knowable
Result: no fill on the 10:00-11:00 bar
Next fill opportunity: first permitted price observation after 11:00
```

### 9.2 Checklist DOL false, campaign DOL present

```text
Sweep: unresolved
Checklist DOL: false
Campaign DOL: prior-week high at 42.00
Grade: B
Result: setup may enter with 42.00 target; checklist evidence stays false
```

### 9.3 Campaign DOL unresolved

```text
QML: structurally valid and monitorable
Campaign DOL: None
Result: no intent, no fixed-2R target, reason=campaign_dol_unresolved
```

### 9.4 Same-bar ambiguity

```text
Open position stop: 90
Campaign DOL: 110
H1 bar range: low=89, high=111
No lower-timeframe ordering data
Base/stress outcome: stop executes first with sell-side costs
```

### 9.5 Replay boundary

```text
Position remains open at 2026-06-20T23:59:59Z
Result: mark to last valid price, include unrealized PnL and drawdown, count
unresolved, and do not drop the trade
```

### 9.6 Forward-only CMC context

```text
Historical date: 2026-02-01
Available inputs: as-of OHLCV-derived 7d/30d momentum
Unavailable inputs: unrecorded Agent Hub narrative and macro skill output
Result: neutral macro clamp, explicit exclusion flag, no present-day backfill
```

## 10. Validation Criteria

- The same decision-core code path authorizes live, paper, and replay intents.
- No future bar, current CMC context, or outcome can affect an earlier decision.
- Entry, stop, campaign DOL, grade, and lifecycle derive from one scanner as-of view.
- No fixed-R target or targetless position replaces unresolved campaign DOL.
- All filled and unresolved positions reconcile through one wallet ledger.
- Base and stress results include explicit nonzero execution costs.
- Coverage failures, survivorship bias, small samples, and parity deviations appear
  before performance claims.
- The ZEC diagnostic uses the frozen date range and controls without post-hoc
  substitution or parameter optimization.
- Forward CMC snapshots are immutable and sufficient for future combined replay.
- Repeated runs are deterministic and accounting reconciliation equals final equity.

## 11. Related Specifications / Further Reading

- `docs/superpowers/specs/2026-06-21-track1-spot-agent-design.md`
- Workspace `docs/agent-trading-rules.md`
- Workspace `docs/superpowers/specs/2026-06-20-active-execution-contract.md`
- Workspace `spec/spec-process-agent-trading-mode-of-operation.md`
- Workspace `TRACK1.MD`
