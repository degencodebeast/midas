# Track 1 Spot Agent — Implementation Plan

> **SUPERSEDED — DO NOT EXECUTE.** This architectural roadmap has been replaced by
> `2026-06-21-scanner-contract-plan.md`, `2026-06-21-midas-spot-runtime-plan.md`, and
> `2026-06-21-causal-replay-plan.md`. The three replacement plans contain the reviewed interfaces,
> TDD steps, and corrected TWAK-only x402 authority.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Each task below is scoped (files + interface + tests + acceptance refs); the executing subagent expands it into bite-sized red→green→commit TDD steps. Steps use `- [ ]` tracking.

**Goal:** Turn MIDAS (`magic-agent`) into a bounded, spot-long, self-custody Track 1 trading agent: CMC ranks/vetoes → frozen scanner authorizes an aggressive Direct QML → deterministic RiskPolicy sizes → receipt-aware TWAK/PancakeSwap execution, with a parallel causal-replay validator.

**Architecture:** One shared `DecisionPipeline` (live/paper/replay) behind a contract-bound identity registry. The frozen scanner is the only setup authority; CMC is rank/veto only; RiskPolicy is mandatory and action-aware; TWAK is the sole signer; nothing becomes a position until receipt + balance reconciliation.

**Tech Stack:** Python (uv), pytest/TDD, `magic-scanner` (commit-pinned), ccxt (data), TWAK CLI/MCP, BNB RPC (web3), CMC Agent Hub (MCP/x402), Decimal accounting.

**Authoritative specs:** `midas/docs/superpowers/specs/2026-06-21-track1-spot-agent-design.md` (REQ-/AC- refs below) and `…/2026-06-21-causal-replay-design.md`.

---

## Step 0 — Read before coding (every implementer)

Read in order; treat as binding context, do not paste into commits:
1. `midas/docs/superpowers/specs/2026-06-21-track1-spot-agent-design.md`
2. `docs/superpowers/specs/2026-06-20-active-execution-contract.md`
3. `docs/agent-trading-rules.md`
4. `spec/spec-process-agent-trading-mode-of-operation.md`
5. `midas/docs/superpowers/specs/2026-06-21-causal-replay-design.md`
6. `TRACK1.MD`
7. `CLAUDE.md` (workspace + `trading-scanner/CLAUDE.md`: reviews on opus; heavy/logic tasks on opus)

