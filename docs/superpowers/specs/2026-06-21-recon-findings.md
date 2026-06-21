# Integration-Surface Reconnaissance — Track-1 Spot Agent (2026-06-21)

> **Purpose.** This is the *grounding artifact* GPT's review correctly demanded: confirm the
> real interfaces (file:line, command schemas, return shapes) **before** finalizing the
> implementation plan, so TDD tasks are written against actual signatures rather than
> guesses. Produced by three parallel read-only recon streams over `midas`,
> `trading-scanner`, and `bnbagent-sdk` / `binance-skills-hub`.
>
> **Headline:** recon caught a wrong-wallet-kit assumption (the surface is **TWAK**, not
> `baw`) and *provisionally retained* the spec's tx-hash model — see §0. **Open epistemic gap:** the exact
> TWAK `--json` payload is corroborated by two reference repos but **not yet captured from the
> real CLI** — a version-pinned credentialed canary is a plan prerequisite (§0 caveat).
>
> **Evidence tiers (read findings through this lens):** ✅ *confirmed code fact* (read in this
> tree) · ⊚ *sound inference* (architecture/derivation) · ⚠️ *unverified external behavior*
> (depends on an uninstalled CLI or an undecided design choice — must be closed before the
> dependent task's tests are frozen).

---

## 0. THE EXECUTOR SURFACE — TWAK CLI (spec's tx-hash model held PROVISIONALLY, canary-gated)

> **Correction history (read this).** An earlier draft of this doc concluded the spec's
> tx-hash model was *wrong* and swaps used an `orderId` model. That was based on the **`baw`
> CLI (`@binance/agentic-wallet` — *Binance* Agentic Wallet)** found in `bnbagent-sdk` /
> `binance-skills-hub`. **That is a different wallet kit.** The competition's "Best Use of
> TWAK" bonus and both of the user's reference repos (`boomerang-ai`, `trading-agent`/Binacci)
> use **TWAK = Trust Wallet Agent Kit** (the `twak` CLI, v0.17.x). The TWAK surface returns a
> **tx_hash**, so the spec's REQ-060–070 tx-hash + receipt model **holds** — it just must be
> grounded on `twak swap` + an `eth_getTransactionReceipt` RPC confirm.

**The real TWAK CLI surface** (verbatim from `boomerang-ai/boomerang/vault/twak_executor.py:7-14`
and `cool_projects/trading-agent/agent/src/binacci/venues.py:10,159-180`):

```
twak wallet create    --password <pw> [--no-keychain] --json
twak wallet address   --chain bsc --json
twak wallet portfolio --chains bsc --password <pw> --json
twak wallet balance   --chain bsc --json
twak wallet status                                   # Binacci uses this
twak auth status                                     # Binacci: configured? (HMAC mode)
twak swap <from> <to> --usd <amt> --chain bsc --slippage <pct> [--quote-only] [--private] --password <pw> --json
twak transfer --to <addr> --amount <n> --token <assetId> --confirm-to <addr> --max-usd <n> --password <pw> --json
twak compete register --password <pw> --json         # Track-1 registration — a built-in command
twak compete status   --password <pw> --json
twak perps open|close|positions|mark …               # perps — NOT used by our spot agent
```

| Concern | Behavior — ⊚ corroborated by 2 reference wrappers, ⚠️ NOT captured from the real CLI (provisional until canary) |
|---|---|
| Swap submission | `twak swap <from> <to> --usd <amt> --chain bsc --slippage <pct> --password <pw> --json` → JSON carrying a **tx_hash** (boomerang `_extract_tx_hash`, `twak_executor.py:155,169`). `--quote-only` returns a price with no broadcast (used as the liquidity/price probe). `--private` = MEV-protected relay. |
| Confirmation | **Submit → get tx_hash → poll `eth_getTransactionReceipt`** via `confirm_receipt_via_rpc(tx_hash, rpc_url, timeout_s)` (Binacci `venues.py:48-81`) → `confirmed` (status `0x1`) / `reverted` (`0x0`) / **`unconfirmed`** (timeout). Stdlib `urllib` JSON-RPC — no web3 dep needed. |
| BROADCAST_UNKNOWN | Is the **`unconfirmed`** return from the receipt poll — a real, handled state (unlike boomerang, which is fire-and-forget). On restart, reconcile by on-chain balance (boomerang `_reconcile_positions`) and/or re-poll the stored tx_hash. |
| Self-custody | Private key stays in the **local `~/.twak` keystore**; signed locally per swap (`twak_executor.py:3-5`). Satisfies the Track-1 self-custody requirement and the "Best Use of TWAK" objective natively. |
| Auth modes | Two: **password/keystore** (boomerang: `--password`, `~/.twak`) and **HMAC env** (Binacci: `twak init` from `TWAK_ACCESS_ID`/`TWAK_HMAC_SECRET`, `twak auth status`). Pick one; password-redaction in logs is mandatory (`twak_executor.py:49-66`). |
| Approval-vs-swap | *Provisionally* not a concern — `twak swap` appears to be one self-contained signed action returning a swap tx_hash (per both wrappers); the canary must confirm no separate approval step / exit code is surfaced. |

**Action:** keep REQ-060–070's intent (positive proof of a settled swap; UNKNOWN halts new
exposure; restart reconciles not retries) and the tx-hash mechanism. Ground the executor on
`twak swap` (submit) + `confirm_receipt_via_rpc` (Binacci) + on-chain-balance restart
reconciliation (boomerang). The `baw` orderId model is **NOT** used.

