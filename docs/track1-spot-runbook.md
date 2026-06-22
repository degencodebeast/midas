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
      deploy. Push commit `5f92552` to the public trading-scanner repo and switch
      `pyproject.toml` to the `https://github.com/degencodebeast/trading-scanner` URL
      before deploying to a VPS.
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
- [ ] Default `RiskConfig.defaults()` values are confirmed:
      - `canary_risk_fraction = 0.0025` (0.25 %)
      - `a_grade_risk_fraction = 0.005` (0.5 %)
      - `b_grade_risk_fraction = 0.0025` (0.25 %)
      - `counter_bias_multiplier = 0.50`
      - `drawdown_throttle_multiplier = 0.50`
      - `max_open_risk = 0.01` (1 %)
      - `max_correlation_bucket_risk = 0.01` (1 %)
      - `max_concurrent_positions = 1`
      - `hard_max_concurrent_positions = 2`
      - `max_token_fraction = 0.25`
      - `stable_reserve_fraction = 0.30`
      - `daily_loss_fraction = 0.015` (1.5 %)
      - `consecutive_stop_halt = 3`
      - `drawdown_throttle = 0.03` (3 %)
      - `drawdown_entry_halt = 0.05` (5 %)
      - `drawdown_review = 0.08` (8 %)
      - `hard_drawdown_dq = 0.30` (30 %)

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

- [ ] `.magic_agent/state.json` (or the configured state-journal path) has been backed
      up to a timestamped copy before live activation.
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
  uv run magic-agent run --executor twak
  ```

- [ ] The first live order uses `canary_risk_fraction = 0.0025` (0.25 %) and
      `max_concurrent_positions = 1`. Do NOT increase these limits until causal
      replay evidence justifies it.

---

## Operational status display

The runtime status (`.magic_agent/status.json`, surfaced by `magic-agent serve`)
must expose the following fields. Operators should monitor all of them:

| Field | Meaning |
|---|---|
| `grade_fraction` | Effective risk fraction applied to this setup (A vs B grade). |
| `counter_bias_multiplier` | Scaling factor applied when `bias_alignment == "counter_bias"` (default 0.50). Counter-bias requires positive top-quartile 7-day momentum; denied otherwise. |
| `drawdown_throttle` (3 %) | Risk fraction halved when peak-to-current drawdown ≥ 3 %. Entries still permitted. |
| `drawdown_entry_halt` (5 %) | New entries blocked when drawdown ≥ 5 %. Exits continue unconditionally. |
| `drawdown_review` (8 %) | Emergency review threshold: entries blocked, operator intervention required. |
| `daily_loss_halt` (1.5 %) | New entries blocked when realized daily loss ≥ 1.5 % of daily anchor equity. |
| `consecutive_stop_halt` (3 stops) | New entries blocked after 3 consecutive stop-outs. |
| `correlation_utilization` | Current correlated-bucket stressed loss as a fraction of the 1 % bucket cap. |
| `open_risk_utilization` | Current total open stressed loss as a fraction of the 1 % open-risk cap. |
| `stale_equity` | `True` when equity data is older than the allowed TTL. Entries are denied; exits continue. |
| `scanner_raw_grade` | Frozen engine grade from the scanner (`raw_grade`); immutable audit field. |
| `scanner_effective_grade` | Scanner-owned effective grade used by RiskPolicy (`grade`). May differ from `raw_grade` when scanner applies a promotion. |
| `promotion_provenance` | Human-readable reason for any promotion from raw to effective grade (`grade_promotion_reason`). `None` when no promotion. |
| `hard_drawdown_dq` (30 %) | Hard disqualification: all entries permanently denied until operator review. Peak-to-current drawdown ≥ 30 %. |

---

## Known limitations before LIVE

The following limitations are accepted and must be understood before live activation.
They do NOT block paper mode but are material for live operation.

**(a) Live (`--executor twak`) end-to-end is not yet wired or tested.**
`cli._cmd_run()` assembles the full spot runtime app and runs paper mode now — a bare
`magic-agent run` (paper default) paces one cycle per closed H1 bar via the
bar-close-aligned clock (no busy-spin). Paper mode (`--executor paper`) is the validated
surface exercised by tests and the paper cycle gate. Live wiring (`--executor twak`)
still routes through the TWAK/coordinator path that is not yet exercised end-to-end; do
not attempt live trading until that path is validated.

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

**(d) Scanner pin is a `file://` machine-local path — push before VPS deploy.**
`pyproject.toml` currently pins the scanner with `git = "file:///Users/..."`. This is
valid for local development but will fail on any other machine (VPS, CI, collaborators).
Before deploying to a VPS: push commit `5f92552e8fdd688808e2709eefc176ab681b7f4f` to
the public `github.com/degencodebeast/trading-scanner` remote, update `pyproject.toml`
to `git = "https://github.com/degencodebeast/trading-scanner"`, run `uv lock`, and
commit both files. Never deploy with a `file://` source.

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
# Confirm position and stop level in .magic_agent/status.json, then
# use twak directly to execute a manual sell if the position manager fails.
twak swap <qty> <token-contract> USDC --chain bsc --yes
```

**State inspection:**
```bash
cat .magic_agent/status.json
cat .magic_agent/decisions.jsonl | tail -20
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
- `deploy/twak-vps-bringup.sh` has passed quote-only smoke on the VPS
- TWAK wallet address matches `WALLET_ADDRESS`
- BNB gas reserve and USDC/USDT trading capital are funded
- kill-switch file path is known
- operator approval is explicit for the first live canary

Canary observation sequence:

1. Start with `magic-agent run --executor twak --max-iters 1` under direct operator supervision.
2. Confirm a scanner-authorized setup exists.
3. Confirm RiskPolicy caps the first live entry to `canary_risk_fraction = 0.0025`.
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

1. Real BSC RPC client. `build_app(mode="twak")` defaults the receipt/confirmation
   port to `_FailClosedLiveRpc` (reports status `0x0` / zero confirmations), which by
   design cannot reconcile a swap into a booked position. A real BSC RPC client that
   reads `BSC_RPC_URL` (transaction receipt + confirmation depth) MUST be built and
   injected as `live_rpc` before a funded canary can book.
2. Coordinator post-swap balance read. The post-broadcast balance snapshot in the
   execution coordinator is outside the broadcast try/except; a malformed balance read
   after a real swap must be mapped to a fail-closed terminal state (blocks new
   exposure) rather than crashing the cycle.
3. Live sell idempotency. The TWAK sell path has no execution-journal idempotency
   record; a sell broadcast whose response is lost must map to a safe terminal state
   so a protective exit is not re-broadcast on the next cycle.
4. Chain-truth restart rebuild. Live restart still loads the paper position store;
   before unsupervised live operation, open-position truth must be rebuilt from
   reconciled execution evidence + chain balances (`rebuild_positions_from_chain`)
   instead of `positions.json`, so a restart cannot re-enter a position already held
   on-chain.

Until all four are closed, operate only the supervised quote-only smoke and a single
operator-watched canary; do not enable systemd (keep the unit disabled) and do not
run autonomously.
