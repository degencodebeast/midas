---
title: Track 1 Spot Trading Agent Design
version: 1.1
date_created: 2026-06-21
last_updated: 2026-06-21
owner: degencodebeast
tags: [architecture, trading, spot, bnb-chain, twak, x402, coinmarketcap, risk]
---

# Introduction

This specification defines the active design for MIDAS as a bounded autonomous,
spot-long trading agent for BNB Hack Track 1. MIDAS uses CoinMarketCap (CMC) to
rank and veto candidates, the deterministic `magic-scanner` to authorize ICT/SMC
setups, a mandatory deterministic risk policy to size exposure, and Trust Wallet
Agent Kit (TWAK) as the sole live execution and signing layer.

The design also targets the Best Use of Trust Wallet Agent Kit special prize by
making self-custody, autonomous execution, bounded native x402 payments, and
on-chain proof substantive parts of the operating loop.

This specification supersedes the active-path interpretation in
`2026-06-15-bnb-track1-trading-agent-design.md`. The earlier Aster/perpetuals
design remains historical evidence and must not control the live hackathon path.

## 1. Purpose & Scope

### 1.1 Purpose

The agent shall maximize competition return without approaching the unknown
maximum-drawdown disqualification threshold. The operating posture is
**aggressive-but-survivable**: enter authorized Direct QML setups without waiting
for M15 confirmation, while enforcing conservative portfolio and execution caps.

### 1.2 In scope

- Contract-bound identity for the approved spot universe.
- CMC candidate ranking and deterministic vetoes.
- Clamp-only CMC macro-regime context and forward snapshot capture.
- Weekly/H12 campaign context, existing H4 refinement where available, and H1
  QML authorization from the scanner.
- Direct QML as the active hackathon execution path.
- Canonical structural stop consumption from the scanner.
- Fixed-fractional sizing with execution and portfolio caps.
- PancakeSwap-compatible spot swaps executed and signed through TWAK.
- Native x402 payment through TWAK for an allowlisted trade-loop data or tool
  request, subject to a separate deterministic spending budget.
- Receipt-aware, restart-safe execution and reconciliation.
- Position monitoring, structural exits, risk halts, audit records, and status.
- Daily competition qualification tracking.
- A causal portfolio replay lane running in parallel with live-readiness work.

### 1.3 Out of scope

- Fixing QMLs that the scanner incorrectly labels `superseded`.
- H1 MSS sweep-and-break strategy.
- Automatic M15 SCOB or MSS confirmation entries during the hackathon.
- The provisional four-hour M15 MSS locality rule on the active path.
- Perpetuals, shorts, leverage, Aster, DCA, averaging down, and stacking.
- Claims that CMC momentum selection or concentrated leaders are profitable
  before causal out-of-sample replay supports them.

The known supersession issue may cause missed trades. The agent shall accept that
failure mode rather than duplicate or bypass scanner QML authorization.

## 2. Definitions

- **Aggressive Direct QML**: Entry at the scanner-authorized H1 QML without
  requiring or waiting for M15 confirmation.
- **Canonical structural stop**: Stop beyond the reclaim-confirmed H12 sweep
  candle extreme, otherwise beyond the confirmed protective H12 pivot, including
  the scanner's configured structural buffer.
- **Campaign DOL**: A concrete directional move-completion target and source
  selected by the scanner from authoritative external liquidity. It is distinct
  from checklist qualification.
- **Checklist DOL**: A scoring binary requiring direction, entry, coherent
  sweep-derived stop, on-side target, and minimum reward-to-risk. It may be false
  while a concrete campaign DOL still exists.
- **Macro clamp**: A deterministic CMC-derived multiplier or veto that may only
  reduce deployment or stop new exposure; it cannot create a setup or increase
  size above the scanner/RiskPolicy result.
- **Gold identity**: Case-preserved competition symbol bound to one CMC ID, BSC
  chain ID, verified BEP-20 contract, decimals, and observed on-chain symbol.
- **Executability probe**: A fresh, trade-size quote and round-trip route check.
- **Strategy trade**: A position authorized by the scanner and RiskPolicy.
- **Compliance action**: A separately specified action intended only to satisfy
  a competition qualification rule. It is never a scanner setup.
- **UNKNOWN execution**: An operation that may have broadcast but cannot yet be
  classified as confirmed or reverted.
