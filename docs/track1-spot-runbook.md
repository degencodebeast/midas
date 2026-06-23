# Track 1 Spot Activation Runbook

Operational runbook for promoting MIDAS from paper mode to a live Track 1 spot-long
execution on BNB Chain. Every gate in Step 3 is a hard prerequisite — no gate may be
waived. Complete them in order; document every observation and artifact hash before
continuing.

---

## Step 1 — Run deterministic gates

```bash
uv run pytest -q
```

Expected: all tests PASS with no network.

```bash
rg -n "policy_config is not None|AsterRestExecutor|ENTER_SHORT" src/magic_agent
```

Expected: no active-runtime bypass or directional-short import matches. Any hit is
release-blocking.

---

## Step 2 — Credentialed observations without funds

Run with real TWAK credentials but ZERO balance — these observations do not activate
live trading:

```bash
twak auth status
twak compete status --json
twak swap 1 USDC <your-wallet-address> --chain bsc --quote-only --json
twak swap 1 <ZEC-contract-address> <your-wallet-address> --chain bsc --quote-only --sell --json
twak x402 quote 'https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?id=1' --json
```

Save redacted JSON artifacts from each command. Confirm exit code 0 and parseable JSON
for every call. These are the schema fixtures for `tests/fixtures/twak_quote.json` etc.

> **Note:** Step 2 requires live TWAK credentials and is NOT validated in CI. It is an
> operator-performed gate that must be completed and documented before live activation.

---

## Step 3 — Activation gates (mandatory checklist — all items required)

Work through every item below. Mark each `[x]` only after you have independently
confirmed it. Do not mark items speculatively.

### 3.1  Scanner commit pin

- [ ] `pyproject.toml` pins `magic-scanner` to commit `5f92552e8fdd688808e2709eefc176ab681b7f4f`
      (or a reviewed successor commit — never a branch reference).
- [ ] The scanner source uses a `git = "https://github.com/..."` URL, **not** a
      `file://` local path. The `file://` form is machine-local and will fail on VPS
      deploy. Commit `5f92552` is pushed to the (PRIVATE) trading-scanner repo and
      `pyproject.toml` uses the `https://github.com/degencodebeast/trading-scanner` URL.
      Because the repo is private, the VPS/CI clone needs a read-only GitHub token (d).
- [ ] `uv lock` has been re-run after any `pyproject.toml` change; `uv.lock` is
      committed.
- [ ] `uv run python -c "import magic_scanner; print(magic_scanner.__version__)"` (or
      equivalent import check) exits cleanly.

### 3.2  Fresh post-Plan-1 filter artifact

- [ ] `data/research/latest_track1_filter.json` exists and was produced AFTER scanner
      Plan 1 was reviewed and committed (not from an earlier stale scan).
- [ ] The artifact records: input/output hashes, observation timestamp, scanner commit
      SHA, all exclusion reasons, and the six pinned monitoring symbols.
- [ ] The artifact hash is stable — re-running the same scan at the same scanner commit
      produces the same exclusion set (deterministic).

### 3.3  149-row eligibility accounting

- [ ] `data/track1_eligibility.json` covers all 149 competition rows.
- [ ] Every row carries an explicit `coverage_status` (`scannable`, `unscannable`, or
      `excluded`) and a `reason` for non-scannable rows.
- [ ] The total of all coverage-status counts equals 149 (no silent omissions).
- [ ] Exclusion reasons do not contain any `location_candidate` token as tradable — this
      field is observation-only and must never appear as a trade authorization.

### 3.4  Pinned monitoring list

- [ ] `src/magic_agent/watchlist.py` `PINNED_SYMBOLS` equals
      `("ZEC", "DEXE", "TRX", "APE", "LINK", "XRP")` exactly (six symbols, in this
      order).
- [ ] `data/track1_identities.json` carries gold-verified identity records for each of
      the six pinned symbols.