> ⚠️ **Unverified — must close before freezing executor tests:**
> 1. **TWAK JSON schema is NOT captured from the real CLI.** Neither `twak` nor `baw` is
>    installed in this workspace. "`twak swap --json` returns a `tx_hash`" is corroborated by
>    *two* reference wrappers (boomerang `twak_executor.py:155,169`, Binacci `venues.py`) but
>    boomerang explicitly flags that its success-field extraction *"requires credentialed
>    integration testing"* (`twak_executor.py:16`) — i.e. both repos assume tolerant keys
>    against an **un-pinned** schema. **Prerequisite:** a version-pinned, credentialed
>    **canary capture** of the real `twak swap`/`compete register`/`wallet balance` `--json`
>    payloads (record exact keys, tx_hash field name, error shape, exit codes) before the
>    executor's parsing tests are written. Until then, treat the schema as *probable*, and make
>    the executor **validate** rather than assume.
> 2. **MIDAS needs a STRICT subprocess runner — do NOT mirror the reference wrappers.** Both
>    boomerang (`twak_executor.py:78`) and Binacci (`venues.py:138`) **ignore `proc.returncode`**
>    and lean on tolerant JSON recovery (`json.loads(out[out.index("{"):])`). MIDAS's runner
>    must check `returncode == 0` **and** validate the parsed JSON shape, failing closed on
>    either — inheriting these templates verbatim would import the bug.

---

## 1. Scanner (`trading-scanner/src/magic_scanner/`) — smaller, but NOT just "surface 2 fields"