- **Stressed loss**: Expected loss using actual/quoted entry, structural stop,
  adverse exit slippage, fees, gas, and route impact.
- **Fresh CMC snapshot**: A timestamped selector snapshot within its configured
  time-to-live (TTL).
- **x402 payment**: A bounded, allowlisted machine payment initiated through
  TWAK for a verified data, inference, or tool request used in the trade loop.

## 3. Requirements, Constraints & Guidelines

### 3.1 Document authority and migration

- **REQ-001**: This specification shall control MIDAS's spot-agent architecture.
- **REQ-002**: Before live activation, the following workspace documents shall be
  updated to agree with this specification:
  - `docs/superpowers/specs/2026-06-20-active-execution-contract.md`
  - `docs/agent-trading-rules.md`
  - `spec/spec-process-agent-trading-mode-of-operation.md`
- **REQ-003**: Historical Aster/perpetuals specifications and plans shall be
  marked superseded rather than rewritten as if they had always described spot.
- **CON-001**: `TRACK1.MD` is competition-source material and shall not be edited.

### 3.2 Identity and market-data eligibility

- **REQ-010**: Live execution shall use BSC contract addresses, never ambiguous
  tickers, as token identity.
- **REQ-011**: A live token shall have a gold identity record.
- **REQ-012**: `USDf` and `USDF` shall remain case-distinct identities.
- **REQ-013**: On-chain `symbol()` and `decimals()` are corroborating metadata;
  they shall not replace CMC-ID-to-contract evidence.
- **REQ-014**: Each token shall declare a market-data symbol, data source,
  coverage status, and freshness status.
- **REQ-015**: Tokens without the closed OHLC frames required by the reviewed
  scanner profile shall be classified `UNSCANNABLE`, not silently omitted or
  treated as having no setup. The aggressive profile requires at least Weekly,
  H12, and H1; H4/D1 are consumed only where the pinned scanner already supports
  them.

### 3.3 CMC selector

- **REQ-020**: CMC may rank the gold, scannable universe using point-in-time
  multi-window strength, volume, volatility-expansion risk, sector-relative
  strength, and narrative context.
- **REQ-021**: CMC may veto deterministic falling-knife, stale-data, or explicit
  market-risk conditions defined in versioned configuration.
- **REQ-021A**: CMC macro-regime context may apply a versioned deployment clamp
  or explicit extreme-risk veto. It may only preserve or reduce deterministic
  exposure and shall never import a provider-suggested allocation directly.
- **REQ-022**: CMC shall not create a setup, direction, QML, POI, stop, target, or
  trade authorization.
- **REQ-023**: `location_candidate` shall remain research/location metadata and
  shall never be interpreted as `TAKE` or a tradeable setup.
- **REQ-024**: If no fresh CMC snapshot or permitted last-known snapshot exists,
  new entries shall stop; existing position monitoring and exits shall continue.
- **GUD-001**: Selector thresholds remain research parameters until causal replay
  establishes their effect after costs.

### 3.4 Scanner authorization and aggressive execution

- **REQ-030**: The scanner remains the sole source of strategy setup authority.
- **REQ-031**: A strategy trade requires a governing H12/D1 campaign POI, a valid
  H1 QML inside that POI, an A- or B-family grade, a canonical structural stop,
  and a concrete campaign DOL target.
- **REQ-032**: Missing reclaim-confirmed H12 sweep or RR-qualified checklist DOL
  may lower grade but shall not automatically invalidate an otherwise authorized
  B-grade Direct QML when a concrete campaign DOL still exists.
- **REQ-033**: In the active hackathon profile, Direct QML shall be selected
  immediately when REQ-031 passes.
- **REQ-034**: Direct QML shall outrank already-observed M15 SCOB or MSS evidence.
- **REQ-035**: Missing, unavailable, or stale M15 data shall not block Direct QML.
- **REQ-036**: M15 SCOB/MSS may be recorded as advisory evidence only and shall
  not change entry geometry, size, or create a duplicate entry.
- **REQ-037**: The agent shall not wait four hours, one H1 candle, or any M15
  confirmation interval before selecting Direct QML.
- **REQ-038**: Scanner supersession and lifecycle outputs shall be consumed as-is.
  MIDAS shall not revive a scanner-rejected or superseded QML.