- [ ] Running `uv run pytest -q -k watchlist` passes (pinned-symbol unit tests green).

### 3.5  Gold identity required before execution

- [ ] Every symbol in the execution path has `verification_status = "gold"` in the
      identity registry (`data/track1_identities.json`).
- [ ] `identity_registry.execution_eligible()` returns only gold-verified records.
- [ ] The runner loop (`runner.run_cycle`) skips and journals any candidate with
      `execution_eligible = False` before sizing. Confirm by reading
      `src/magic_agent/runner.py` lines that check `candidate.execution_eligible`.
- [ ] On-chain contract address, `symbol()` return, and decimals have been verified for
      each gold token (exact match — no substring match, e.g. no `"B" in "BUSD"` false
      positive).

### 3.6  Fresh CMC snapshot

- [ ] A fresh CMC batch snapshot (via x402 or direct API) has been captured and saved
      to `data/research/latest_track1_cmc_snapshot.json` within the last 4 hours.
- [ ] The snapshot covers all 149 competition rows (or explicitly records which rows are
      unscannable).
- [ ] The snapshot timestamp (`observed_at`) is within TTL before live activation
      begins.
- [ ] CMC is rank/veto only — it does NOT authorize setups or override scanner
      authorization. Confirm no code path treats a CMC signal as a trade trigger.

### 3.7  Mandatory RiskPolicy

- [ ] A `RiskConfig` instance (or explicit `None` for fail-safe deny-increase) is wired
      into the live `app.risk_policy`.
- [ ] `runner.run_cycle` raises `RuntimeError("mandatory RiskPolicy is missing")` when
      `app.risk_policy is None` — verify this path is NOT bypassed.
- [ ] Default `RiskConfig.defaults()` values are confirmed (FIXED-MARGIN sizing — the
      position is a fixed % of EQUITY; the scanner stop is exit geometry only, used for
      risk tracking + the safety ceilings, NOT to size the position):
      - `canary_margin_fraction = 0.05` (5 % — canary upper bound)
      - `a_grade_margin_fraction = 0.05` (5 %)
      - `b_grade_margin_fraction = 0.025` (2.5 %)
      - `counter_bias_multiplier = 0.50`
      - `max_open_risk = 0.06` (6 % — stop-derived open-risk ceiling)
      - `max_correlation_bucket_risk = 0.06` (6 %)
      - `max_concurrent_positions = 3`
      - `hard_max_concurrent_positions = 3`
      - `max_token_fraction = 0.50`
      - `stable_reserve_fraction = 0.015` (1.5 %)
      - `daily_loss_fraction = 0.10` (10 %)
      - `consecutive_stop_halt = 3`
      - Graduated drawdown ladder (strictly ordered):
        - `drawdown_throttle = 0.05` (5 %) → margin × `drawdown_throttle_multiplier = 0.50`,
          A+B, max `drawdown_throttle_max_positions = 2`
        - `drawdown_defense = 0.10` (10 %) → margin × `drawdown_defense_multiplier = 0.25`,
          A-only, max `drawdown_defense_max_positions = 1`
        - `drawdown_entry_halt = 0.15` (15 %) → block all new entries (exits continue)
        - `drawdown_hard_review = 0.20` (20 %) → deny new entries, operator review
        - `hard_drawdown_dq = 0.30` (30 %) → hard disqualification

### 3.8  Gas reserve

- [ ] TWAK wallet holds sufficient BNB for gas on BSC (≥ the estimated gas for 2
      swap transactions — one buy, one stop-exit — plus a 2× safety margin).
- [ ] `twak wallet balance --json` confirms BNB balance above the reserve threshold.
- [ ] Gas reserve is tracked separately from USDT trading capital; it is NOT counted as
      part of equity.

### 3.9  Fresh two-sided final-size quote

- [ ] A fresh buy-side quote (`twak swap <qty> USDC <token-contract> --chain bsc
      --quote-only --json`) has been obtained for the intended first-trade size within
      the last 60 seconds.
