# MIDAS — bounded autonomous ICT/SMC trading agent

`magic-agent` is a bounded autonomous trading agent. It will drive a poll loop
(`magic-agent run`), serve a read-only API (`magic-agent serve`), and render a
dashboard from `web/`. The LLM advisor is off by default, key-gated, clamp-only,
and returns `None` on failure; policy is fail-closed.

**Current status:** the production run-assembly (`App` + `build_app(mode)` +
`cli._cmd_run` + `reconcile_unfinished`) is implemented and **paper mode is now
runnable** (the full `uv run pytest -q` suite passes). `uv run magic-agent run --executor paper --max-iters 1`
completes an offline cycle with exit code 0 — no network, no funds, no signing
(uses `FixtureCmcClient` + `FixtureFrameSource`). The offline paper feed monitors
the fixtured symbol set (ZEC fixture committed); `--live-frames --live-cmc` restore
the full Track-1 universe on real gate.io + CoinMarketCap data. Live execution
(`--executor twak --live-frames --live-cmc`) IS wired and **signs real BSC swaps**
via TWAK — operator-gated (requires the live secrets + a funded wallet + explicit
`--executor twak`) and supervised-canary-proven; never run it without those. See
[`docs/track1-spot-runbook.md`](docs/track1-spot-runbook.md) for the activation runbook
and known limitations.

## Install (deployable)

The scanner (`magic_scanner.scan.scan_symbols`) is declared in `pyproject.toml`
as a commit-pinned dependency (bump the `rev` deliberately, then `uv lock`,
to pick up newer scanner changes).

**Current pin:** engine commit `5f92552e8fdd688808e2709eefc176ab681b7f4f`, declared
as a deployable `git+https` source (NOT a machine-local `file://` — so a clean
VPS/CI install resolves the scanner from GitHub, no sibling checkout required):

```toml
magic-scanner = { git = "https://github.com/degencodebeast/trading-scanner", rev = "5f92552e8fdd688808e2709eefc176ab681b7f4f" }
```

That repo is **private**, so a clean VPS/CI
install needs a **read-only GitHub token** (a fine-grained PAT with Contents: read on
`trading-scanner`) configured via git's credential helper / `url.insteadOf` — **never**
embedded in the pinned URL or committed. See Known limitation (d) in
[`docs/track1-spot-runbook.md`](docs/track1-spot-runbook.md), and `deploy/docker-smoke.sh`
which verifies the token-authenticated clone in a clean Linux container.

```bash
uv sync                       # installs magic-agent + the commit-pinned scanner
uv run magic-agent serve --host 127.0.0.1 --port 8000
```

### Optional: ERC-8004 on-chain identity (`[identity]` extra)

On-chain identity is **opt-in** and independent of the trading path. Without it the
dashboard shows `unregistered` — `cli._resolve_agent_id()` returns `None` when
the SDK is absent or unconfigured, and never fabricates an id. (Identity does not
gate the runtime; paper mode is runnable and live mode (`--executor twak`) is wired and
signs real BSC swaps — see Current status.)

```bash
uv sync --extra identity            # or: pip install 'magic-agent[identity]'
```

The optional dependency is the `bnbagent` SDK
(<https://github.com/bnb-chain/bnbagent-sdk>; distribution and import package are
both `bnbagent`), also pinned to an immutable commit in `pyproject.toml` for
reproducible identity installs. Identity also requires the ERC-8004 env vars
(`MAGIC_AGENT_ERC8004_URI`, `MAGIC_AGENT_ERC8004_RPC`, `MAGIC_AGENT_ERC8004_KEY`).
Note: the production wiring against the SDK's real `ERC8004Agent` API is still
pending, so identity currently stays `unregistered` even with the extra
installed — this is intentional and honest, not a silent failure.

### Live context (CMC)

The agent can wire a **real** CoinMarketCap client into the live context path, gated
on env config:

- `CMC_API_KEY` — CoinMarketCap API key. Required for live mode (it is in
  `_require_live_env`); `--live-cmc` wires the real `CoinMarketCapClient` from it, and
  the build fails closed if it is absent. (The base URL `https://pro-api.coinmarketcap.com`
  is a client default, not an env override in the live build path.)

**Configured** (key present): a real authenticated client is wired in. On each context
fetch it issues one GET (10s timeout) to the CMC Fear & Greed endpoint and **logs** the
raw reading (`magic_agent.cmc` logger, INFO). Any fetch error or timeout degrades the
context to `status="unavailable"` (the loop never blocks on CMC). However, this runs in
**observe-only** mode — it does
**not** gate trades. The client deliberately feeds the decision gate a non-vetoing
`regime="neutral", risk_flag="low"` context regardless of the Fear & Greed value. The
mapping from a CMC reading to a `regime`/`risk_flag` veto is a deliberate, **deferred**
trading-policy decision to be validated against real observations before it can influence
sizing or vetoes. So today: CMC is fetched and surfaced, the deterministic scanner path
remains authoritative, and CMC does **not** yet gate trades.

**Unconfigured** (no key): the factory returns `None`, the context adapter degrades
honestly to `status="unavailable"`, and the deterministic scanner path stands on its own
(unavailable context is a full passthrough — no veto, no boost).

### Local dev: editable sibling scanner

The committed pin is a `git+https` source, so `uv sync` resolves the scanner from
GitHub at the pinned commit (works on any machine). If you want a fully editable
(live-code) install against your local sibling checkout instead, run:

```bash
uv sync
uv pip install -e ../trading-scanner   # point magic_scanner at the local checkout
```

Alternatively, temporarily swap the committed `[tool.uv.sources]` entry for the
`path = "../trading-scanner", editable = true` form in `pyproject.toml` — but do
**not** commit that; before VPS deploy the source must be changed to the `git+https`
form at the pushed `5f92552` commit (see Install section above).

## Shared local state

`magic-agent run` and `magic-agent serve` communicate through files under a **per-mode**
journal tree in the working directory — paper writes under `.magic_agent/paper/`, live
(twak) under `.magic_agent/twak/`:

```text
.magic_agent/twak/status.json      # live status snapshot (run --executor twak)
.magic_agent/twak/decisions.jsonl
.magic_agent/paper/status.json     # paper status snapshot (run --executor paper)
.magic_agent/paper/decisions.jsonl
```

Both processes must run with the same working directory. `serve` defaults to the live
per-mode status path; point it at the paper path with `magic-agent serve --status
.magic_agent/paper/status.json` to view a paper run (see `serve --status`).

## Track 1 spot runtime

### Execution modes

Paper is the **intended default and only validated surface** — a bare `magic-agent run`
will wire `PaperExecutionAdapter` (simulated fills, no funds, full decision logging).
Live execution (`--executor twak`) is an explicit opt-in and requires completing every
gate in the [activation runbook](docs/track1-spot-runbook.md) first.

**Note:** paper mode is fully runnable. Live mode (`--executor twak --live-frames
--live-cmc`) is wired and **signs real BSC swaps** via TWAK — gate it behind the live
secrets + a funded wallet and the [runbook](docs/track1-spot-runbook.md) checks before use.

```bash
uv run magic-agent run                           # paper (default — offline, no funds, no signing)
uv run magic-agent run --executor paper --max-iters 1  # paper single-cycle smoke test
uv run magic-agent run --executor twak --live-frames --live-cmc  # LIVE — signs real BSC swaps (requires live env + funded wallet)
```

### Authority model

- **TWAK is the sole signer** for all swaps and x402 payments. No other signing path
  exists in the live runtime.
- **The scanner owns setup authority.** `scanner_gateway.scan` is the only source of
  `AuthorizedSetup`; CMC is rank/veto only and never creates or overrides a setup.
- **RiskPolicy is mandatory.** Missing policy raises `RuntimeError` before any entry is
  sized — the only exception is a protective exit on a reconciled position.
- **No optimistic booking.** A position is booked only after receipt confirmation and
  balance-delta reconciliation. An unconfirmed or mismatched receipt blocks new
  exposure.

### Risk model (fixed-margin sizing)

Track 1 uses **fixed-MARGIN sizing**, not stop-derived risk sizing. Each entry deploys a
fixed fraction of EQUITY as notional; the scanner stop is **exit geometry only** (used for
risk tracking + the safety ceilings, never to size the position). Per-trade risk is an
OUTPUT: `risk = deploy_notional × stop_distance%`. The actual values
(`RiskConfig.defaults()` in `src/magic_agent/risk_policy.py`):

- **A-grade** deploys `a_grade_margin_fraction = 0.05` (5 % of equity); **B-grade** deploys
  `b_grade_margin_fraction = 0.025` (2.5 %); the canary cap is `canary_margin_fraction = 0.05`
  (an upper bound, applied only when in canary mode).
- **Counter-bias** keeps the `counter_bias_multiplier = 0.50` (applied to the margin %),
  and requires positive top-quartile 7-day momentum or it is denied.
- **Concurrency:** `max_concurrent_positions = 3` (= `hard_max_concurrent_positions`), so
  max deployed ≈ 3 × 5 % = 15 % of equity (A-only). No separate open-risk-from-margin cap —
  concurrency × margin bounds it naturally.
- **Safety ceilings** still bind on the stop-derived risk: `max_open_risk = 0.06`,
  `max_correlation_bucket_risk = 0.06`, `daily_loss_fraction = 0.10`,
  `stable_reserve_fraction = 0.015`, `consecutive_stop_halt = 3`. A pathologically wide stop
  can still TRIM or deny via the open-risk / correlation-bucket / daily-loss caps.

These limits must not be raised until causal replay evidence justifies it.

### Graduated drawdown ladder

`drawdown = (peak_equity − equity) / peak_equity`, applied highest-band-first (see
`RiskConfig.defaults()`). Protective EXITS always run; only NEW entries are gated:

| Band | Threshold | Effect |
|---|---|---|
| Normal | `< 0.05` | margin ×1, A+B grades, full concurrency (3) |
| Throttle | `≥ drawdown_throttle = 0.05` | margin × `0.50`, A+B, max **2** positions |
| Defense | `≥ drawdown_defense = 0.10` | margin × `0.25`, **A-only**, max **1** position |
| Entry halt | `≥ drawdown_entry_halt = 0.15` | block ALL new entries (exits continue) |
| Hard review | `≥ drawdown_hard_review = 0.20` | deny new entries, operator review required |
| Hard DQ | `≥ hard_drawdown_dq = 0.30` | hard disqualification — all entries denied |

### Activation runbook

See [`docs/track1-spot-runbook.md`](docs/track1-spot-runbook.md) for the full
operator checklist, all activation gates, operational status display fields, known
limitations, and emergency procedures.

## Tests

```bash
uv run pytest
```