- **REQ-039**: The scanner's public aggressive result shall expose the selected
  entry, canonical `stop_level`, `stop_source`, `stop_anchor`, `stop_anchor_bar`,
  `campaign_dol_level`, `campaign_dol_source`, and checklist-DOL binary from one
  internally consistent as-of evaluation.
- **REQ-039A**: If campaign DOL is unresolved, MIDAS shall retain the QML as
  monitorable scanner state but shall not create a live strategy intent. Fixed
  reward multiples shall not be fabricated as replacement targets.
- **CON-002**: The scanner change is limited to an explicit aggressive API mode
  that can derive Direct QML without reading M15 and export the existing canonical
  stop and campaign-DOL results listed in REQ-039. Detector semantics,
  supersession logic, and new H4/D1 detector implementations are banned from this
  implementation slice.

### 3.5 RiskPolicy

- **REQ-040**: RiskPolicy shall be mandatory and non-optional on every funds path.
  It shall be action-aware: entry and compliance actions may increase exposure,
  x402 actions may spend only their service budget, and `risk_exit` actions may
  only reduce an already-reconciled position.
- **REQ-041**: Empty or missing custom policy configuration shall activate an
  immutable fail-safe policy that denies exposure-increasing actions and permits
  only reconciled, exposure-reducing exits. No caller may skip policy evaluation.
- **REQ-042**: Position size shall use fixed-fractional stressed loss:

  ```text
  risk_budget_usd = current_equity_usd * risk_fraction
  base_qty = risk_budget_usd / stressed_loss_per_token
  final_qty = min(base_qty, liquidity_cap, pool_share_cap,
                  concentration_cap, available_cash_cap,
                  gap_and_execution_stress_cap)
  ```

- **REQ-043**: The structural stop shall never be tightened solely to increase
  position size.
- **REQ-044**: Initial defaults shall be:

  | Control | Default |
  |---|---:|
  | Live canary risk | 0.25% equity |
  | Standard A-grade risk | 0.50% equity |
  | B-grade/lower-confluence risk | 0.25% equity |
  | Total open stressed risk | 1.00% equity |
  | Concurrent strategy positions | 1 initially; hard maximum 2 |
  | Stable reserve | at least 30% |
  | Per-token notional | at most 25% equity |
  | Daily actual-loss halt | 1.50% of daily opening equity |
  | Consecutive stopped-trade halt | 3 |
  | Drawdown throttle | 3% peak-to-current drawdown |
  | New-entry halt | 5% peak-to-current drawdown |
  | Emergency reconciliation/review | 8% peak-to-current drawdown |

- **REQ-045**: All long crypto positions shall initially share one correlation
  bucket; total stressed risk shall be capped across the bucket.
- **REQ-046**: The pre-entry daily gate shall compare consistent USD values:

  ```text
  actual_daily_loss_usd
  + stressed_open_loss_usd
  + stressed_new_intent_loss_usd
  <= daily_loss_budget_usd
  ```

- **REQ-047**: Unreliable equity pricing shall block new entries but shall not
  trigger liquidation from a potentially false drawdown reading.
- **REQ-048**: A minimum order size shall never override RiskPolicy. If the safe
  size is below venue minimum or fee viability, the trade shall be skipped.
- **REQ-049**: The AI advisor may wait or reduce size only. It may not authorize,
  increase size, change direction, entry, stop, target, identity, or policy.
- **REQ-049A**: A macro clamp and AI clamp shall compose by taking the most
  restrictive result. Neither may increase the pre-clamp RiskPolicy quantity.
- **CON-003**: No leverage, averaging down, DCA, stopless entry, or user override
  may exceed deterministic caps.

### 3.6 Executability

- **REQ-050**: A discovery cache may identify potentially executable tokens, but
  it shall never authorize an order.
- **REQ-051**: Every order shall receive a fresh quote at intended size using
  contract addresses.
- **REQ-052**: The probe shall require positive output, route/provider evidence,
  acceptable impact and slippage, and an executable sell route.
- **REQ-053**: Missing price-impact or route fields shall fail closed rather than
  default to zero risk.
- **REQ-054**: Quote evidence shall include timestamp/block, source and destination
  contracts, amount, expected output, minimum output, impact, and expiry.
- **REQ-055**: Expired quotes shall be refreshed before submission.
- **REQ-056**: Sufficient BNB gas reserve shall be maintained separately from the
  stablecoin reserve.

### 3.7 Receipt-aware TWAK execution

