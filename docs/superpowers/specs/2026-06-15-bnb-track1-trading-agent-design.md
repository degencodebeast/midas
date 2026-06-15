# Design — BNB HACK Track 1: Autonomous ICT/SMC Trading Agent

**Date:** 2026-06-15
**Status:** Design (pre-implementation)
**Hackathon:** BNB HACK — AI Trading Agent Edition, Track 1 (Autonomous Trading Agents)
**Builds on:** `trading-scanner/` (deterministic ICT/SMC scanner, Phase 4 entry model + Phase 5 alert layer, live)

---

## 1. Context & goal

We have a working, deterministic ICT/SMC scanner that grades multi-timeframe setups and emits an
immutable `Setup` (direction, entry, evidence, A/B grade). Track 1 asks for an **autonomous trading
agent** that reads market context, decides, and **executes live on BNB Chain** through the sponsor
stack (CoinMarketCap Agent Hub, Trust Wallet Agent Kit, BNB AI Agent SDK).

**Goal:** wrap the scanner in a bounded agent that trades **leveraged longs *and* shorts** on a BNB
perpetuals venue, with the scanner as the **sole source of trade signals** and the AI strictly bounded
to *filter → rank → size → execute → manage*. The agent never originates trades.

**Why this shape:** it is simultaneously "agentic" (autonomous read/decide/execute/manage loop with
on-chain identity and self-custody) and **defensible/backtestable** (the edge is deterministic and
ours, not an LLM guessing). That is the strongest Track 1 story.

**Why perps, not spot:** the validated method is symmetric — the golden set includes bearish QMLs
(USD/JPY, GBP/CAD) as well as bullish (CAD/JPY, NZD/JPY, HYPE). Spot cannot short, so a spot-only
venue amputates ~half the strategy. Perps are the honest venue for strategy fidelity.

---

## 2. Success criteria

1. **Live (or testnet) autonomous loop:** on each newly-closed candle, the agent reads context,
   evaluates the scanner's setup, decides, and — when warranted — opens/manages a real long or short
   position on a BNB perp venue, self-custody.
2. **Bounded AI:** every trade traces to a scanner `Setup`. The AI can only veto, rank, size, or
   manage — never invent an entry. This is verifiable in the decision log.
3. **Deterministic risk:** stops/exits and position sizing are deterministic and checked *before* the
   AI is consulted; the AI cannot override risk limits.
4. **Sponsor-stack integration:** CMC (context), bnbagent-sdk (on-chain identity / wallet), a BNB perp
   venue (execution), TWAK (self-custody treasury) — each used in a role it genuinely fits.
5. **Always-demoable:** a `PaperExecutor` runs the full long/short loop in sim, so the demo works even
   if live perp keys/funding aren't ready.

---

## 3. Architecture

Four layers, each independently testable, communicating through typed interfaces:

```
            ┌─────────────────────────────────────────────────────────────┐
   READ     │  CMC Agent Hub (MCP)  ──►  ContextSnapshot                   │
            │   regime / risk-on-off / liquidity / funding / ETF / news    │
            └───────────────────────────────┬─────────────────────────────┘
                                             │
   SIGNAL   ┌──────────────┐   Setup         ▼
            │ trading-     │───────────►  ┌────────────────────────────────┐
            │ scanner      │  (sole       │  DECISION LAYER (bounded AI)    │
            │ (A/B grade)  │   signal      │   gate(Setup, ContextSnapshot) │
            └──────────────┘   source)     │   → veto / allow               │
                                           │   size(risk %, equity)         │
                                           │   deterministic risk first     │
                                           │   → AgentDecision (typed)      │
                                           └───────────────┬────────────────┘
                                                           │ AgentDecision
   EXECUTE                                                 ▼
            ┌──────────────────────────────────────────────────────────────┐
            │  PerpExecutor (Protocol)                                       │
            │   quote / open_position / place_stop / place_tp / close / sync │
            │   ├─ AsterRestExecutor      (default; ccxt class exists)       │
            │   ├─ ApolloXRestExecutor    (alt REST venue)                   │
            │   ├─ PaperExecutor          (fallback; always demoable)        │
            │   └─ OnchainContractExecutor (stretch; bnbagent-sdk signs)     │
            └──────────────────────────────────────────────────────────────┘
                                                           ▲
   BNB-NATIVE   bnbagent-sdk: ERC-8004 on-chain identity + EVM wallet/signing │
               TWAK: self-custody treasury (collateral move / PnL sweep) ─────┘
```

