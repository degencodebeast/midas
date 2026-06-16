# MIDAS — bounded autonomous ICT/SMC trading agent

`magic-agent` is a bounded autonomous trading agent. It drives a poll loop
(`magic-agent run`), serves a read-only API (`magic-agent serve`), and renders a
dashboard from `web/`. The LLM advisor is off by default, key-gated, clamp-only,
and returns `None` on failure; policy is fail-closed.

## Install (deployable)

A fresh machine needs only this repo checked out — no sibling `trading-scanner`
checkout is required. The scanner is pulled from a git source **pinned to an
immutable commit** (`rev = <sha>`), so installs are reproducible and the resolved
source can't drift to a new branch HEAD under you.

```bash
uv sync                       # installs magic-agent + the commit-pinned scanner
uv run magic-agent run --executor paper
uv run magic-agent serve --host 127.0.0.1 --port 8000
```

The scanner (`magic_scanner.scan.scan_symbols`) is declared in `pyproject.toml`
as a commit-pinned git dependency (bump the `rev` deliberately, then `uv lock`,
to pick up newer scanner changes):

```toml
[tool.uv.sources]
magic-scanner = { git = "https://github.com/degencodebeast/trading-scanner", rev = "4ccd1a95b2b32262dbcffd7a920fcba5eb033f54" }
```

That repo is **public**, so no GitHub token is needed for a clean VPS install.
(If it were private, the VPS would need a token, e.g. a `GIT_*`/credential
helper or `git config url."https://<TOKEN>@github.com/".insteadOf` so `uv` can
fetch it.)

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

Workspace devs who want the local editable scanner can override after sync:

```bash
uv sync
uv pip install -e ../trading-scanner   # point magic_scanner at the local checkout
```

Alternatively, temporarily swap the committed `[tool.uv.sources]` entry for the
commented `path = "../trading-scanner", editable = true` form in
`pyproject.toml` — but do **not** commit that; the committed source must remain
the commit-pinned git source so the deployable install stays self-contained.

## Shared local state

`magic-agent run` and `magic-agent serve` communicate through files under
`.magic_agent/` in the working directory:

```text
.magic_agent/status.json
.magic_agent/decisions.jsonl
```

Both processes must run with the same working directory.

## Tests

```bash
uv run pytest
```