- **REQ-060**: TWAK shall be the sole live swap/signing layer.
- **REQ-061**: Execution shall use this durable state machine:

  ```text
  INTENT_PERSISTED -> EXECUTING -> SUBMITTED -> MINED
  -> CONFIRMED | REVERTED | BROADCAST_UNKNOWN
  -> RECONCILED
  ```

- **REQ-062**: The intent, pre-trade balances, wallet nonce, quote, policy result,
  and idempotency key shall be persisted before invoking TWAK.
- **REQ-063**: CLI success requires process exit code zero, strict success JSON,
  and the swap transaction hash. An approval hash shall not count as the swap.
- **REQ-064**: A nonzero exit, timeout, malformed output, or missing hash after a
  possible broadcast shall become `BROADCAST_UNKNOWN`, not a safe retry.
- **REQ-065**: Confirmation requires a successful receipt and configured
  confirmation depth.
- **REQ-066**: Reconciliation requires token/stable balance deltas consistent with
  the action. Only `CONFIRMED` plus `RECONCILED` creates an open position.
- **REQ-067**: Any `BROADCAST_UNKNOWN` state shall globally halt new exposure until
  reconciliation completes.
- **REQ-068**: Restart recovery shall reconcile unfinished executions and shall
  never blindly retry them.
- **REQ-069**: Actual reconciled quantity and fill shall replace quoted values.
- **REQ-070**: If actual risk exceeds policy tolerance after reconciliation, the
  system shall halt and execute the configured controlled-reduction procedure.

### 3.8 Position management

- **REQ-080**: Position monitoring and exits shall remain active during every
  entry halt.
- **REQ-080A**: Entry halts, stale CMC data, and entry concentration limits shall
  not deny an otherwise safe `risk_exit`. Exit actions must still pass identity,
  fresh-route, balance, journal, receipt, and reconciliation controls.
- **REQ-081**: Stop exits shall use fresh sell-route evidence and actual wallet
  balance, not stale tracked quantity.
- **REQ-082**: A failed exit shall retain the position in managed state and raise
  an urgent alert; it shall not remove the position from state.
- **REQ-083**: Peak equity, daily anchor, positions, risk state, compliance state,
  and execution journal shall survive restart.
- **REQ-084**: State writes shall be atomic and integrity-protected. Missing or
  invalid integrity evidence shall halt new entries.
- **CON-004A**: The H12-pivot fallback may occasionally produce a macro-distant
  stop. MIDAS shall accept the resulting small size or skipped trade; it shall not
  invent an H1-local stop tier during this implementation slice.

### 3.9 Competition compliance

- **REQ-090**: The compliance ledger shall track confirmed eligible swaps per
  competition day and the seven-trade weekly qualifier.
- **REQ-091**: Strategy trades shall satisfy daily qualification whenever they
  meet the competition's counting semantics.
- **REQ-092**: Compliance shall not reinterpret `location_candidate`, a naked QML,
  or a scanner rejection as a strategy setup.
- **REQ-093**: A compliance-only action shall be disabled by default and shall not
  activate until a separate mini-spec verifies:
  - what the competition counts as a trade;
  - the authoritative day boundary;
  - whether eligible stable-to-stable swaps count;
  - minimum effective amount and simulated costs;
  - route, identity, receipt, and anti-wash-trading constraints.
- **REQ-094**: Any enabled compliance action shall be labeled and audited as
  `compliance`, never `strategy`, and shall still pass identity, RiskPolicy,
  executability, execution, and reconciliation gates.
- **REQ-095**: The agent shall maintain more than $1 of in-scope portfolio value
  during measured hours and a nonzero in-scope balance at competition start.
- **CON-004**: Compliance cannot bypass a halt, unknown execution, token allowlist,
  gas reserve, drawdown cap, or execution guard. This restriction does not block
  a policy-approved exposure-reducing `risk_exit`.

### 3.10 Offline replay, forward validation, and claims

- **REQ-100**: Historical causal replay shall use only point-in-time data that is
  available or reproducible as of each decision, including scanner frames,
  reconstructable momentum, RiskPolicy, portfolio constraints, and versioned
  cost assumptions.
- **REQ-101**: Replay work shall run in parallel and shall not block the minimum
  safe live path.
- **REQ-102**: Live defaults shall remain conservative until replay evidence
  supports a versioned change.