**Decision authority inversion vs `llm-trading-bot`:** we borrow its loop *shape* (new-candle gate,
stops-first, typed decision, broker `Protocol`) but invert authority — in `llm-trading-bot` the LLM
*decides the trade*; here the **scanner decides** and the AI only gates/sizes/manages.

---

## 4. Components

### 4.1 Scanner (existing — `trading-scanner`)
- **Does:** emits an immutable `Setup` (symbol, direction, entry, stop reference = invalidation,
  evidence, A/B grade, `confirmation_kind`). Already live.
- **Interface:** `scan_symbols(...) -> list[ScanResult]` (each carries `inputs`, `result`, `entry`).
- **Change for the agent:** none to the edge. The agent consumes `ScanResult` as the signal.

### 4.2 Context layer — `CmcContextAdapter`
- **Does:** queries CMC Agent Hub (MCP) for a compact `ContextSnapshot`: market regime (risk-on /
  risk-off / neutral), liquidity, derivatives/funding context, and a coarse risk flag. Pre-computed,
  LLM-friendly — low token cost.
- **Interface:** `get_context(symbol) -> ContextSnapshot`.
- **Bound:** read-only. It shapes action on a scanner setup; it never produces a setup.
- **Auth:** CMC's own API key. (x402 billing, if applicable, is verify-then-claim — see §8.)
- **Degradation:** if CMC is unreachable, `ContextSnapshot.status = "unavailable"` → the gate treats
  it as "no veto, no boost" (the scanner setup stands on its own grade). Never blocks the loop.

### 4.3 Decision layer — `gate` + `size` → `AgentDecision`
- **`gate(setup, context) -> GateVerdict`:** deterministic rules over the setup grade + context.
  Examples: A-grade always allowed; B-grade allowed only if context not risk-off against the trade
  direction; veto if context risk flag is high. The verdict carries a `size_multiplier` (e.g. 1.0
  aligned, 0.5 mixed, 0.0 veto).
- **`size(equity, risk_pct, entry, stop) -> qty`:** deterministic risk-based sizing —
  position size from fixed fractional risk (e.g. 0.5–1% equity) and the entry↔stop distance. Never a
  free LLM number.