- [ ] A fresh sell-side quote (`twak swap <qty> <token-contract> USDC --chain bsc
      --quote-only --sell --json`) has been obtained for the same size.
- [ ] Both quotes carry all required fields: `output_qty`, `provider`,
      `minimum_output`, `impact_bps`, `slippage_bps`, `expires_at`.
- [ ] Both `output_qty` and `minimum_output` are positive (non-zero).
- [ ] `expires_at` is in the future at the moment of submission.
- [ ] `executability.validate_round_trip` returns `approved=True` for the pair.

### 3.10  Empty unfinished-execution set

- [ ] `execution_journal` contains no records in state `INTENT_PERSISTED`,
      `EXECUTING`, `SUBMITTED`, `MINED`, or `BROADCAST_UNKNOWN`.
- [ ] Any leftover records from a prior paper or test cycle have been reconciled or
      explicitly cleared before live activation.
- [ ] `app.reconcile_unfinished()` ran at startup and produced no unresolved records.

### 3.11  Competition registration

- [ ] `twak compete status --json` returns a confirmed registration record for this
      wallet.
- [ ] `registration.CompetitionRegistrar.status()` returns the same record without
      error.
- [ ] Registration is via TWAK only (the sole swap/x402 signer). ERC-8004 identity is
      separate and independent — do not conflate the two.

### 3.12  State backup

- [ ] `.magic_agent/twak/state.json` (the live per-mode state-journal path; or the
      configured path) has been backed up to a timestamped copy before live activation.
- [ ] `state_journal` records peak equity, daily anchor, current positions, and risk
      state; these survive restart (atomic integrity-protected write confirmed by tests).
- [ ] Peak equity is initialized from actual wallet balance, NOT from a hardcoded
      starting value.

### 3.13  Paper cycle passed

- [ ] At least one complete paper spot cycle has been run end-to-end on a gold token:
      entry decision → paper fill → position booked → stop/DOL exit → flat.
- [ ] `uv run magic-agent run --executor paper` ran without errors for at least one
      closed H1 bar.
- [ ] Paper cycle decisions are logged in `.magic_agent/decisions.jsonl`.
- [ ] Dashboard (`magic-agent serve`) reflected the paper position correctly.

### 3.14  Explicit operator live activation

- [ ] All gates 3.1 – 3.13 are confirmed `[x]` above.
- [ ] The operator has read and understood the "Known limitations" section below.
- [ ] A final `uv run pytest -q` confirms all tests still pass.
- [ ] Live activation command:

  ```bash
  # Live loop (omit --max-iters to run unbounded, paced to closed H1 bars):
  uv run magic-agent run --executor twak --live-frames --live-cmc
  ```

  The `--live-frames --live-cmc` flags are REQUIRED for live: without them the loop
  scans only the committed offline fixture (ZEC) instead of the real Track-1 universe.

- [ ] The first live order is sized by the canary cap `canary_margin_fraction = 0.05`
      (a 5 % margin upper bound, applied while in canary mode) under
      `max_concurrent_positions = 3`. Do NOT raise these limits until causal replay
      evidence justifies it.

---

## Operational status display

The runtime status (the per-mode snapshot — live writes `.magic_agent/twak/status.json`,
paper `.magic_agent/paper/status.json` — surfaced by `magic-agent serve` pointed at that
path) must expose the following fields. Operators should monitor all of them:

| Field | Meaning |
|---|---|
| `grade_fraction` | Effective MARGIN fraction (deploy-% of equity) applied to this setup. A-grade 5 %, B-grade 2.5 % (fixed-margin sizing — the scanner stop does NOT size the position). |
| `counter_bias_multiplier` | Scaling factor applied to the margin % when `bias_alignment == "counter_bias"` (default 0.50). Counter-bias requires positive top-quartile 7-day momentum; denied otherwise. |
| `drawdown_throttle` (5 %) | Margin halved (×0.50), max 2 positions (A+B), when peak-to-current drawdown ≥ 5 %. Entries still permitted. |
| `drawdown_defense` (10 %) | Margin × 0.25, A-grade only, max 1 position, when drawdown ≥ 10 %. Entries still permitted. |
| `drawdown_entry_halt` (15 %) | New entries blocked when drawdown ≥ 15 %. Exits continue unconditionally. |
| `drawdown_hard_review` (20 %) | Emergency review threshold: entries blocked, operator intervention required. |
| `daily_loss_halt` (10 %) | New entries blocked when realized daily loss ≥ 10 % of daily anchor equity. |
| `consecutive_stop_halt` (3 stops) | New entries blocked after 3 consecutive stop-outs. |
| `correlation_utilization` | Current correlated-bucket stressed loss as a fraction of the 6 % bucket cap. |
| `open_risk_utilization` | Current total open stressed loss as a fraction of the 6 % open-risk cap. |
| `stale_equity` | `True` when equity data is older than the allowed TTL. Entries are denied; exits continue. |
| `scanner_raw_grade` | Frozen engine grade from the scanner (`raw_grade`); immutable audit field. |
| `scanner_effective_grade` | Scanner-owned effective grade used by RiskPolicy (`grade`). May differ from `raw_grade` when scanner applies a promotion. |
| `promotion_provenance` | Human-readable reason for any promotion from raw to effective grade (`grade_promotion_reason`). `None` when no promotion. |
| `hard_drawdown_dq` (30 %) | Hard disqualification: all entries permanently denied until operator review. Peak-to-current drawdown ≥ 30 %. |

---

## Known limitations before LIVE

The following limitations are accepted and must be understood before live activation.
They do NOT block paper mode but are material for live operation.

**(a) Live (`--executor twak --live-frames --live-cmc`) IS wired and signs real BSC swaps.**
`cli._cmd_run()` assembles the full spot runtime app; both paper and live modes pace one
cycle per closed H1 bar via the bar-close-aligned clock (no busy-spin). The live path
(scanner → RiskPolicy → TWAK coordinator → on-chain swap → reconcile) has been exercised
end-to-end by a supervised canary on a funded BSC wallet, and is operator-gated (requires
the live secrets + a funded wallet + explicit `--executor twak`). REMAINING live
limitations before UNATTENDED operation: chain-position-rebuild on restart is not yet
wired — a crash/restart or a cross-machine (local→VPS) handoff with an open position can
re-enter, so run on ONE machine (or copy `.magic_agent/twak/`) until that lands; and sell
idempotency on a lost-response edge is pending. Supervised single-cycle
(`--max-iters 1`) live runs are safe; unattended looping needs those two items.

**(b) x402 budget hardening is pending.**
The current `X402Client` enforces a per-request and daily budget, but it does NOT yet:
apply a canary budget for first-run operation; gate on the server-reported payment
amount before paying (pay only if `server_amount <= budget.max_request_usd`); or fail
closed on unknown asset or network (only USDC on Base is allowlisted). These
hardenings must be implemented before x402 payments are made in production.

**(c) bnbagent-sdk / ERC-8004 surface is UNVALIDATED.**
`registration.Erc8004Registrar` and `identity.IdentityAgent` are built against an
assumed SDK API shape (`register_agent(*, agent_uri: str) -> dict` with keys
`"agentId"` and `"transactionHash"`). The real bnbagent-sdk API surface has NOT been
confirmed via documentation (Context7 was unreachable at build time). Confirm method
names, parameter names, and return shape against the actual bnbagent-sdk docs before
deploying a real ERC-8004 adapter. Use `uv sync --extra identity` only after this
validation.