- **REQ-103**: Historical replay shall execute the same decision pipeline as live:
  scanner authorization, aggressive Direct QML selection, canonical stop,
  campaign DOL, RiskPolicy, portfolio gates, and lifecycle exits. A live macro
  clamp shall run only when its point-in-time input exists; otherwise replay uses
  an explicit neutral clamp and records the exclusion. Only research adapters and
  declared unavailable-input variants may differ.
- **REQ-104**: Agent Hub skill outputs, narratives, sector snapshots, and macro
  recommendations that cannot be reconstructed point-in-time shall be excluded
  from historical claims and validated through forward shadow snapshots.
- **REQ-105**: MIDAS shall persist every live CMC input, raw/normalized values,
  timestamp, TTL, configuration version, and resulting rank/veto/clamp so future
  combined replay is possible.
- **REQ-106**: The detailed replay methodology is governed by
  `2026-06-21-causal-replay-design.md`.
- **CON-005**: No profitability or top-rank claim may be made from current manual
  selector observations or scanner charts alone.

### 3.11 TWAK and x402 prize objective

- **REQ-110**: TWAK shall remain the sole live signing and transaction-processing
  authority for spot swaps and native x402 payments.
- **REQ-111**: At least one verified x402-paid data, inference, or tool request
  shall contribute real information to the candidate-selection or risk-context
  loop before the submission claims native x402 integration.
- **REQ-112**: x402 destinations, service identifiers, chain, asset, and maximum
  price per request shall be allowlisted. Unknown or changed payment terms shall
  fail closed.
- **REQ-113**: x402 spending shall have per-request and daily USD caps, sufficient
  gas checks, idempotency, durable intent/payment/result records, and operator
  alerts for unknown outcomes. It shall pass the mandatory action-aware funds
  policy before TWAK invocation.
- **REQ-114**: x402 failure may make new CMC-dependent entries unavailable, but it
  shall never block position monitoring, reconciliation, or a protective exit.
- **REQ-115**: The demo and audit record shall link scanner decision, RiskPolicy,
  x402 request where applicable, TWAK-signed swap hash, receipt, and reconciled
  balance change without exposing private keys or secrets.
- **CON-006**: A README mention, mocked payment, or unrelated x402 call shall not
  be represented as trade-loop integration.

## 4. Interfaces & Data Contracts

### 4.1 IdentityRecord

```python
class IdentityRecord(TypedDict):
    competition_symbol: str
    cmc_id: int
    cmc_slug: str
    chain_id: int
    contract_address: str
    decimals: int
    onchain_symbol: str
    market_data_source: str
    market_data_symbol: str
    coverage_status: str
    verification_status: str
    verified_at: str
    sources: list[str]
```

### 4.2 CandidateSnapshot

```python
class CandidateSnapshot(TypedDict):
    identity_key: str
    observed_at: str
    expires_at: str
    rank: int
    momentum_7d: float | None
    momentum_30d: float | None
    volume_change: float | None
    volatility_risk: str
    sector_context: str | None
    macro_regime: str | None
    macro_clamp: float
    vetoed: bool
    veto_reasons: list[str]
```

### 4.3 AuthorizedSetup

```python
class AuthorizedSetup(TypedDict):
    setup_id: str
    identity_key: str
    direction: Literal["long"]
    grade: str
    governing_poi_timeframe: str
    governing_poi_id: str
    qml_id: str
    qml_state: str
    entry_type: Literal["direct_qml"]
    entry: float
    structural_stop: float
    stop_source: str
    stop_anchor: float
    stop_anchor_bar: int
    campaign_dol: float
    campaign_dol_source: str
    sweep_confirmed: bool
    checklist_dol: bool
    scanner_commit: str
    observed_at: str
```

### 4.4 RiskDecision

```python
class RiskDecision(TypedDict):
    approved: bool
    denied_by: str | None
    reasons: list[str]
    equity_usd: float
    risk_fraction: float
    risk_budget_usd: float
    stressed_loss_usd: float
    base_qty: float
    final_qty: float
    applied_caps: dict[str, float]
```

### 4.5 X402PaymentRecord

```python
class X402PaymentRecord(TypedDict):
    payment_id: str
    purpose: str
    provider: str
    service_id: str
    chain_id: int
    asset_contract: str
    quoted_amount_usd: float
    approved_amount_usd: float
    max_request_usd: float
    max_daily_usd: float
    daily_spend_before_usd: float
    request_hash: str
    idempotency_key: str
    tx_hash: str | None
    state: str
    result_hash: str | None
    created_at: str
    updated_at: str
```