- **AI role — bounded to a code-clamped safe band (NOT a control path).** `gate`/`size` are the
  **hard floor** and run in code. An optional LLM advisor may only, *within* what the deterministic
  layer already permits: (1) **rank** setups the gate already allowed when several exist — a
  *multi-symbol* concern, **out of v1 scope** (the single-symbol v1 loop has one setup, so ranking is
  inert; see §11); (2) recommend a **size *reduction* only** — a `size_factor ∈ [0, 1]` multiplied onto the
  deterministic qty (it can never increase it); (3) suggest **"wait"** (defer this candle); (4) write
  the **reasoning** string. It may **never** originate a setup, re-grade, flip direction, change
  entry/stops, increase size, un-veto, or loosen a cap. A deterministic **`clamp_advice(baseline,
  advice)`** validates and clamps every LLM output (mirrors `llm-trading-bot`'s `_validate_decision`);
  an out-of-band recommendation is rejected, not executed. **Worst-case LLM failure is therefore a
  missed or smaller trade — never a bad one.** The AI is real and visible (judge-relevant) yet cannot
  break the deterministic edge.
- **System prompt = operating doctrine, NOT strategy.** It encodes what the LLM may do
  (rank / size-down / wait / explain), what it must never do, and how to use the tools (scanner =
  signal truth, CMC = context, executor = orders only, treasury = funds only). It does **not** restate
  the ICT/SMC method — that lives in the scanner; restating it would create a second competing brain
  (drift, "which brain was right?").
- **Output — typed `AgentDecision`** (frozen dataclass; mirrors `llm-trading-bot`'s `LLMDecision`):
  `action`, `intent` (symbol/qty/entry/stop_loss/take_profit/leverage), `gate` (verdict), `setup_ref`,
  `reasoning`. The log additionally records the **deterministic baseline qty + the LLM's pre-clamp
  recommendation + the clamped final** (see §5/§6) so "did the AI help?" is answerable from data.

### 4.4 Execution layer — `PerpExecutor` (Protocol) + implementations
- **Interface (one, shared):** `quote(intent)`, `open_position(intent)`, `place_stop(intent)`,
  `place_take_profit(intent)`, `close_position(position_id)`, `sync_positions()`.
- **Implementations (separate — venues are *not* interchangeable):**
  - **`AsterRestExecutor`** (default) — Aster perps via its signed REST API. ccxt ships an Aster class
    (REST order placement supported; only some websocket `watch*` methods missing — irrelevant, we
    poll). Long+short via `positionSide` hedge mode.
  - **`ApolloXRestExecutor`** — ApolloX perps, Binance-fapi-style HMAC REST.
  - **`PaperExecutor`** — in-memory sim (the `BacktraderBroker` analogue). The always-demoable
    fallback; preserves the full long/short loop with no live dependency.
  - **`OnchainContractExecutor`** (stretch) — direct perp contract calls (ApolloX documents
    `openMarketTrade`/`createLimitOrder`/`closeTrade`), signed/sent via bnbagent-sdk's
    `ContractClientMixin` + `EVMWalletProvider`. Highest self-custody purity, highest build risk.
- **Auth is venue-specific (verify per venue):** ApolloX-style = HMAC API key (wallet *not* in order
  path). Aster v3 references `signer`/`privateKey`/`AGENT` → possibly **wallet-signed** orders (wallet
  *is* in the path — a self-custody *and* third-pillar win; confirm in the spike, §8).

### 4.5 Runner / loop (borrowed shape from `llm-trading-bot`)
- **New-closed-candle gate:** poll OHLCV, drop the forming bar (`ohlcv[:-1]`), fire once per newly
  closed candle (`latest_ts <= last_ts` dedupe). This *is* our non-repainting discipline.
- **Stops-first ordering:** on each candle → (1) check SL/TP and close if hit (deterministic, before
  anything else); (2) skip if an order is pending; (3) build position+account; (4) scan → setup;
  (5) `gate`/`size` → decision; (6) execute; (7) log.
- **Resilience:** errors sleep+retry; the loop never dies on a single venue/API hiccup.

### 4.6 BNB-native layer
- **bnbagent-sdk:** register the agent on-chain via **ERC-8004** (agentId NFT, gas-free on testnet) —
  a verifiable "BNB-native agent" credential for judges; provides `EVMWalletProvider`/signing reused
  by `OnchainContractExecutor` (and possibly Aster v3 signing).
- **TWAK:** **self-custody treasury** — the agent's funds live in a TWAK agent-wallet; `swap`/`transfer`
  move USDT collateral to the venue and sweep PnL back. Genuine, demoable, honest role. **Not** the
  perp executor (perps are not among TWAK's listed capabilities; native perp support is unverified).

### 4.7 UI / Observability — read-only mission-control dashboard (v1 submission scope)
A **read-only** judge-facing console, modelled on aegis's operator studio (`engine/studio/*`): a thin
**FastAPI** server exposes a **REST status snapshot** (`/api/status`, polled ~5s) + a **REST decision
feed** (`/api/decisions?take=N`, also polled), and a **Next.js/React + Tailwind** read-only page renders
it. (**v1 polls**; a live **`/ws/decisions`** WebSocket stream with ring-buffer backfill, mirroring
aegis `engine/studio/ws/signals.mjs`, is a documented **later enhancement** — deferred, not dropped.) **Next.js + FastAPI is the locked default;
Streamlit is the fallback** (Python-native, reads the JSONL log directly) used **only if** the core
loop isn't green in time — not an open coin-flip at build time. Panels:
- **Status:** mode (paper/testnet/live), venue, wallet/treasury + ERC-8004 identity badges, kill-switch.
- **Performance:** equity curve, realized/open PnL, win-rate, avg R, drawdown.
- **Positions:** current/open positions + risk used.
- **Decision feed (centrepiece):** each row = scanner grade+regime → CMC context → deterministic
  baseline size → LLM recommendation → clamped final → outcome → reasoning. Reads §4.3/§5's JSONL log;
  visually *proves* the bounded-AI clamp.
- **Policy/risk:** caps, daily-loss state, why the next trade is allowed/blocked.
Strictly read-only (never places/changes trades — that would undercut autonomy); a pure read layer over
artifacts the core already produces. Built **last**, after the core loop is green.

---

## 5. Data flow (one cycle)

1. New H1/15m candle closes → runner wakes.
2. Stops-first: if open position hit SL/TP → `close_position` → log → done.
3. Scanner grades the symbol → `ScanResult` (+ `Setup` when a real entry exists).
4. `CmcContextAdapter.get_context(symbol)` → `ContextSnapshot` (or `unavailable`).
5. `gate(setup, context)` → allow/veto + `size_multiplier`.
6. If allowed: `size(...)` → qty; assemble `AgentDecision`.
7. `PerpExecutor.open_position(intent)` + `place_stop` + `place_take_profit`.
8. Append a decision record to a JSONL log (reuses the Phase-5 alert-log substrate) for
   backtest/audit — recording **scanner setup, context snapshot, deterministic baseline qty, the
   LLM's pre-clamp recommendation, the clamped final decision, and outcome** — so the AI's marginal
   effect (baseline vs final) is measurable later.

---

## 6. Risk & safety guardrails (deterministic, non-overridable by AI)

- Fixed-fractional risk per trade (config; default 0.5–1% equity).
- Hard caps: max leverage, max concurrent positions, max daily loss → **kill-switch** halts new entries.
- Every entry requires a stop (from the setup's invalidation level); no stopless entries.
- Idempotent orders (client order IDs) so a retry never double-fills.
- Testnet/paper first; tiny real-funds size for any live demo.
- **Fail-closed policy engine — the only path to funds** (`run_policies(intent, state, config) ->
  PolicyResult`, modelled on aegis `engine/policies/engine.mjs`). One gate, **AND semantics** (every
  policy must pass; first denial short-circuits), and it **throws on empty config** (no policies = no
  trade, never a silent allow). Policies: max-notional/size cap, max-leverage, **daily-loss
  kill-switch**, max-concurrent (one position), **stop-required**, cooldown. The runner calls it
  unconditionally before any `open_position`; a denial is logged, never forced. A **no-bypass test**
  proves empty config raises and a denied intent never reaches the executor. The LLM has no direct path
  to funds.
- **`judge-trace` CLI** (modelled on aegis `scripts/judge-trace.mjs`): one command, zero funds/network,
  prints a single-screen PASS/DENY proof of the policy engine on canned intents (one passing, one
  cap-breaching) — the deterministic "prove the safety story in 10 seconds" demo artifact.
- **Log every LLM influence** (scanner setup, context, deterministic baseline size, LLM
  recommendation, clamped final) so any divergence between baseline and final is auditable and the
  AI's contribution is backtestable — otherwise you can't tell whether the AI helped.

---

## 7. Sponsor-pillar mapping (honest: genuine vs garnish)

| Pillar | Role | Genuineness |
|---|---|---|
| **CMC Agent Hub** | Context gate (regime/risk/liquidity/funding) | Genuine — shapes every decision |
| **bnbagent-sdk** | ERC-8004 on-chain identity; wallet/signing (Option C / Aster-v3) | Genuine — identity always; execution if signed path |
| **Perp venue (Aster/ApolloX)** | Actual perp execution | Genuine — the trade |
| **TWAK** | Self-custody treasury (collateral move / PnL sweep) | Genuine but supporting — *not* the executor |
| **x402** | Pay-per-call billing (CMC?) / our paid signal service (ERC-8183) | **Garnish until verified** — not load-bearing |

---

## 8. Open facts → the venue spike (implementation task 0)

These are **facts to verify**, not decisions to debate. A 2–4h spike must answer, per candidate venue:
1. **Auth flow:** HMAC API key vs wallet-signed (`signer`/`privateKey`/`AGENT`)? Is the wallet in the
   order path?
2. **Testnet/sandbox** availability for perps (decides testnet-first vs paper+tiny-live).
3. **Leverage / margin / `positionSide`** params for symmetric long+short.
4. **ccxt fit:** does the current ccxt class place orders correctly (Aster), or do we need a thin
   Binance-subclass / signed client (ApolloX)?
5. **x402 / CMC billing** mechanism — confirm before claiming it.

Spike output picks the default venue + executor shape and finalizes the effort estimate.

---

## 9. Build sequencing (6-day clock)

0. **Venue spike** (§8) — de-risk the riskiest integration first.
1. `PaperExecutor` + `PerpExecutor` interface + runner loop (full long/short in sim, end-to-end green).
2. `CmcContextAdapter` + `gate`/`size` + typed `AgentDecision` + `clamp_advice` + decision log.
3. **Policy engine (`run_policies`) + runner integration + `judge-trace` CLI** — cheap; the safety
   spine + the demo proof. (Done early because it's load-bearing for trust and trivial to wire.)
4. `AsterRestExecutor` (or ApolloX) behind the same interface; testnet orders.
5. bnbagent-sdk ERC-8004 identity registration; TWAK treasury wiring.
6. **FastAPI read API (`/api/status` + `/api/decisions`, polled) + Next.js read-only dashboard** (Streamlit
   fallback) — built after the core loop is green.
7. Tiny real-funds live demo; polish the judge narrative.
8. (Stretch) `OnchainContractExecutor` via bnbagent-sdk.

---

## 10. Testing strategy

- **TDD throughout** (project rule): red→green per component — for all logic, including the dashboard's
  **API (Task 17, TDD'd with `TestClient`)**. The one carve-out: the read-only dashboard **frontend**
  (Task 18, presentational Next.js) is **exempt from unit-TDD** (rendering, not logic); it's verified by
  a manual smoke against a paper-run log.
- `PaperExecutor` makes the entire loop testable with **no network** — inject it everywhere the live
  executor would go.
- Decision-layer tests: gate veto/allow/size-multiplier per (grade × context); sizing math; AI cannot
  exceed risk caps.
- Runner tests: new-candle gate (no double-fire), stops-first ordering, kill-switch halts entries.
- Reuse the parity/golden discipline from the scanner for any new deterministic logic.

---

## 11. Out of scope (YAGNI)

- LLM *originating* trades (breaks the thesis).
- **Multi-symbol ranking across simultaneous setups** — v1 is single-symbol, so the LLM's "rank"
  ability is inert; a multi-symbol scan + ranking pass is a post-v1 stretch.
- Multi-venue arbitrage / routing.
- A *full* product app / rich PWA. The v1 **read-only mission-control dashboard IS in scope** (§4.7);
  only the full Phase-6 app is deferred. Interactive / human-trading controls are excluded (they'd
  undercut autonomy).
- Full backtest harness for the agent layer (the JSONL decision log is the substrate; backtest later).

---

## 12. Decisions log (forks resolved + reasons)

1. **Approach A (deterministic-edge, bounded AI), not LLM-originated trades** — defensible + agentic.
2. **Perps over spot** — strategy is symmetric; spot can't short; golden set has bearish QMLs.
3. **TWAK is treasury, not executor** — perps aren't a TWAK capability; HMAC/wallet auth bypasses or
   uses the wallet directly, neither of which is TWAK's swap primitive.
4. **One `PerpExecutor` interface, N separate impls** — venues share concepts but not auth/margin;
   "one adapter, swap base URL" is false.
5. **Paper-sim fallback, not spot fallback** — preserves long/short; demo never depends on live.
6. **Option B (REST) now, Option C (on-chain) stretch** — REST ships on the clock; contract/margin/
   liquidation plumbing is where a 6-day build dies.
7. **Aster default** — dedicated ccxt class (REST trading) + strongest BNB-ecosystem narrative;
   ApolloX is the Option-C contract venue (documents `openMarketTrade`).
8. **x402 demoted to bonus** — verify CMC/TWAK auth before wiring; auth is per-service.
9. **AI clamp (size-down / wait only, code-enforced)** — worst-case LLM failure is a missed/smaller
   trade, never a bad one; LLM cannot un-veto, increase size, flip, or re-grade.
10. **Gate on scanner rating *family*** (`A++…A-`, `B+…B-`), not exact `{"A","B"}` — engine.py emits the
    full family; exact-match would wrongly veto your best grades.
11. **Dashboard in v1 (read-only mission-control); Next.js + FastAPI is the locked default, Streamlit
    is fallback-only under time pressure** — judge legibility is product proof, not cosmetic.
12. **Explicit fail-closed policy engine (`run_policies`) + no-bypass test** (from aegis) — one
    auditable gate, not scattered checks; throws on empty config; runner is the only execution path.
13. **`judge-trace` CLI** (from aegis) — deterministic, zero-funds, single-screen safety proof; the
    highest-ROI demo artifact surfaced across all reference repos.
14. **Track 1 first, by deliberate choice; Track 2 deferred, not dropped.** `hackathon-agent-layer.md`
    framed a Track-2 anchor (CMC strategy-skill + `SKILL.md` packaging + backtest report) with Track 1
    as a stretch; we invert to **Track-1-first** because it's the bigger prize and splitting focus on a
    6-day clock dilutes the execution-critical path. Track 2 stays **cheaply recoverable** later — it
    reuses the same deterministic scanner core + the CMC adapter we're already building, and the JSONL
    decision log is exactly the backtest substrate a Track-2 report needs. This is a prioritization, not
    an omission.