**(d) Scanner is a PRIVATE `git+https` pin — deploys need a read-only GitHub token.**
`pyproject.toml` pins the scanner at `git = "https://github.com/degencodebeast/trading-scanner"`
rev `5f92552e8fdd688808e2709eefc176ab681b7f4f` (no more `file://` — that is resolved).
Because `degencodebeast/trading-scanner` is **private**, a clean `uv sync` on the VPS / CI /
Docker must authenticate the clone (a Mac with cached GitHub creds works, but a fresh box
does not — verified: `deploy/docker-smoke.sh` fails closed without a token). Configure a
**fine-grained read-only PAT** (Contents: read on `trading-scanner`) via git — do NOT put
the token in the pinned URL or commit it:

```bash
# on the VPS (token in env / git config only, never in the repo):
git config --global url."https://oauth2:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
chmod 600 ~/.gitconfig   # the token lands here in plaintext — restrict it
uv sync                  # now resolves the private scanner with the token
```

Verify before the VPS with `GITHUB_TOKEN_FILE=~/.midas-gh-token bash deploy/docker-smoke.sh`
(a clean Linux container proves the token-authenticated clone + paper runtime). Never deploy
with a `file://` source.

---

## Emergency procedures

**Halt all new entries immediately:**
```bash
# Kill the run loop process (Ctrl-C or SIGTERM to the systemd unit).
# Existing positions continue to be managed by the position manager on restart.
```

**Force position exit:**
```bash
# Restart with paper executor to inspect state without risking new orders.
uv run magic-agent run --executor paper
# Confirm position and stop level in .magic_agent/twak/status.json (live; paper writes
# .magic_agent/paper/status.json), then
# use twak directly to execute a manual sell if the position manager fails.
twak swap <qty> <token-contract> USDC --chain bsc --yes
```

**State inspection (per-mode tree — twak = live, paper = paper):**
```bash
cat .magic_agent/twak/status.json
cat .magic_agent/twak/decisions.jsonl | tail -20
```

**If `BROADCAST_UNKNOWN` state is detected:**
Do NOT retry. Check the BSC explorer for the transaction hash recorded in
`execution_journal`. If confirmed: manually reconcile and update `state_journal`.
If not found: treat as not broadcast and clear the record after operator review.
BROADCAST_UNKNOWN blocks all new exposure until resolved.

---

## Supervised live canary gate

Do not enable systemd or run autonomous live mode before this gate is complete.

Prerequisites:

- scanner dependency is a deployable git pin at `5f92552e8fdd688808e2709eefc176ab681b7f4f`
- the VPS can clone the PRIVATE scanner: a read-only GitHub token is configured in git
  (see Known limitation (d)) — a fresh box has no cached creds and `uv sync` fails closed
- the VPS runs **Node >= 20.19** (Node 24 recommended, matching the tested local setup).
  The distro default `apt install nodejs` (Node 18) CRASHES the TWAK CLI with
  `ERR_REQUIRE_ESM` — install Node 24 via NodeSource:
  `curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && apt-get install -y nodejs`
- `deploy/twak-vps-bringup.sh` has passed quote-only smoke on the VPS
- a clean-Linux deploy smoke (`deploy/docker-smoke.sh`) has passed (proves the private
  scanner clone + paper runtime + TWAK-on-Node-24 before the VPS)
- TWAK wallet address matches `WALLET_ADDRESS`
- BNB gas reserve and USDC/USDT trading capital are funded
- kill-switch file path is known
- operator approval is explicit for the first live canary

Canary observation sequence:

1. Start with `magic-agent run --executor twak --live-frames --live-cmc --max-iters 1` under direct operator supervision.
2. Confirm a scanner-authorized setup exists.
3. Confirm RiskPolicy caps the first live entry to the canary margin cap (`canary_margin_fraction = 0.05`).
4. Confirm TWAK quote is fresh and exact-size.
5. Confirm TWAK submit returns a transaction hash.
6. Confirm the chain receipt reaches the required confirmations.
7. Confirm balance-delta reconciliation books the position.
8. Confirm the position appears in status and journals as confirmed and reconciled.