### 4.6 ExecutionRecord

```python
class ExecutionRecord(TypedDict):
    intent_id: str
    idempotency_key: str
    identity_key: str
    setup_id: str | None
    purpose: Literal["strategy", "compliance", "risk_exit"]
    state: str
    quote: dict
    policy: RiskDecision
    pre_balances: dict[str, str]
    pre_nonce: int
    tx_hash: str | None
    receipt: dict | None
    post_balances: dict[str, str] | None
    error: str | None
    created_at: str
    updated_at: str
```

### 4.7 Component boundaries

```text
IdentityRegistry -> X402BudgetedDataClient -> CmcSelector -> ScannerGateway
-> MacroClamp -> RiskPolicy
-> ExecutabilityProbe -> ExecutionJournal -> TwakSpotExecutor
-> ReceiptReconciler -> PositionManager

ComplianceLedger observes confirmed ExecutionRecords but cannot call
ScannerGateway or TwakSpotExecutor directly. If a later mini-spec enables a
compliance action, its scheduler must submit the request through RiskPolicy and
the same execution coordinator used by strategy intents.
```

## 5. Acceptance Criteria

- **AC-001**: Given an authorized A/B Direct QML, when M15 data is absent, then
  aggressive mode returns a Direct QML setup with the canonical stop.
- **AC-002**: Given an authorized Direct QML and existing M15 SCOB/MSS evidence,
  when aggressive mode runs, then Direct QML remains selected and M15 is metadata.
- **AC-003**: Given a QML the scanner marks superseded, when MIDAS evaluates it,
  then MIDAS does not revive or trade it.
- **AC-004**: Given only a `location_candidate`, when the decision pipeline runs,
  then no strategy intent is created.
- **AC-005**: Given MIDAS receives a scanner result, when it builds the setup, then
  the stop equals the scanner's canonical structural stop and is not derived from
  `qml_key_level` plus a fixed percentage.
- **AC-005A**: Given MIDAS receives an aggressive scanner result, then the entry,
  stop level/source/anchor/bar, campaign DOL level/source, and checklist-DOL value
  are present and come from the same as-of evaluation.
- **AC-005B**: Given checklist DOL is false but campaign DOL is concrete, when the
  remaining A/B setup requirements pass, then the lower-grade setup may proceed
  using that campaign target without fabricating checklist confluence.
- **AC-005C**: Given campaign DOL is unresolved, when MIDAS evaluates the QML,
  then it remains monitorable but creates no live strategy intent or fixed-R target.
- **AC-006**: Given policy configuration is missing, when any funds action is
  attempted, then exposure-increasing execution is denied before TWAK invocation;
  only a reconciled exposure-reducing exit may proceed under fail-safe policy.
- **AC-007**: Given CMC data is stale beyond permitted TTL, when new entries are
  evaluated, then entries stop while open-position exits remain operational.
- **AC-008**: Given safe size is below venue minimum, when sizing runs, then the
  setup is skipped rather than rounded up.
- **AC-009**: Given two correlated longs each risk 0.5%, when a third 0.5% intent
  is evaluated, then it is denied by the 1% bucket cap.
- **AC-010**: Given TWAK exits nonzero after possible broadcast, when the runner
  handles the result, then state becomes `BROADCAST_UNKNOWN` and no retry occurs.
- **AC-011**: Given a successful transaction receipt but no expected balance
  delta, when reconciliation runs, then no open position is created.
- **AC-012**: Given an unfinished execution exists after restart, when MIDAS boots,
  then it reconciles before allowing any new exposure.
- **AC-013**: Given a sell fails, when position state is updated, then the position
  remains managed and an urgent alert is emitted.
- **AC-014**: Given no qualifying strategy trade occurred today, when compliance
  fallback has not been separately verified and enabled, then no automatic
  compliance trade occurs.
- **AC-015**: Given a compliance action is enabled later, when it executes, then it
  is separately labeled and passes every non-strategy safety gate.
- **AC-016**: Given the agent restarts, when state loads, then peak equity, daily
  anchor, execution journal, compliance ledger, and positions are preserved.
- **AC-017**: Given a drawdown or stale-CMC entry halt is active, when a managed
  position reaches its stop, then a policy-approved `risk_exit` remains executable.