**Reference repos (patterns only — borrow, don't copy; only Boomerang is MIT):** `boomerang-ai` (registry bootstrap `data/eligible_tokens.json`, peak-equity DD, pool/slippage/oracle checks, atomic persistence, audit), `cool_projects/trading-agent` (one-core-shared-by-modes, typed signals, `_verify_universe` quote-discovery, action gates), `cool_projects/BHRAMHA-Trading-System` (fixed-fractional `calculate_position_size`, Wilson bounds — reject ATR/astrology stops), `bnbagent-sdk` (registration/identity), `auto-ninja-trader`/`altpulse`/`AlgoBotVMC-demo`/`llm-trading-bot` (survey for executor/reconciliation/risk patterns only).

---

## File structure (created/modified)

**trading-scanner (narrow, frozen-scope change — opus implementer):**
- Modify `src/magic_scanner/detectors/entry.py` — extract campaign-DOL resolution from sweep qualification; add aggressive Direct QML mode.
- Modify `src/magic_scanner/scan.py` — export stop + DOL on the scan result contract.
- Create `tests/test_aggressive_direct_qml.py`, extend `tests/test_entry.py`, `tests/test_scan.py`.

**MIDAS (`midas/src/magic_agent/`):**
- Create `identity/registry.py`, `identity/verify.py`, `identity/eligible.py`
- Create `selection/cmc_selector.py`, `selection/forward_snapshot.py`
- Modify `scanner_gateway.py`, `setup_view.py` (kill fabricated stop)
- Create `risk/policy.py`, `risk/sizing.py`, `risk/drawdown.py`, `risk/caps.py`
- Create `execution/probe.py`, `execution/journal.py`, `execution/twak_spot.py`, `execution/reconcile.py`, `execution/state_machine.py`
- Create `execution/x402.py`
- Modify `runner.py`, `models.py`, `cli.py`, `treasury.py`, `status.py` (spot pivot; remove Aster/perp from active path)
- Create `position/manager.py`, `compliance/ledger.py`, `state/journal.py`
- Create `replay/` package (engine, adapters, costs, metrics, report)
- Tests mirror each module under `midas/tests/`.

---

## Phase A — Scanner export + aggressive Direct QML  (prerequisite; opus)

### Task 1 — Export concrete campaign DOL independent of sweep
**Why:** `dol.py:69` already computes concrete targets, but `entry.py:510` returns early without a coherent sweep, and `scan.py:84` exports neither stop nor DOL. Campaign DOL must exist without a sweep; checklist DOL stays false without sweep/RR; lifecycle may be unresolved while the concrete target exports.
**Files:** Modify `entry.py` (decouple DOL resolution from sweep gate), `scan.py` (add fields). Test `tests/test_entry.py`, `tests/test_scan.py`.
**Tests (write first):** campaign DOL present with no sweep; `checklist_dol=false` while `campaign_dol_level` concrete; no target candidates → `campaign_dol=None`; existing golden suite stays green.
**Acceptance:** spot-spec REQ-004/REQ-031/REQ-032/REQ-033; replay AC-004/AC-005.
**Commit:** `feat(scanner): export concrete campaign DOL independent of sweep`.

### Task 2 — Export canonical structural stop on the scan contract
**Files:** Modify `scan.py` (export `stop_level/source/anchor/bar` from `risk.py:27` resolver), no detector-semantics change. Test `tests/test_scan.py`.
**Tests:** scan result carries canonical stop fields equal to `resolve_structural_stop` output; long-side macro-distant pivot yields same small/skip behavior (no H1-local tier).
**Acceptance:** spot REQ-031/AC-005; CON-002 (no new H4/D1 work; no supersession change).
**Commit:** `feat(scanner): surface canonical structural stop on scan result`.

### Task 3 — Aggressive Direct QML mode (M15-independent)
**Files:** Modify `entry.py` (explicit aggressive mode: select Direct QML immediately from valid H1/H12; M15 advisory only; no 4-hour gate). Test `tests/test_aggressive_direct_qml.py`.
**Tests:** authorized A/B QML with no M15 → Direct QML returned with canonical stop (AC-001); with M15 SCOB/MSS present → Direct QML still selected, M15 = metadata (AC-002); superseded QML → no setup (AC-003); full scanner suite green (regression).
**Acceptance:** spot REQ-030/033-038, CON-002.
**Commit:** `feat(scanner): aggressive Direct QML mode (M15-independent)`. Then bump MIDAS `pyproject.toml` scanner `rev` to the new commit; `uv lock`.

---

## Phase B — Identity  (sonnet impl, opus review)

### Task 4 — Gold identity registry (contract-first)
**Files:** `identity/registry.py` (`IdentityRecord` per spec §4.1), `identity/eligible.py` (parse case-preserved `TOKENS.MD`; keep `USDf`≠`USDF`). Bootstrap from `boomerang-ai/data/eligible_tokens.json`. Test `tests/identity/`.
**Tests:** case distinct USDf/USDF (REQ-012); ambiguous tickers (B/H/M/U/APE/dup-SLX) require explicit verification; ticker-only joins rejected (REQ-016).
**Acceptance:** REQ-010–015. **Commit:** `feat(identity): contract-first gold registry`.

### Task 5 — On-chain + CMC-ID verification (exact, not substring)
**Files:** `identity/verify.py` (web3 `symbol()`/`decimals()` exact-match; bind CMC ID; record `verified_at`/`sources`/`coverage_status`). Gold-verify ZEC/DEXE/TRX/APE first.
**Tests:** exact symbol match (no `"B" in "BUSD"` false positive); UNSCANNABLE when frames unavailable (REQ-015).
**Acceptance:** REQ-011/013/014. **Commit:** `feat(identity): exact on-chain + CMC-ID verification`.

---

## Phase C — Scanner gateway  (opus — parity-critical)

### Task 6 — Consume canonical stop + campaign DOL; delete fabricated stop
**Files:** Modify `scanner_gateway.py`, `setup_view.py` (remove qml_key_level+buffer fabrication at `setup_view.py:18`; map scanner exports → `AuthorizedSetup` spec §4.3). Test `tests/test_setup_view.py`.
**Tests:** stop equals scanner canonical, never `qml_key_level`±% (AC-005); `campaign_dol=None` → no AuthorizedSetup; superseded → none.
**Acceptance:** REQ-030/031/038. **Commit:** `feat(agent): consume canonical scanner stop+DOL`.

---

## Phase D — RiskPolicy  (opus — correctness-critical)

### Task 7 — Action-aware policy + fail-safe on missing config
**Files:** `risk/policy.py` (`RiskDecision` §4.4; entry/compliance may increase, `risk_exit` only reduces; missing config → immutable deny-increase/allow-exit). Test `tests/risk/`.
**Tests:** missing config denies exposure-increase before TWAK (AC-006) but allows reconciled exit; no caller skips policy.
**Acceptance:** REQ-040/041, REQ-080A, AC-017. **Commit:** `feat(risk): mandatory action-aware policy + fail-safe`.

### Task 8 — Fixed-fractional stressed sizing + min-of-caps
**Files:** `risk/sizing.py` (BHRAMHA fixed-fractional core, extended): `base = risk_budget/stressed_loss`; `final = min(base, liquidity, pool_share, concentration, cash, gap_exec_stress)`. `risk/caps.py`.
**Tests:** wider stop → smaller size; stop never tightened to size up (REQ-043); below venue-min → skip not round-up (AC-008).
**Acceptance:** REQ-042/043/048. **Commit:** `feat(risk): fixed-fractional stressed sizing with caps`.

### Task 9 — Correlated bucket + peak-equity DD + stress gate
**Files:** `risk/drawdown.py` (peak-to-current; daily anchor; throttle 3% / halt 5% / review 8%), bucket cap in `risk/caps.py`. Defaults table = spot REQ-044.
**Tests:** third 0.5% correlated long denied by 1% bucket (AC-009); `actual_daily_loss + stressed_open + stressed_new ≤ budget` gate (REQ-046); stale equity blocks entry, not liquidation (REQ-047).
**Acceptance:** REQ-044/045/046/047, REQ-049 (AI reduce-only). **Commit:** `feat(risk): correlation bucket + peak-equity drawdown gate`.

---

## Phase E — Selection  (sonnet impl, opus review)

### Task 10 — CMC selector (rank/veto only)
**Files:** `selection/cmc_selector.py` (`CandidateSnapshot` §4.2; multi-window momentum + volume + volatility + sector/narrative; falling-knife veto; macro clamp-only ≤1.0; TTL → stop new entries on stale). Test `tests/selection/`.
**Tests:** CMC never creates setup/direction (REQ-022); `location_candidate` never TAKE (REQ-023); stale snapshot stops entries, exits continue (REQ-024/AC-007); macro never increases size.
**Acceptance:** REQ-020–024, GUD-001. **Commit:** `feat(selection): CMC rank/veto selector`.

### Task 11 — Forward CMC snapshot capture
**Files:** `selection/forward_snapshot.py` (`ForwardCmcSnapshot` replay §4.4; persist raw+normalized+TTL+x402 id). 
**Tests:** every live CMC call persisted immutably for future combined replay.
**Acceptance:** replay REQ-024. **Commit:** `feat(selection): forward CMC snapshot capture`.

---

## Phase F — Execution  (opus — safety-critical)

### Task 12 — Fail-closed executability probe
**Files:** `execution/probe.py` (fresh trade-size quote by contract; require positive output, route/provider, impact/slippage, sell route; missing fields fail closed; quote expiry; gas reserve separate). Borrow Binacci `_verify_universe` shape but fail-closed at size.
**Tests:** missing price-impact → fail closed not zero-risk (REQ-053); expired quote refreshed (REQ-055).
**Acceptance:** REQ-050–056. **Commit:** `feat(exec): fail-closed executability probe`.

### Task 13 — Execution journal + state machine
**Files:** `execution/journal.py`, `execution/state_machine.py` (`ExecutionRecord` §4.5; `INTENT_PERSISTED→EXECUTING→SUBMITTED→MINED→CONFIRMED|REVERTED|BROADCAST_UNKNOWN→RECONCILED`).
**Tests:** intent+balances+nonce+quote+idempotency persisted before invoke (REQ-062); restart reconciles unfinished, never retries (AC-012).
**Acceptance:** REQ-061/062/068. **Commit:** `feat(exec): durable execution state machine`.

### Task 14 — Receipt-aware TWAK spot executor (own runner)
**Files:** `execution/twak_spot.py`, `execution/reconcile.py` (own runner — check exit code, require swap tx hash, approval≠swap; nonzero/timeout/no-hash → BROADCAST_UNKNOWN; receipt status==1 + confirmations; token Δ>0 & stable Δ<0 → RECONCILED). **Do NOT reuse Boomerang executor** (it's fail-open).
**Tests:** nonzero exit after possible broadcast → BROADCAST_UNKNOWN, no retry (AC-010); receipt ok but no balance delta → no position (AC-011); BROADCAST_UNKNOWN halts new exposure (REQ-067); actual fill replaces quote (REQ-069); over-risk after reconcile → controlled reduction (REQ-070).
**Acceptance:** REQ-060/063–070. **Commit:** `feat(exec): receipt-aware TWAK spot executor`.

### Task 15 — Spot pivot: remove Aster/perp from active path
**Files:** Modify `runner.py` (`policy_config=None` bypass at `runner.py:96` removed → fail-safe), `models.py`, `cli.py`, `treasury.py`, `status.py`, retire `aster.py` from active path (keep as superseded module/test). 
**Tests:** no active path constructs shorts/leverage/perp collateral; paper spot cycle on a gold token end-to-end.
**Acceptance:** spot §1.3 out-of-scope, REQ-041. **Commit:** `refactor(agent): spot-long pivot, remove perp active path`.

### Task 16 — x402 prize objective (bounded)
**Files:** `execution/x402.py` (pay-per-request for CMC data/inference in the trade loop; spending allowlist + daily budget + audit + fail-safe-on-exhaustion). 
**Tests:** x402 spend capped to daily budget; exhaustion fails safe (no trade dependency break); audit trail per payment.
**Acceptance:** spot x402 objective (TRACK1.MD:32). **Commit:** `feat(exec): bounded x402 trade-loop payments`.

---

## Phase G — Position, compliance, state  (sonnet impl, opus review)

### Task 17 — Position manager (exits survive halts)
**Files:** `position/manager.py` (monitor+exit active during entry halts; exits use fresh sell route + actual balance; failed exit stays managed + urgent alert).
**Tests:** drawdown/stale-CMC halt active → stop exit still executes (AC-017); failed sell keeps position + alerts (REQ-082).
**Acceptance:** REQ-080/080A/081/082. **Commit:** `feat(position): halt-safe exit manager`.

### Task 18 — Compliance ledger (disabled by default) + state journal
**Files:** `compliance/ledger.py` (track confirmed eligible swaps/day + 7/week; **no auto-trade**; observe-only, cannot call scanner/executor), `state/journal.py` (peak equity, daily anchor, positions, risk, compliance, journal survive restart; atomic + integrity).
**Tests:** no qualifying trade + compliance unverified → no auto trade, urgent alert (AC-014); restart preserves all state (AC-016); compliance never reinterprets location_candidate (REQ-092).
**Acceptance:** REQ-083/084, REQ-090–095, CON-004. **Commit:** `feat(compliance+state): observe-only ledger + durable journal`.

---

## Phase H — Causal replay + ZEC diagnostic  (opus — research integrity)

### Task 19 — Replay engine (shared DecisionPipeline + adapters)
**Files:** `replay/` (`ReplayConfig/Event/SimulatedFill/Report` per replay §4; HistoricalFrameAdapter as-of slicing; SimulatedExecutionAdapter; one wallet ledger). Replay imports the **same** DecisionPipeline — no recoded rules.
**Tests (golden + property):** as-of `close_time≤t` (AC-001); no decision-bar fill / next-executable (AC-002); expiry not loss (AC-003); same-bar stop-first (AC-006); unresolved marked-to-market (AC-007); single-wallet never sums per-trade (REQ-062); future bars can't alter past decision; higher costs can't improve net PnL.
**Acceptance:** replay REQ-001–066. **Commit:** `feat(replay): causal portfolio engine with live parity`.

### Task 20 — ZEC predeclared diagnostic + metrics/report
**Files:** `replay/metrics.py`, `replay/report.py` (Wilson, expectancy, profit factor, avg/median R, MAE/MFE, streak, exposure; base/stress costs; buy-hold benchmark; `<30 trades=insufficient`; lead with sample/bias/cost/parity).
**Run:** frozen window `2026-01-01→2026-06-20`, symbols `ZEC,ETH,TRX,APE` (controls frozen, no post-hoc swap), no param optimization.
**Tests:** manifest pins dates+symbols before outcomes (AC-013); `<30 trades` labels insufficient + still shows Wilson (AC-014); deterministic re-run reconciles (AC-015); survivorship shown before metrics (AC-017).
**Acceptance:** replay REQ-070–094, CON-005/006. **Commit:** `feat(replay): ZEC diagnostic + uncertainty-aware report`.

---

## Phase I — Integration gates (before any live canary)

- [ ] Full scanner suite green after Phase A (regression: supersession unchanged).
- [ ] No active code path invokes TWAK without RiskPolicy (grep + integration test).
- [ ] No position created before receipt + reconciliation (integration test).
- [ ] Paper spot cycle end-to-end on a gold token.
- [ ] Dry-run TWAK quote at zero funds.
- [ ] ZEC diagnostic runs and reports with parity_status=`live_parity`.
- [ ] Minimum-value live canary ONLY after explicit operator activation + all above green.

---

## Self-review (spec coverage)

- Spot REQ-001–003/CON-001 (doc authority/superseded): handled by GPT's doc sync (verify before live) + Step 0.
- Identity REQ-010–015 → Tasks 4–5. CMC REQ-020–024 → Tasks 10–11. Scanner/aggressive REQ-030–038 → Tasks 1–3,6. Risk REQ-040–049 → Tasks 7–9. Probe REQ-050–056 → Task 12. Execution REQ-060–070 → Tasks 13–14. Position REQ-080–084 → Task 17. Compliance REQ-090–095 → Task 18. Replay REQ-100–102 + full replay spec → Tasks 19–20. x402 → Task 16. Spot pivot/policy bypass → Task 15.
- Open dependency (resolved): campaign DOL exists in `dol.py:69` — extract independent of sweep (Task 1), not a new detector.

---

## Notes / accepted limitations (carry into every result)

- Scanner **supersession** bug and **macro-distant H12-pivot** stop are accepted (may cause skipped/under-sized trades) — no scanner internals reopened this slice.
- CMC Agent Hub skill outputs are **forward-only** (not historically reconstructable) — historical replay uses reconstructable momentum + neutral macro clamp; live snapshots captured for future combined replay.
- Live defaults stay conservative (posture B numeric limits) until causal replay produces evidence; **no profitability claim** from manual selector observations or charts (CON-005).
