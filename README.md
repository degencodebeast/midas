# MIDAS — bounded autonomous ICT/SMC trading agent

`magic-agent` is a bounded autonomous trading agent. It will drive a poll loop
(`magic-agent run`), serve a read-only API (`magic-agent serve`), and render a
dashboard from `web/`. The LLM advisor is off by default, key-gated, clamp-only,
and returns `None` on failure; policy is fail-closed.

**Current status:** the component runtime and paper execution logic are implemented
and unit-tested (227 tests pass). However, end-to-end run-assembly — the production
`App` object, `cli._cmd_run`, and `reconcile_unfinished` — is **not yet implemented**,
so `magic-agent run` cannot be launched in any mode today (`cli._cmd_run` raises
`NotImplementedError`). This is tracked as the next task. See
[`docs/track1-spot-runbook.md`](docs/track1-spot-runbook.md) for the activation runbook
and known limitations.

## Install (deployable)

The scanner (`magic_scanner.scan.scan_symbols`) is declared in `pyproject.toml`
as a commit-pinned dependency (bump the `rev` deliberately, then `uv lock`,
to pick up newer scanner changes).

**Current pin:** engine commit `5f92552e8fdd688808e2709eefc176ab681b7f4f`, declared
as a machine-local `file://` source:

```toml
[tool.uv.sources]
magic-scanner = { git = "file:///Users/.../trading-scanner", rev = "5f92552e8fdd688808e2709eefc176ab681b7f4f" }
```

This means the sibling `../trading-scanner` checkout **must be present** on the same
machine. The `file://` form is valid for local development but will fail on any other
machine (VPS, CI, collaborators). Before deploying to a VPS: push commit `5f92552` to
`github.com/degencodebeast/trading-scanner` and switch `pyproject.toml` to the
`git+https` form:

```toml
magic-scanner = { git = "https://github.com/degencodebeast/trading-scanner", rev = "5f92552e8fdd688808e2709eefc176ab681b7f4f" }
```

Then run `uv lock` and commit both files. That repo is **public**, so no GitHub token
is needed for a clean VPS install. See Known limitation (d) in
[`docs/track1-spot-runbook.md`](docs/track1-spot-runbook.md).

```bash
uv sync                       # installs magic-agent + the commit-pinned scanner
uv run magic-agent serve --host 127.0.0.1 --port 8000
```

### Optional: ERC-8004 on-chain identity (`[identity]` extra)

On-chain identity is **opt-in**. Without it the agent runs normally and the
dashboard shows `unregistered` — `cli._resolve_agent_id()` returns `None` when
the SDK is absent or unconfigured, and never fabricates an id.

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

- `MAGIC_AGENT_CMC_API_KEY` — CMC API key (required to enable the client).
- `MAGIC_AGENT_CMC_BASE_URL` — optional base-URL override (defaults to
  `https://pro-api.coinmarketcap.com`).

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

The committed pin is already a `file://` local source, so `uv sync` resolves the
scanner from the sibling `../trading-scanner` checkout at the pinned commit. If you
want a fully editable (live-code) install instead, run:

```bash
uv sync
uv pip install -e ../trading-scanner   # point magic_scanner at the local checkout
```

Alternatively, temporarily swap the committed `[tool.uv.sources]` entry for the
`path = "../trading-scanner", editable = true` form in `pyproject.toml` — but do
**not** commit that; before VPS deploy the source must be changed to the `git+https`
form at the pushed `5f92552` commit (see Install section above).

## Shared local state

`magic-agent run` and `magic-agent serve` communicate through files under
`.magic_agent/` in the working directory:

```text
.magic_agent/status.json
.magic_agent/decisions.jsonl
```

Both processes must run with the same working directory.

## Track 1 spot runtime

### Execution modes

Paper is the **intended default and only validated surface** — a bare `magic-agent run`
will wire `PaperExecutionAdapter` (simulated fills, no funds, full decision logging).
Live execution (`--executor twak`) is an explicit opt-in and requires completing every
gate in the [activation runbook](docs/track1-spot-runbook.md) first.

**Note:** `magic-agent run` is not yet launchable in any mode — `cli._cmd_run` raises
`NotImplementedError` (end-to-end run-assembly is the next task). The paper and live
commands below show the intended interface once run-assembly is complete:

```bash
# NOT YET FUNCTIONAL — pending run-assembly task
uv run magic-agent run                   # paper (default — safe, no funds)
uv run magic-agent run --executor twak  # live — all activation gates must be met first
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

### First live order

The first live order uses `canary_risk_fraction = 0.0025` (0.25 %) and one concurrent
position. These limits must not be raised until causal replay evidence justifies it.

### Activation runbook

See [`docs/track1-spot-runbook.md`](docs/track1-spot-runbook.md) for the full
operator checklist, all activation gates, operational status display fields, known
limitations, and emergency procedures.

## Tests

```bash
uv run pytest
```