- **AC-018**: Given CMC proposes a larger allocation than RiskPolicy, when the
  macro clamp is applied, then final size does not exceed the deterministic base.
- **AC-019**: Given an allowlisted x402 request exceeds its request or daily cap,
  when payment is evaluated, then no payment occurs and the denial is audited.
- **AC-020**: Given x402 is unavailable while a position is open, when its stop is
  reached, then monitoring and the TWAK protective exit remain operational.
- **AC-021**: Given an x402-paid CMC request and a resulting live trade, when the
  audit trail is rendered, then the request, decision, swap receipt, and balance
  reconciliation are linked without exposing signing secrets.

## 6. Test Automation Strategy

- **Framework**: `pytest` for Python; existing frontend checks for dashboard changes.
- **Method**: TDD for every behavior change; each new test must be observed failing
  for the intended reason before implementation.
- **Unit tests**:
  - identity ambiguity and case preservation;
  - selector ranking, vetoes, TTL, and stale behavior;
  - macro clamp composition and no-size-increase invariant;
  - aggressive Direct QML with existing or absent M15;
  - canonical stop and campaign-DOL mapping;
  - checklist-DOL false with campaign target versus unresolved campaign DOL;
  - fixed-fractional sizing and every cap;
  - drawdown, daily stress, correlation, and halt behavior;
  - strict TWAK output parsing and state transitions;
  - x402 allowlist, budgets, idempotency, persistence, and unknown outcomes.
- **Integration tests**:
  - scanner commit and setup contract;
  - quote-to-intent-to-receipt-to-balance reconciliation;
  - restart during `EXECUTING`, `SUBMITTED`, and `BROADCAST_UNKNOWN`;
  - failed exits remain managed;
  - policy no-bypass across strategy, compliance, and risk exits.
- **End-to-end tests**:
  - paper spot cycle across a gold token;
  - dry-run TWAK quote with zero funds;
  - minimum-value live canary after explicit operator activation.
- **Regression tests**: Scanner supersession behavior is not changed; existing
  scanner suite must remain green after the narrow aggressive API addition.
- **Offline research tests**: point-in-time replay, next-executable-price fills,
  costs, no look-ahead, portfolio overlap, daily qualification, and drawdown.
- **Forward research tests**: immutable CMC snapshot capture, schema/config version,
  normalized-output reproducibility, and future replay ingestion.

## 7. Rationale & Context

Direct QML is the strategy owner's preferred aggressive hackathon entry. Requiring
M15 first creates missed-entry risk and contradicts that intent. M15 remains useful
as evidence and as a deferred confirmation strategy, but not as active-path
authorization.

The scanner contains a known supersession concern. Reopening that lifecycle work
under the deadline creates greater risk than accepting missed setups. The safe
failure is therefore omission, never agent-side revival.

Checklist DOL and campaign DOL are intentionally separate. Missing sweep or RR
qualification may lower a setup to B-grade while leaving a real campaign target.
When the campaign target itself is unresolved, preserving the QML for monitoring
does not justify opening a targetless position.

The H12-pivot fallback is deliberately conservative. A distant pivot reduces
fixed-fractional size or causes a skip; this is preferable to introducing an
unvalidated tighter stop under the hackathon deadline.

Track 1 requires at least one trade per day and seven across the week, but the
provided rules do not define all counting semantics. A compliance lane is modeled
so the obligation is visible, while automatic fallback remains disabled until its
behavior is separately verified and approved.

## 8. Dependencies & External Integrations

### External Systems

- **EXT-001**: CMC Agent Hub/API - candidate data, context, forward snapshots,
  and an x402-paid request used in the trade loop.
- **EXT-002**: TWAK - sole local signing, spot swap, and native x402 payment layer.
- **EXT-003**: BNB Smart Chain RPC - token metadata, receipts, confirmations,
  balances, nonce, and reconciliation.
- **EXT-004**: PancakeSwap-compatible liquidity - routes surfaced through TWAK.

### Infrastructure Dependencies

- **INF-001**: Durable local or VPS-mounted state storage with atomic writes.
- **INF-002**: Reliable process supervision and restart behavior.
- **INF-003**: Structured audit logs and urgent operator alerts.

### Data Dependencies

