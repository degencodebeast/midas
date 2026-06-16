# MIDAS — bounded autonomous ICT/SMC trading agent

`magic-agent` is a bounded autonomous trading agent. It drives a poll loop
(`magic-agent run`), serves a read-only API (`magic-agent serve`), and renders a
dashboard from `web/`. The LLM advisor is off by default, key-gated, clamp-only,
and returns `None` on failure; policy is fail-closed.

## Install (deployable)

A fresh machine needs only this repo checked out — no sibling `trading-scanner`
checkout is required. The scanner is pulled from a **git-pinned** source.

```bash
uv sync                       # installs magic-agent + the git-pinned scanner
uv run magic-agent run --executor paper
uv run magic-agent serve --host 127.0.0.1 --port 8000
```

The scanner (`magic_scanner.scan.scan_symbols`) is declared in `pyproject.toml`
as a git dependency:

```toml
[tool.uv.sources]
magic-scanner = { git = "https://github.com/degencodebeast/trading-scanner", branch = "build/scanner-core" }
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
both `bnbagent`). Identity also requires the ERC-8004 env vars
(`MAGIC_AGENT_ERC8004_URI`, `MAGIC_AGENT_ERC8004_RPC`, `MAGIC_AGENT_ERC8004_KEY`).
Note: the production wiring against the SDK's real `ERC8004Agent` API is still
pending, so identity currently stays `unregistered` even with the extra
installed — this is intentional and honest, not a silent failure.

### Local dev: editable sibling scanner

Workspace devs who want the local editable scanner can override after sync:

```bash
uv sync
uv pip install -e ../trading-scanner   # point magic_scanner at the local checkout
```

Alternatively, temporarily swap the committed `[tool.uv.sources]` entry for the
commented `path = "../trading-scanner", editable = true` form in
`pyproject.toml` — but do **not** commit that; the committed source must remain
the git-pin so the deployable install stays self-contained.

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