If any step fails, stop autonomous activation. Do not promote to normal scoring mode.

### Known live-execution gaps (must close before funded/autonomous live)

The Phase 0 live wiring is fail-closed but incomplete. The supervised quote-only
smoke above is safe (no funds move), but the following MUST be closed before any
funded or unsupervised live run, and the systemd unit MUST stay disabled until then:

1. Real BSC RPC client — DONE. `build_app(mode="twak")` now defaults the
   receipt/confirmation port to the real `BscRpcClient` (`live_rpc.py`), which reads
   `BSC_RPC_URL` and queries the transaction receipt + confirmation depth over
   stdlib JSON-RPC. It fails closed: a receipt-poll timeout or RPC/transport error
   RAISES (never a fabricated `0x0` receipt), so the coordinator maps it to a
   `BROADCAST_UNKNOWN` (exposure-blocking) outcome. Construction is lazy (no chain
   call until a cycle runs), so a funded canary can now book. The coordinator's
   post-swap reads (confirmations + post balance snapshot + reconcile) are also now
   guarded: a fault there returns `MINED` / `BROADCAST_UNKNOWN` (a non-terminal,
   exposure-blocking state) instead of crashing the cycle. Inject `live_rpc` only to
   override the default with a stub.
2. Live sell idempotency. The TWAK sell path has no execution-journal idempotency
   record; a sell broadcast whose response is lost must map to a safe terminal state
   so a protective exit is not re-broadcast on the next cycle.
3. Chain-truth restart rebuild. Live restart still loads the paper position store;
   before unsupervised live operation, open-position truth must be rebuilt from
   reconciled execution evidence + chain balances (`rebuild_positions_from_chain`)
   instead of `positions.json`, so a restart cannot re-enter a position already held
   on-chain.

Until the remaining gaps are closed, operate only the supervised quote-only smoke and a single
operator-watched canary; do not enable systemd (keep the unit disabled) and do not
run autonomously.

---

## Track 1 Live Canary To Scoring Run

The first `magic-agent run --executor twak --live-frames --live-cmc` activation uses a mandatory first live canary. The canary is a supervised safety gate, not the scoring mode.

Canary rules:

- the canary margin cap `canary_margin_fraction = 0.05` bounds the deploy while in canary mode (fixed-margin sizing — the scanner stop is exit geometry only)
- scanner authorization is still required
- RiskPolicy approval is still required
- exact-size TWAK quote is required
- TWAK submission must confirm on BSC
- balance-delta reconciliation must book the position
- failed, timed-out, or `BROADCAST_UNKNOWN` canary attempts do not promote

Promotion rules:

- after a reconciled canary and passing cost viability, the runtime may promote to normal scoring mode
- normal scoring uses RiskPolicy fixed-MARGIN sizing: A-grade deploys 5% of equity, B-grade 2.5%, counter-bias with the 0.50x multiplier; the scanner stop is exit geometry only (risk = deploy × stop%)
- hard-DQ, daily halt, the graduated drawdown ladder (throttle→defense→entry_halt→hard_review→DQ), concurrency cap (max 3), token cap, stable reserve, stale equity, and consecutive-stop halt remain active

Qualification:

- the runtime tracks minimum trade-count pace
- behind-pace warnings may increase operator attention or discovery urgency
- behind-pace status cannot force trades or bypass scanner authorization

Cost viability:

- before autonomous normal scoring mode, live quotes must show that gas, swap fees, slippage, and impact do not obviously dominate the intended order size
- malformed, expired, missing, or zero-output quote data fails closed

Agent narrative:

- the dashboard and journals should show observed -> scanned -> authorized or denied -> sized -> quoted -> signed -> reconciled -> monitored or exited
- smart-money and LLM supervisor are deferred from the live canary critical path
- future smart-money or LLM features must be advisory, non-blocking, and unable to alter scanner, stop, DOL, or RiskPolicy authority