- **DAT-001**: Case-preserved official eligible-token list.
- **DAT-002**: Gold identity records for the activated universe.
- **DAT-003**: Closed Weekly, H12, and H1 OHLC data required by the aggressive
  profile; existing H4/D1 refinement may be consumed when the pinned scanner
  already provides it.
- **DAT-004**: M15 data is optional advisory evidence in aggressive mode.
- **DAT-005**: Reconstructable historical momentum inputs for causal replay.
- **DAT-006**: Forward-captured raw and normalized CMC selector/macro snapshots.

### Compliance Dependencies

- **COM-001**: `TRACK1.MD` competition requirements.
- **COM-002**: Verified trade-count semantics and competition-day boundary before
  enabling compliance fallback.

## 9. Examples & Edge Cases

### 9.1 Aggressive setup with M15 evidence

```text
CMC: candidate ranked and not vetoed
Scanner: valid A-grade H1 QML in governing H12 POI
Stop: H12 reclaim-sweep candle extreme plus structural buffer
M15: chained SCOB exists
Decision: Direct QML now; chained SCOB stored as advisory evidence
```

### 9.2 Aggressive setup without M15

```text
CMC: fresh candidate
Scanner: valid B-grade QML, missing H12 sweep, canonical H12 pivot stop
M15: unavailable
Decision: Direct QML may proceed at lower risk; no fabricated confirmation
```

### 9.3 Known supersession concern

```text
Scanner: QML marked superseded
MIDAS: no setup
Outcome: missed trade accepted; no agent-side QML reconstruction
```

### 9.4 Checklist DOL missing but campaign target present

```text
Scanner: valid B-grade QML; checklist_dol=false; campaign_dol=prior-week high
Decision: lower-grade Direct QML may proceed using the prior-week-high target
Outcome: no sweep or RR-qualified checklist evidence is fabricated
```

### 9.5 Campaign DOL unresolved

```text
Scanner: structurally valid QML; campaign_dol_level=None
Decision: preserve monitorable QML state; do not create a live intent
Outcome: no fixed-2R replacement and no targetless position
```

### 9.6 Unknown broadcast

```text
TWAK process: timeout after wallet nonce changed
Transaction hash: unavailable
State: BROADCAST_UNKNOWN
Outcome: global entry halt; inspect nonce, balances, and recent receipts; no retry
```

### 9.7 No qualifying trade near day boundary

```text
Strategy trades today: 0
Compliance fallback: disabled pending verified counting semantics
Outcome: urgent qualification-risk alert; no disguised location trade
```

### 9.8 Macro-distant pivot fallback

```text
Scanner: valid QML; no coherent sweep; latest confirmed H12 pivot is far away
RiskPolicy: safe quantity falls below fee-viable or venue-minimum size
Outcome: skip; do not tighten the stop to an H1 pivot
```

### 9.9 x402 request failure during an open position

```text
x402 CMC request: unavailable
New CMC-dependent entries: halted
Existing position: monitored normally; stop exit remains available through TWAK
```

## 10. Validation Criteria

- All requirements have corresponding tests or explicit external verification.
- No active code path can invoke TWAK without RiskPolicy.
- No position is created before receipt and balance reconciliation.
- Direct QML works without M15 and remains selected when M15 evidence exists.
- Canonical scanner stops reach sizing and position management unchanged.
- Campaign DOL reaches live and replay exits unchanged; unresolved campaign DOL
  cannot become a fixed-R or targetless live trade.
- Scanner supersession logic remains untouched.
- CMC cannot originate a setup or increase risk.
- CMC snapshots required for future combined replay are captured prospectively.
- x402 is bounded, auditable, used in the trade loop, and cannot block risk exits.
- Compliance cannot masquerade as strategy or bypass safety controls.
- Historical perp documents are clearly superseded before deployment.
- The active scanner dependency is pinned to the reviewed aggressive-mode commit.

## 11. Related Specifications / Further Reading

- Workspace `TRACK1.MD`
- Workspace `docs/superpowers/specs/2026-06-20-active-execution-contract.md`
- Workspace `docs/agent-trading-rules.md`
- Workspace `spec/spec-process-agent-trading-mode-of-operation.md`
- `docs/superpowers/specs/2026-06-15-bnb-track1-trading-agent-design.md`
- `docs/superpowers/specs/2026-06-15-venue-spike-findings.md`
- `docs/superpowers/specs/2026-06-21-causal-replay-design.md`