The two values MIDAS fabricates are computed *on the post-sweep lifecycle path* and then
discarded; the aggressive entry geometry already exists behind an unplumbed flag. **But** that
computation sits **behind the H12 coherent-sweep gate** (`entry.py:510`), while your active
rules permit a **sweep-OPTIONAL** lower-grade trade when a concrete campaign DOL exists (per
`agent-trading-rules.md` — a missing sweep *reduces the checklist grade*, it does not forbid
the trade). So the work is *surface the existing fields* **+ re-resolve DOL and stop
*independent of the sweep gate*** for the aggressive/sweep-optional setups — the original Task-1 ("extract
campaign-DOL independent of sweep") is **still required**, not eliminated.

### 1a. Campaign-DOL target — already computed
- `detectors/dol.py:69-83` — `DolTarget` = `{level: float, source: str}` (price + provenance:
  `equal_pool|prior_day|prior_week|range_opposite`).
- `detectors/dol.py:231-239` — `dol_targets(df, direction, *, left=15, right=10, equal_tol=…, ref_price=None) -> list[DolTarget]` (target prices at `dol.py:288-307`).
- The **dominant** target is already picked inside `classify_qml_lifecycle`
  (`detectors/entry.py:543-560`) and exposed on the lifecycle dict as
  `dominant_dol_level` / `dominant_dol_source` (`entry.py:570-572`) — **but never put on
  `EntrySetup` or `ScanResult`.**
- ⚠️ **Gated behind the sweep.** This DOL pick is reached **only after** the coherent-sweep
  gate at `entry.py:510-521` (which early-returns `_evidence_gap("no_coherent_sweep")`). So for
  a **sweep-optional aggressive setup** the DOL is *not resolved at all* — "surface the existing
  field" yields `None` exactly where you need it. The export path must call `dol_targets(...)`
  + dominant-pick **independent of the sweep gate** (the original Task-1 extraction).

### 1b. Structural stop — already computed
- `detectors/risk.py:27-38` — `resolve_structural_stop(df_h12, *, direction, entry,
  qml_level=None, left=15, right=10, buffer_frac=0.05, require_reclaim=True,
  allow_pivot_fallback=True) -> StopPlan | None`.
- `risk.py:17-25` — `StopPlan = {level, anchor, source ("h12_sweep_extreme"|"h12_pivot"), anchor_bar}`. Consumer wants `.level`.
- Called only as a **gate** at `inputs.py:325` and `entry.py:523`, then **discarded**.
- ⚠️ **Canonical fallback may be disabled on the lifecycle call.** The hierarchy is
  *sweep-extreme first, then major H12 pivot* (`risk.py:79-103`, behind `allow_pivot_fallback`).
  The lifecycle call at `entry.py:523` may not enable the pivot fallback, so a sweep-optional
  setup could resolve **no stop**. The export must invoke `resolve_structural_stop(...,
  allow_pivot_fallback=True)` itself and export **full provenance** (`level, anchor, source,
  anchor_bar`) — one as-of evaluation, not a re-derived proxy.

### 1c. The export gap — confirmed
- `scan.py:56-88` — `ScanResult{symbol, inputs, result, confirmation_kind="none", entry: EntrySetup|None}`. **No stop field, no DOL field.**
- `EntrySetup` fields (`entry.py:162-172`): `entry, entry_type, confirmation_kind, chained,
  mss, both, first, qml_key_level, qml_reclaim_time, confirmation_time, chained_scob` — **no
  stop, no DOL.**
- `inputs.draw_on_liquidity` is a **bool** (`inputs.py:369`), not a price.
- **Add the FULL export record per spot REQ-039** — not "≈2 fields". One as-of evaluation
  must export: `entry`; the **stop** as `{level, source, anchor, anchor_bar}` (the whole
  `StopPlan`); the **campaign DOL** as `{level, source}` (the `DolTarget`); and the
  **checklist DOL** (the RR-qualified `draw_on_liquidity` draw, distinct from campaign DOL —
  `entry.py:457-459`). Populate in `scan_pair` (it already holds `h12_df` at `scan.py:135`,
  direction, `entry_report.qml_key_level`) via the §1f-§0 independent resolver. Stamp all on
  `ScanResult` (a nested `stop`/`dol` record, not loose floats) so MIDAS consumes provenance,
  never a re-derived proxy.

### 1d. M15 dependence spread — three hard gates (matches GPT's flag)
- `scan.py:131-150` — `scan_pair` requires non-empty `ltf_df`; else `entry_report=None`.
- `inputs.py:286-301` — assembly auto-derive requires non-empty `ltf_for_entry`; else
  `entry=None`/`entry_type="—"`, which cascades into the #6 DOL gate (`inputs.py:318`).
- `detectors/entry.py:700-909` — `df_15m` is a **required positional**; `detect_chained_scob`
  (`entry.py:846`) and `detect_mss` (`entry.py:849`) index into it.
- **NOTE / correction:** the `entry.py:510` early-return is an **H12** sweep gate, *not* M15;
  the lifecycle already tolerates `no_coherent_sweep` (`entry.py:827-831`).

### 1e. Aggressive geometry — already present, just unreachable
- `entry.py:900-901` — Aggressive branch: `entry = qml.key_level`, `entry_type="Aggressive"`,
  `confirmation_kind="none"`.
- `entry.py:893-894` — `prefer_aggressive=True` short-circuits straight to it.
- **But** `prefer_aggressive` exists **only in `entry.py`** — not plumbed through
  `inputs.py`/`scan.py`, and the function still indexes `df_15m` before reaching the branch.

### 1f. Scanner change-scope (limited to: export stop, export DOL, aggressive-without-M15)
0. **Extract DOL + stop resolution *independent of the sweep gate*** (the load-bearing one —
   §1a/§1b ⚠️): a canonical resolver that, given direction + H12 frame + QML key level, returns
   `(stop: StopPlan, dol: DolTarget)` with `allow_pivot_fallback=True`, reachable even when
   `classify_qml_lifecycle` would early-return on `no_coherent_sweep`. Both are exported with
   full provenance from **one as-of evaluation**.
1. `scan.py` `ScanResult` (+2 fields, with provenance) and `scan_pair` (compute via §0 + stamp).
2. `entry.py` `derive_entry_report`/`derive_entry_setup`: make `df_15m` optional/empty-tolerant
   → when absent, skip confirmation detectors and fall through to the Aggressive branch.
3. **Thread `prefer_aggressive=True` end-to-end — MANDATORY, not optional.** The hackathon agent
   uses Direct-QML aggressive entry *always*. When M15 data **is** present, the detector
   otherwise runs SCOB/MSS (`entry.py:846-856`) and returns a *Confirmation* entry instead of
   Direct QML — so `prefer_aggressive` (today unreachable outside `entry.py`) must be plumbed
   through `assemble_inputs`/`scan_pair` and set `True`, forcing the Aggressive branch
   (`entry.py:893-894`) regardless of M15 presence. Also relax the `len(ltf)` guards
   (`inputs.py:286-301`, `scan.py:139`) so H1+QML alone fires when M15 is absent.
- **Out of scope (frozen):** QML supersession, H4, D1.

---

## 2. MIDAS (`midas/src/magic_agent/`) — holes confirmed, boundaries scoped

### 2a. No `DecisionPipeline`; the de-facto core is `runner.on_candle`
- **No class named `DecisionPipeline`** anywhere in `src/` (grep-confirmed).
- `runner.on_candle` (`runner.py:33`) is the per-candle orchestrator, called by
  `live.run_live` (`live.py:107`). The reusable decision core is **`runner.py:70-115`**
  (signal→context→`build_decision`→advisor-clamp→`run_policies`→`open_position`).
- **Insertion point (corrected):** the shared core must cover **both entry and exit
  decisions** — extract **`runner.py:52-115`** (the stops/hold *decision* at `:52-68` **and**
  the signal→decision→policy block at `:70-115`) into `DecisionPipeline`. Extracting only
  `:70-115` leaves stop/exit logic outside the shared path, so live/paper/replay could **diverge
  on exits** — defeating the parity goal. The split is **decision (entry + exit) inside the
  pipeline** vs **execution side-effects behind injected adapters** (the Binacci hook pattern,
  §5f): `decide(...)` returns an intent/exit decision; the executor adapter performs it.
- ⚠️ **Extraction is necessary but NOT sufficient.** `runner.py:52-115` only implements the
  *old static stop/target* exit. The shared pipeline must additionally **build exit behaviors
  the current code lacks**: **campaign-DOL completion** (close at the move-completion target the
  scanner now exports, §1c), **opposing-HTF invalidation** (exit when HTF bias flips against the
  position), and **RiskPolicy-directed reduction** (act on a reduce signal). So the
  DecisionPipeline task = *extract `52-115`* **+ implement these three new exit-lifecycle
  behaviors** — not extraction alone.
- **Replay is ABSENT** — only live+paper exist, both via `run_live` (a `--executor paper`
  run still goes through `run_live`, `cli.py:356`). "Shared core for live/paper/replay" =
  *extract the pipeline* **and** *build a replay driver*.

### 2b. Fabricated stop + policy bypass
- `setup_view.py:33-38` — stop synthesized as `qml * (1 ∓ stop_buffer_pct)` (default `0.005`,
  `setup_view.py:21`); TP = `min_rr × risk`. **Must consume the scanner's `stop_price`
  (§1b) instead.**
- `runner.py:96-110` — policy gate is `if policy_config is not None`; default is `None`
  (`runner.py:43`), so a direct `on_candle(...)` with no config **opens positions with zero
  RiskPolicy**. `run_live` always passes a config (`live.py:35,114`; `cli.py:233-239`), but
  the bypass is reachable on any non-`run_live` path (e.g. the future replay driver).
  **Harden:** fail-closed regardless of caller.

### 2c. Registration ≠ Identity (do not conflate — both confirmed)
- **(a) Track-1 competition registration: ABSENT in MIDAS code** — no `compete` subcommand;
  CLI is only `run`/`serve`/`judge-trace` (`cli.py:30,61,64`). **But it is NOT "build a
  contract call from scratch"** — registration is the built-in TWAK command
  **`twak compete register --password <pw> --json`** (+ `twak compete status`), used by both
  reference repos (boomerang `twak_executor.py:13-14`, Binacci `venues.py:175-180`). MIDAS
  just needs a thin subprocess wrapper + a `register`/`status` CLI subcommand. No external
  contract/ABI sourcing required.
- **(b) ERC-8004 identity: EXISTS but unwired.** `Erc8004Identity` (`identity.py:13`),
  `register() -> int` (`identity.py:20`). CLI resolver `_resolve_agent_id` (`cli.py:266`)
  **raises `NotImplementedError`** at `cli.py:284-288`; `agent_id` is always `None` today.
  Backed by `bnbagent.ERC8004Agent` (see §3b).

### 2d. Perp-shaped surfaces (must change for spot-long)
- Whole module `executors/aster.py` (leverage/positionSide/reduceOnly/contracts).
- `cli.py:310-325` (ccxt.aster wiring, `--executor aster`), `--leverage`/`--max-leverage`
  (`cli.py:36,41`).
- `leverage` threads `models.py:82` → `decision.py:78,90` → `runner.py:40,78` →
  `live.py:37,113` → `policy.py:20,64-65`. For spot it stays `1.0` / dropped.
- Short side: `models.py:17,23`; `decision.py:36-37,87`; `setup_view.py:15,36-38`;
  `executor.py:60`; `aster.py:29,45,50,60,64`; `status.py:73` (becomes dead code).
- `treasury.py` moves "USDT collateral to perp venue" → self-custody spot wallet.
- "funding" — ABSENT (no funding-rate handling exists).

### 2e. Migration boundaries
- **Reuse as-is:** `models.py` (leverage stays 1.0), `policy.py` (drop `max_leverage`),
  `decision.py` long-only math, `log.py`, `status_store.py`, `api.py`, `context.py`,
  `advisor.py`, `judge_trace.py`; `status.py` (short branch becomes dead).
- **Rewrite:** `executors/aster.py` → spot executor (§3a); `setup_view.py` → consume scanner
  stop; `treasury.py` → self-custody spot.
- **Harden:** `runner.py` policy bypass (§2b).
- **Build new:** CMC selector (multi-symbol picker — today one hardcoded `--symbol`,
  `cli.py:31`); spot executor; shared `DecisionPipeline`; Track-1 registration; replay driver.

---

## 3. Integration surface (`bnbagent-sdk` + `binance-skills-hub`)

> **Scope note.** The **swap + competition-registration** surface is **TWAK** (`twak` CLI) —
> see §0. This §3 documents the *separate* Binance/`bnbagent-sdk` stack: **§3a `baw`** is a
> *different* wallet kit (Binance Agentic Wallet) that MIDAS does **NOT** use for swaps — kept
> here only to record it was evaluated and rejected in favor of TWAK. **§3b** records the
> `bnbagent-sdk` identity/commerce surface plus an observed x402 signer for reference. MIDAS uses
> the SDK for ERC-8004 identity only; TWAK remains the sole swap and x402 payment authority.

### 3a. The `baw` npm CLI — a DIFFERENT wallet kit, NOT used for swaps (rejected)
- Skill: `binance-agentic-wallet` (in `binance-skills-hub`); CLI `@binance/agentic-wallet`,
  required v`1.1.1`; `--binanceChainId 56` (BSC mainnet), tokens selected by **contract
  address** (`--fromToken`/`--toToken`) — consistent with our resolved addresses
  (e.g. APE `0x8f86…`).
- **Quote:** `baw market-order quote --fromTokenQty Q --fromToken A --toToken B
  --binanceChainId 56 [--slippage auto|0-100] --json` →
  `{"success":true,"data":{"fromCoinSymbol,fromCoinAmount,toCoinSymbol,toCoinAmount,slippage"}}`.
- **Swap:** `baw market-order swap … [--mev true|false] [--gasLevel LOW|MEDIUM|HIGH] --json` →
  `{"success":true,"data":{"orderId":"…"}}` (**submitted, not executed**).
- **Poll:** `baw market-order list --orderId <id> --json` → entry with status
  `PENDING|FINISHED|FAILED` + `txHash, fromTokenQty, toTokenQty, slippage, bookTime, updatedTime`.
- **Lock gate:** `baw wallet tx-lock --binanceChainId 56 --json` → `UNLOCKED|LOCKED`
  (LOCKED = pending tx or a double-confirm waiting in the Binance App).
- **Balance:** `baw wallet balance --json`.
- **Exit codes:** `0` success · `1` upstream/usage (body carries business `code`) · `3`
  network failure. Business codes: `000000` OK · `100004` rate-limited · `100002` bad param ·
  `000400` token/chain unsupported.

### 3b. bnbagent-sdk (Python) = identity + ERC-8183; local x402 signer is reference-only
- **ERC-8004 identity:** `ERC8004Agent.register_agent(agent_uri, metadata=None)`
  (`erc8004/agent.py:277-385`) → `{success, transactionHash, agentId:int, receipt, agentURI}`
  (two-step: `register_agent` then `set_agent_uri`). Identity registry addresses
  (`config.py:57-79`, `networks/addresses.py:49-67`): BSC testnet
  `0x8004A818BFB912233c491871b3d84c89A494BD9e` (chain 97), mainnet
  `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` (chain 56). Testnet gas-free via paymaster
  `https://bsc-megafuel-testnet.nodereal.io`.
- ✅ **x402 authority resolved from official documentation (2026-06-21).** TWAK is the sole
  MIDAS payment signer. Trust Wallet documents native `twak x402 quote` and
  `twak x402 request`; the latter signs EIP-3009 (preferred) or Permit2 payment authorization
  and retries the HTTPS request with `PAYMENT-SIGNATURE`. CoinMarketCap documents its x402
  endpoints as generic EVM-signer endpoints charging 0.01 USDC on Base, so TWAK is a compatible
  signer and no SDK-side fallback is required. Sources:
  `https://developer.trustwallet.com/developer/agent-sdk/cli-reference.md` and
  `https://coinmarketcap.com/api/documentation/ai-agent-hub/skills/cmc-x402`.
- **Locked split:** TWAK signs swaps and x402 payments; `bnbagent-sdk` supplies ERC-8004 identity
  (and its separate ERC-8183 commerce capability remains outside this Track-1 runtime). The
  locally observed `X402Signer` and Binacci's x402 wrappers are reference-only and must not be
  wired into MIDAS's funds path.
- **x402 (reference signer):** `X402Signer(wallet, max_value_per_call={token:int}, session_budget={token:int})`;
  `sign_payment(domain, types, message, expected_to)` (`x402/signer.py:83-188`) → EIP-712
  `TransferWithAuthorization` signature; X-PAYMENT = `base64(json)` header. Payment "U token"
  (United Stables): testnet `0xc70B8741…` (97), mainnet `0xcE24439F…` (56). **Security:**
  `expected_to` MUST come from a trusted source, not the 402 body. Errors:
  `X402RecipientMismatchError`, `X402AmountExceededError`, `X402BudgetExhaustedError`,
  `X402PolicyError`; budget via `SessionBudgetTracker`.
- **SDK tx model (identity/x402 path only):** `_send_tx` (`core/contract_mixin.py:34-136`)
  calls `wait_for_transaction_receipt` **synchronously**; `MAX_RETRIES=5` (nonce/429 only);
  `status==0` → `RuntimeError("Transaction reverted on-chain")`; web3 `TimeExhausted` on
  receipt timeout (NOT a `BNBAgentError`); `nonce_mgr.reset()` after failure. **No "pending"
  return** — distinct from the `baw` swap path's async orderId model.
- **Track-1 competition registration: ABSENT in the `bnbagent-sdk` / `binance-skills-hub`
  repos** (this §3 stack). It is **present** as the built-in **`twak compete register`/`status`**
  command in the *TWAK* path used by boomerang + Binacci (§0) — not conflated, just a different
  surface. So registration is *not* sourced externally; it is a TWAK CLI call.

---

## 4. What this changes in the plan

1. **Split into 3 executable plans** (GPT, agreed): **scanner-contract** / **MIDAS-spot-runtime** / **causal-replay**.
2. **Executor task grounded on TWAK** (§0): `twak swap` (submit, returns tx_hash) +
   `confirm_receipt_via_rpc` (Binacci `venues.py:48`, `confirmed`/`reverted`/`unconfirmed`) +
   on-chain-balance restart reconciliation (boomerang). **No spec rewrite** — REQ-060–070's
   tx-hash model stands; the `baw` orderId model is dropped. **Prerequisite (⚠️ §0):** a
   version-pinned credentialed **canary capture** of the real `twak --json` payloads gates the
   executor's parsing tests; the runner is **strict** (check `returncode` + validate JSON), not
   a mirror of the reference wrappers (which ignore both).
3. **Scanner plan** = (a) **extract DOL+stop resolution *independent of the sweep gate*** so
   sweep-optional aggressive setups still get a concrete DOL/stop (§1a/§1b ⚠️, the load-bearing
   task — NOT "just surface"); (b) export the **full REQ-039 record** (entry, stop
   {level/source/anchor/bar}, campaign-DOL {level/source}, checklist-DOL) from one evaluation
   (§1c); (c) make `df_15m` optional + plumb `prefer_aggressive` (§1d-§1f).
4. **DecisionPipeline** task = extract **`runner.py:52-115`** (stops/exit *and* entry decisions
   — §2a corrected) with execution side-effects behind injected adapters; close the
   `policy_config=None` bypass; add a **replay driver** (absent today) (§2b). Test via a
   `ScriptAdvisor`-style deterministic harness (lifted from `llm-trading-bot`, §5f).
5. **Two separate registration tasks:** (a) Track-1 competition registration = a thin wrapper
   over **`twak compete register`/`status`** (§2c — a CLI call, not a from-scratch contract),
   and (b) wire the already-present ERC-8004 `register_agent` (§3b) — never conflated.
6. **x402 task — TWAK-only authority** (§3b ✅): use `twak x402 quote` for read-only route and
   price discovery, then `twak x402 request` with an allowlisted CMC HTTPS endpoint,
   `--max-payment`, Base/USDC preference, EIP-3009 preference, and strict JSON parsing. Persist
   request/daily budgets and payment evidence in MIDAS before invocation. A version-pinned,
   credentialed canary still captures the exact JSON schema before parser tests are finalized.
   SDK-side signing is prohibited; x402 failure may halt fresh CMC-dependent entries but must
   never block monitoring, reconciliation, or a protective exit.
7. **Spot executor** replaces `executors/aster.py`, shelling to **`twak`**
   (`swap`/`--quote-only`/`wallet balance`/`compete`) — not a ccxt perp adapter, not `baw`
   (§2d/§0). Take boomerang's `TwakExecutor` *command vocabulary* + mandatory password
   redaction, but wrap it in a **strict runner** (returncode + JSON validation) — do not inherit
   its returncode-ignoring/tolerant-parse behavior.

---

## 5. Reference-pattern harvest (what to borrow, with evidence)

Pattern-mining across the 7 reference repos. **Integration surfaces** (must-match) are §0–§3;
this section is **edges** (reusable code/ideas). Each row: the pattern, where it lives, and how
MIDAS uses it. Repos examined: `boomerang-ai`, `trading-agent`(Binacci), `BHRAMHA`,
`AlgoBotVMC-demo`, `altpulse`, `llm-trading-bot`, `auto-ninja-trader`.

### 5a. The execution backbone — BORROW Binacci, NOT boomerang
- **Binacci `confirm_receipt_via_rpc`** (`trading-agent/agent/src/binacci/venues.py:48-81`):
  poll `eth_getTransactionReceipt` (stdlib `urllib`) → `confirmed`/`reverted`/**`unconfirmed`**.
  The only one of the three on-chain repos that **handles broadcast-unknown**. → MIDAS's
  receipt confirmer.
- **Binacci `reconcile_open_fill` / `rollback_open`** (`execution.py:298-310` / `:290-296`):
  Binacci books optimistically then reconciles/rolls back. ⚠️ **MIDAS must NOT book optimistically**
  — the spec invariant is **only CONFIRMED+RECONCILED is an open position** (`design.md:273`).
  Map Binacci's *mechanism* onto the **intent state machine**, not a position: persist the
  execution *attempt* (`INTENT_PERSISTED→…`), confirm via receipt, and **only on
  CONFIRMED+RECONCILED record a position**; `rollback` clears the *attempt/intent* (never an
  already-booked position), and `reconcile_open_fill`'s real-fill-rewrite applies at the moment
  the position is recorded. Borrow the reconcile-to-real-fill idea; reject the optimistic booking.
- **boomerang is fire-and-forget** (`twak_executor.py` `buy`/`sell` return `ok=True` on a
  parsed tx_hash, **no receipt wait**) — explicitly the model to **avoid**. Borrow only its
  **startup on-chain-balance reconciliation** (`agent.py:186-265`: drop tracked positions
  whose on-chain qty < 20%, import untracked holdings with a sentinel) as the restart-idempotency layer.

### 5b. Liquidity / universe gating — BORROW both probes (complementary)
- **Binacci `_verify_universe`** (`live.py:635-676`): send a **$1 test `twak swap --quote-only`**
  per candidate, drop on error or `priceImpact > max_price_impact_pct`, cache 24h, gate
  execution to verified symbols only. → the real-quote probe beats an orderbook check on
  fragmented BSC liquidity (directly relevant to thin tokens like APE). ⚠️ **Universe-PREFLIGHT
  only** — the cached $1 quote screens *eligibility*; it does **not** replace the **fresh,
  trade-size** quote required immediately before *every* submission (slippage/impact at $1 ≠ at
  the real notional).
- **boomerang round-trip-retention probe** (`bnb_validation.py:346-436`): whitelist (CMC
  `/info` → BSC addr) → `min_pool_liquidity_usd` → `max_pool_share_pct` (1%) → **buy+resell
  quote, reject if retention < 0.97** (catches tax/slippage/fee in one shot). → strongest
  single liquidity gate found.
- **altpulse** (`data.py:107-160`): async-batch → price-normalize → threshold-filter (CEX OI).
  → the *shape* for a pool-TVL/volume floor; OI itself doesn't exist for spot AMM tokens.

### 5c. Sizing — the formula (BHRAMHA) wired with the buffers (boomerang/Binacci)
- **BHRAMHA stop-distance fixed-fractional** (`smart_exit_engine.py:319-340`, the *unused*
  function — its live path is stop-blind fixed-notional, an **anti-pattern**):
  `risk_amount = balance*(risk_pct/100); units = risk_amount/|entry-sl|`. → the canonical
  formula MIDAS's `decision.size()` should implement (size scales inversely with stop distance).
- **boomerang 0.97 stable-buffer** (`risk_engine.py:130-158`): `min(target, available_stable*0.97)`
  — size never exceeds available stable. → borrow the buffer.
- 🛑 **REJECTED — boomerang's conviction scale** (`risk_engine.py:373-380`, 0.6–2.0×): scaling
  size *up* to 2× on conviction conflicts with MIDAS's grade-fixed sizing (A=0.5% / B=0.25%) and
  the no-sizing-up discipline. Size comes from grade + RiskPolicy caps, never a conviction multiplier.
- **Binacci 30% reserve floor** (`config.py:76-104`): never deploy >70% of equity. → borrow
  the reserve floor (it complements your ≥30% stable-reserve rule).
- 🛑 **REJECTED — Binacci's drawdown averaging** (`execution.py:172-189`). Even though it is
  *gated* (adds only in drawdown, capped at 2), it is still **averaging down**, which
  `agent-trading-rules.md:46` prohibits **without qualification** (no DCA / averaging / stacking).
  Do **NOT** borrow it. An earlier draft called it "properly-gated, borrow it" — that was a
  doctrine violation smuggled in from a reference repo. Take ONLY the 30% reserve floor above;
  position adds are out.

### 5d. Drawdown / halts — BORROW boomerang dual-gate + Binacci aggregate kill-switch + AlgoBotVMC state machine
- **boomerang dual-gate** (`risk_engine.py:56-101`): all-time `_peak_equity` + daily UTC
  anchor (`day = now_ts // 86400`), both with `-1e-9` epsilon, **fail-CLOSED** halt
  (`agent.py:622-626` → `panic()`).
- **Binacci aggregate-floating-DD kill-switch** (`execution.py:369-396`): sum floating losses
  across open positions; close **all, worst-first**. Borrow ONLY the *mechanics* (aggregate
  floating-loss calc + worst-first closure). 🛑 Its **`>= 0.30 * deposit` threshold is REJECTED**
  — MIDAS uses its own RiskPolicy thresholds (DD throttle 3% / halt 5% / review 8%, daily halt
  1.5%), not Binacci's 30%.
- **AlgoBotVMC three-state breaker** (`AlgoBotVMC-demo/src/strategy/session_tracker.py:324-365`):
  `NORMAL/RECOVERY/HALTED` with SQLite-persisted HWM; RECOVERY = 0.5× size; **survives restart**
  via `INSERT OR REPLACE`. → the cleanest state-machine encoding of MIDAS's DD throttle/halt/review tiers.
- **AlgoBotVMC layered gating order** (`trade_manager.py:341-393`): circuit-breaker → position-count
  → heat. → the order MIDAS's RiskPolicy stack should enforce.
- **AlgoBotVMC fill reconciliation by position-delta** (`trade_manager.py:230-329`): when the
  order vanishes, accept the fill if the **position-size delta** matches within 5%. → a
  *second* confirmation source alongside the tx-hash receipt poll.

### 5e. Risk layering — Binacci is the richest template
- **Binacci 5-dimension layering** (`config.py:160-194`, `execution.py:126-132`,
  `orchestrator.py:202-212`): RISK_PRESETS × REGIME_WEIGHTS (macro size mult) × per-book
  capacity caps × fee-floor gate (reject if target < breakeven) × a portfolio kill-switch
  (mechanics only — **MIDAS thresholds per §5d, not Binacci's 30%**). → the structural template;
  MIDAS adds its correlated-bucket cap (none of the repos have one). Borrow the *layering shape*,
  not Binacci's specific numbers or its REGIME size-*up* weights beyond 1.0×.

### 5f. Shared decision core + LLM seam — Binacci architecture + llm-trading-bot craft
- **Binacci hook-injected shared core** (`live.py:135-206` ≡ `backtest.py:80-110`): one
  `orchestrator.on_candle()` for paper/live/backtest; **venue injected via hooks** after
  instantiation. → the exact shape for MIDAS's `DecisionPipeline` so live/paper/replay share one
  path with the executor swapped at runtime. Borrow the **`on_open`/`on_close`** hooks only;
  🛑 the **`on_average` hook is REJECTED** — it drives position adds / averaging, forbidden by
  `agent-trading-rules.md:46`.
- **llm-trading-bot is the cautionary counter-example** (LLM owns `risk_pct` up to 100%,
  `executor.py:71-92`, no deterministic gate) — **validates** MIDAS's clamp-only doctrine.
  Borrow its **craft, not its authority model**:
  - **`ScriptAdvisor` deterministic harness** (`tests/trading_harness.py:22-34,131-175`): run
    the whole pipeline against canned decisions, zero LLM calls. → unit-test `clamp_advice` +
    drive causal-replay parity cheaply. **High-value for the replay plan.**
  - **strict JSON-schema + Pydantic + semantic-legality validator** that downgrades illegal
    LLM output to a safe `HOLD` (`client.py:107-152`). → MIDAS advisor's "illegal → safe no-op".
  - **"stops may only tighten" geometry rule** (`executor.py:133-148`) → ports to reduce-only.
  - **no-timestamp / drop-forming-candle / warmup-gate** anti-look-ahead discipline → replay hygiene.

### 5g. Not load-bearing (recorded so they aren't re-investigated)
- **`auto-ninja-trader`** — NinjaTrader/**C#** desktop-futures research (`NT8Bridge`,
  `nt8_bridge_wrapper.py`). IRRELEVANT to a BSC spot agent.
- **`llm-trading-bot` execution** — no web3/on-chain layer (CEX brokers only); value is
  cognition/test-harness (§5f), not execution.
- **`BHRAMHA` everything except the sizing formula** — Binance-Futures 20× perp, binary halt,
  hardcoded universe; CEX-perp-specific.
- **`altpulse` triggers** — perp-futures funding/OI/long-short-ratio signals; no spot analogue.
