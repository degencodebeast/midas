# Rollout Runbook — paper → supervised canary → unattended live

This runbook covers the **staged rollout** from paper simulation through a supervised
single-cycle live canary to unattended live trading. It complements `deploy/README.md`,
which documents install, packaging, and VPS setup — read and follow that guide first.
This document starts where installation ends.

**Principle:** advance only on green smoke + explicit go/no-go. Never skip a stage.

> **Executor note:** there are exactly TWO executors — `paper` (no funds, default) and
> `twak` (live, real BSC swaps). There is NO `aster` executor. The canonical live unit
> is `deploy/midas-agent.service`; the paper-only unit is
> `deploy/systemd/magic-agent-run.service`.

---

## Overview — the three stages

| Stage | Executor | Funds at risk | Purpose |
|---|---|---|---|
| 1 — Paper | `--executor paper` | None (simulated) | Validate the full stack end-to-end with zero financial exposure |
| 2 — Supervised live canary | `--executor twak --live-frames --live-cmc --max-iters 1` | Real funds — single cycle, operator watching | Confirm real TWAK signing, on-chain fill, and reconciliation before enabling the loop |
| 3 — Unattended live loop | canonical `midas-agent.service` | Real funds | Autonomous loop paced to closed H1 bars |

Each stage requires its own smoke checklist pass. A **go/no-go gate** at the end of
each stage must be explicitly cleared before proceeding to the next.

---

## Stage 1 — Paper

### 1.1 Start the services

**Via systemd (recommended on VPS):**

The paper-only unit is `deploy/systemd/magic-agent-run.service`. Its `ExecStart`
already passes `--executor paper` — no edits required for Stage 1.

```bash
sudo systemctl start magic-agent-run.service
sudo systemctl start magic-agent-serve.service
sudo systemctl start magic-agent-web.service
```

Confirm all three are active:

```bash
sudo systemctl status magic-agent-run magic-agent-serve magic-agent-web
```

**Or directly (local dev / quick validation):**

```bash
# Terminal 1 — poll loop (paper, no funds)
uv run magic-agent run --executor paper

# Terminal 2 — read-only API
uv run magic-agent serve --host 127.0.0.1 --port 8000
```

`--executor paper` runs `PaperExecutor(starting_equity=1000.0)` — sign-aware
simulated PnL, no network calls, no funds.

> **Per-mode paths:** paper writes `.magic_agent/paper/status.json` (and
> `decisions.jsonl`). The serve unit must point `--status` at that path for Stage 1.
> The live unit writes `.magic_agent/twak/*` — see `deploy/README.md` §CRITICAL.

---

### 1.2 Smoke Checklist (Stage 1 — Paper)

Work through every item in order. Record the timestamp and result for each check.

---

#### CHECK 1 — Poll loop starts

**Command:**

```bash
sudo journalctl -u magic-agent-run -n 50 --no-pager
```

**Expected result:** Log lines indicating the loop is alive. The process should not
exit or throw an unhandled exception.

---

#### CHECK 2 — Status snapshot updates

**Command (check mtime advances across two polls):**

```bash
stat /opt/midas/.magic_agent/paper/status.json
# wait for at least one NEW CLOSED CANDLE (~5 min), then:
stat /opt/midas/.magic_agent/paper/status.json
```

**Cadence:** the loop polls every ~15 s but only writes new state when a **5-minute
candle closes**. Allow up to ~5–6 minutes between the two checks.

**Command (verify API surface):**

```bash
curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool
```

**Expected result:** HTTP 200, valid JSON object containing at minimum `halted`,
`daily_loss`, and `max_daily_loss`. The `halted` field must be `false` in a fresh
paper session with no simulated losses.

---

#### CHECK 3 — Decision log writes

**Command:**

```bash
wc -l /opt/midas/.magic_agent/paper/decisions.jsonl
# wait for at least one new closed candle (~5 min), then:
wc -l /opt/midas/.magic_agent/paper/decisions.jsonl
```

**Expected result:** line count increases after at least one closed candle.

**Command (verify API surface):**

```bash
curl -s http://127.0.0.1:8000/api/decisions | python3 -m json.tool
```

**Expected result:** HTTP 200, valid JSON array (may be empty on a fresh deploy).

---

#### CHECK 4 — API responds (both endpoints, 200 + JSON)

```bash
curl -o /dev/null -s -w "%{http_code}" http://127.0.0.1:8000/api/status
# expected: 200

curl -o /dev/null -s -w "%{http_code}" http://127.0.0.1:8000/api/decisions
# expected: 200
```

---

#### CHECK 5 — Dashboard reads the API

**Precondition:** `NEXT_PUBLIC_API_BASE` in `/opt/midas/.env` must point at the
correct API base URL. The Next.js build bakes this value in at build time — if you
change it you must rebuild (`cd /opt/midas/web && npm run build`).

```bash
curl -o /dev/null -s -w "%{http_code}" https://your-domain.example.com/
# expected: 200

curl -o /dev/null -s -w "%{http_code}" https://your-domain.example.com/api/status
# expected: 200
```

**Expected result:** Both return 200. Load the dashboard in a browser and confirm the
status panel reflects real data.

---

#### CHECK 6 — Advisor key-gating behaves

**Sub-check A — No key → deterministic mode (default, safe):**

Ensure `MAGIC_AGENT_LLM_API_KEY` is absent from the environment. Restart and check
the log:

```bash
sudo systemctl restart magic-agent-run
sudo journalctl -u magic-agent-run -n 30 --no-pager
```

**Expected:** no log lines mentioning LLM or advisor initialisation; deterministic
scanner path only.

**Sub-check B — `--advisor` with no key → still deterministic:**

Temporarily add `--advisor` to the paper unit's `ExecStart` (without setting the key):

```bash
sudo systemctl daemon-reload && sudo systemctl restart magic-agent-run
sudo journalctl -u magic-agent-run -n 30 --no-pager
```

**Expected:** log indicates advisor factory returned `None`; loop continues on the
deterministic path, no crash.

Restore `ExecStart` to the default (no `--advisor`) after this check.

---

#### CHECK 7 — Daily-loss kill-switch observable

```bash
curl -s http://127.0.0.1:8000/api/status | python3 -c "
import sys, json; s = json.load(sys.stdin)
print('halted:', s.get('halted'), '| daily_loss:', s.get('daily_loss'), '| max_daily_loss:', s.get('max_daily_loss'))
"
```

**Expected result:** `halted: False`, `daily_loss: 0.0` (or a small negative paper
value).

To exercise the kill-switch: pass `--max-daily-loss 0.01` as a test-only flag; let
the loop process one or more closed candles; confirm `halted: True` when the
simulated daily loss exceeds the limit. Restore the real default before advancing.

---

### 1.3 Go/No-Go Gate — Stage 1 to Stage 2

All of the following must be true before advancing:

- [ ] CHECK 1: poll loop starts without errors
- [ ] CHECK 2: status.json mtime advances; `/api/status` returns 200 + valid JSON
- [ ] CHECK 3: decisions.jsonl grows; `/api/decisions` returns 200 + valid JSON
- [ ] CHECK 4: both API endpoints return HTTP 200
- [ ] CHECK 5: dashboard loads and reads the API correctly
- [ ] CHECK 6: advisor key-gating confirmed (Sub-checks A and B at minimum)
- [ ] CHECK 7: kill-switch `halted` field observed in status.json
- [ ] All activation gates in `docs/track1-spot-runbook.md` §3 are `[x]`
  (scanner pin, eligibility, gold identity, CMC snapshot, RiskPolicy, gas reserve,
  fresh quotes, empty unfinished execution set, competition registration, state backup,
  paper cycle, explicit operator approval)
- [ ] Rollback procedure rehearsed (§ Rollback below) — at least one manual stop +
  restart cycle completed

**If any item is red: do not advance. Investigate and fix before proceeding.**

---

## Stage 2 — Supervised live canary

### 2.1 Preconditions — verify BEFORE starting

- Stage 1 smoke checklist fully green.
- All activation gates in `docs/track1-spot-runbook.md` §3 confirmed `[x]`.
- `/etc/midas/agent.env` is root-owned, `chmod 600`, and contains all six required
  live env vars (see § Environment below).
- TWAK wallet funded (BNB gas reserve + USDC/USDT trading capital).
- Node ≥ 20.19 on the VPS (`node --version`); Node 24 recommended — distro default
  Node 18 crashes the TWAK CLI with `ERR_REQUIRE_ESM`.
- `deploy/twak-vps-bringup.sh` quote-only smoke has passed on the VPS.
- `deploy/docker-smoke.sh` has passed (clean Linux container proves private scanner
  clone + paper runtime + TWAK-on-Node-24 before the VPS).

### 2.2 Environment — `/etc/midas/agent.env`

The canonical live unit reads secrets from `/etc/midas/agent.env` (never from
`/opt/midas/.env`). All six vars are required; the live path fails closed if any is
absent or empty (`_require_live_env` in `app.py`):

| Var | Purpose |
|---|---|
| `TWAK_ACCESS_ID` | TWAK API access id |
| `TWAK_HMAC_SECRET` | TWAK HMAC secret (KEEP SECRET) |
| `TWAK_WALLET_PASSWORD` | TWAK wallet password (KEEP SECRET) |
| `BSC_RPC_URL` | BSC mainnet JSON-RPC endpoint |
| `CMC_API_KEY` | CoinMarketCap API key (`--live-cmc` fails closed without it) |
| `WALLET_ADDRESS` | TWAK wallet address |

```bash
sudo mkdir -p /etc/midas
sudo cp /opt/midas/deploy/.env.example /etc/midas/agent.env
sudo chown root:root /etc/midas/agent.env && sudo chmod 600 /etc/midas/agent.env
sudo nano /etc/midas/agent.env    # fill in all six vars
```

### 2.3 Run the supervised canary

The canary is a single cycle under direct operator supervision. `--max-iters 1` causes
the loop to run exactly one closed-H1-bar cycle and exit.

```bash
uv run magic-agent run --executor twak --live-frames --live-cmc --max-iters 1
```

`--live-frames --live-cmc` are REQUIRED for live: without them the loop scans only the
committed offline ZEC fixture instead of the real Track-1 universe.

**Canary observation sequence (from `docs/track1-spot-runbook.md` § Supervised live canary gate):**

1. Confirm a scanner-authorized setup exists.
2. Confirm RiskPolicy caps the first live entry to `canary_margin_fraction = 0.05` (5 % of equity).
3. Confirm TWAK quote is fresh and exact-size.
4. Confirm TWAK submit returns a transaction hash.
5. Confirm the chain receipt reaches the required confirmations.
6. Confirm balance-delta reconciliation books the position.
7. Confirm the position appears in status and journals as confirmed and reconciled.

If any step fails, stop. Do not promote to unattended loop.

### 2.4 Go/No-Go Gate — Stage 2 to Stage 3

- [ ] Canary cycle completed without errors
- [ ] TWAK submission confirmed on BSC (transaction hash + required confirmations)
- [ ] Balance-delta reconciliation booked the position correctly
- [ ] Status and journals reflect a confirmed, reconciled position
- [ ] Kill-switch (`halted`) observable via API
- [ ] Rollback rehearsed: manual stop + restart confirmed

**If any item is red: do not enable the unattended loop. Real funds are at risk in Stage 3.**

---

## Stage 3 — Unattended live loop

### 3.1 Pre-unattended caveats (from `docs/track1-spot-runbook.md` § Known limitations)

Two items remain open before fully autonomous operation is safe:

1. **Sell idempotency:** the TWAK sell path has no execution-journal idempotency record
   for a lost-response edge. A sell broadcast whose response is lost must map to a safe
   terminal state so a protective exit is not re-broadcast on the next cycle.
2. **Chain-rebuild on restart:** live restart still loads the paper position store.
   Before unsupervised operation, open-position truth must be rebuilt from reconciled
   execution evidence + chain balances (`rebuild_positions_from_chain`) rather than
   `positions.json`, so a restart cannot re-enter a position already held on-chain.

Until both items are closed: run only on ONE machine; if you must move to a new
machine or VPS, copy `.magic_agent/twak/` before restarting. **Supervised
single-cycle live (`--max-iters 1`) is safe. Unattended looping requires both items.**

### 3.2 Install and enable the canonical live unit

```bash
# Install the canonical live unit (if not done in deploy/README.md Step 5):
sudo cp /opt/midas/deploy/midas-agent.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start:
sudo systemctl enable --now midas-agent.service
sudo systemctl enable --now magic-agent-serve.service
sudo systemctl enable --now magic-agent-web.service
```

Confirm all three are running:

```bash
sudo systemctl status midas-agent magic-agent-serve magic-agent-web
```

The canonical live unit (`deploy/midas-agent.service`) runs:

```
ExecStart=/opt/midas/.venv/bin/magic-agent run --executor twak --live-frames --live-cmc
EnvironmentFile=/etc/midas/agent.env
WorkingDirectory=/opt/midas
```

It writes state under `/opt/midas/.magic_agent/twak/` (status.json, decisions.jsonl,
state.json). `magic-agent-serve.service` must have `--status .magic_agent/twak/status.json`
to read the live snapshot — see `deploy/README.md` §CRITICAL.

### 3.3 Risk model reference

The live loop uses `RiskConfig.defaults()` — **fixed-MARGIN sizing** (the scanner
stop is exit geometry only, not the sizing input):

| Parameter | Value |
|---|---|
| Canary margin cap | 5 % of equity |
| A-grade margin | 5 % of equity |
| B-grade margin | 2.5 % of equity |
| Counter-bias multiplier | × 0.50 (requires positive top-quartile 7-day momentum) |
| Max concurrent positions | 3 |
| Max open risk (stop-derived ceiling) | 6 % of equity |
| Max correlation bucket risk | 6 % of equity |
| Stable reserve | 1.5 % of equity |
| Daily loss halt | 10 % of daily-anchor equity |
| Consecutive stop halt | 3 stops |
| **Graduated drawdown ladder:** | |
| ≥ 5 % drawdown | Throttle: margin × 0.50, max 2 positions (A+B) |
| ≥ 10 % drawdown | Defense: margin × 0.25, A-grade only, max 1 position |
| ≥ 15 % drawdown | Entry halt (exits continue unconditionally) |
| ≥ 20 % drawdown | Hard review: entries denied, operator intervention required |
| ≥ 30 % drawdown | Hard disqualification: all entries permanently denied until review |

### 3.4 Monitor

```bash
sudo journalctl -u midas-agent       -f    # live poll loop + executor output
sudo journalctl -u magic-agent-serve -f    # API
sudo systemctl status midas-agent magic-agent-serve magic-agent-web

# Watch status live
watch -n 10 "curl -s http://127.0.0.1:8000/api/status | python3 -c \
  \"import sys,json; s=json.load(sys.stdin); print('halted:', s.get('halted'), '| daily_loss:', s.get('daily_loss'))\""
```

---

## Rollback

### Stopping safely

```bash
sudo systemctl stop midas-agent
```

Stopping `midas-agent` prevents the poll loop from opening new entries. Open
positions on TWAK/BSC are **not** automatically closed — the executor does not issue
a close-all on shutdown. Manage open positions manually (via `twak swap` or the TWAK
UI).

`magic-agent-serve` and `magic-agent-web` can remain running — they are read-only
and harmless.

### Open positions

After stopping the run service, inspect the live state:

```bash
cat /opt/midas/.magic_agent/twak/status.json
cat /opt/midas/.magic_agent/twak/decisions.jsonl | tail -20
```

To manually flatten a position:

```bash
twak swap <qty> <token-contract> USDC --chain bsc --yes
```

### Revert to paper

1. Stop the live run service: `sudo systemctl stop midas-agent`
2. Start the paper-only unit:
   ```bash
   sudo systemctl start magic-agent-run.service
   ```
   The paper unit (`deploy/systemd/magic-agent-run.service`) already passes
   `--executor paper` and reads `/opt/midas/.env` (no live secrets required).
3. Update serve's `--status` to `.magic_agent/paper/status.json` if switching to a
   paper dashboard.
4. Re-run the Stage 1 smoke checklist to confirm the paper session is healthy.

### Safe-failure expectations

| Failure | Behaviour |
|---|---|
| Transient network error during a poll | Current poll is skipped; loop logs the error and waits for the next poll interval. No crash, no kill of open positions. |
| Advisor LLM call fails (any reason) | Advisor returns `None`; loop proceeds on the deterministic scanner path. Never crashes the loop. |
| CMC context fetch fails | Context degrades to `status="unavailable"`; deterministic scanner path remains authoritative. Loop does not block or halt. |
| ERC-8004 identity unavailable | `agent_id` is `None`; dashboard shows `unregistered`. Not a blocker for trading. |
| `--advisor` flag set but `MAGIC_AGENT_LLM_API_KEY` absent | Advisor factory returns `None`; loop runs pure deterministic. Identical to advisor-off mode. |

### Logs for diagnosis

```bash
sudo journalctl -u midas-agent       -n 200 --no-pager    # live poll loop
sudo journalctl -u magic-agent-serve -n 100 --no-pager    # API
sudo journalctl -u magic-agent-web   -n 50  --no-pager    # dashboard
```

---

## Best-effort / External-boundary notes

### ERC-8004 identity (PENDING)

Production wiring against the `ERC8004Agent` SDK API is **pending**. Even with the
`[identity]` extra installed and all three `MAGIC_AGENT_ERC8004_*` env vars set, the
dashboard will show `unregistered`. This is not a blocker for trading.

### CMC context (OBSERVE-ONLY)

CMC Fear & Greed / rank data is fetched and logged when configured. CMC does NOT gate
trades — the deterministic scanner path is authoritative. "CMC unavailable" is a full
passthrough (no veto, no boost).

### LLM advisor (OPTIONAL, OFF BY DEFAULT)

The advisor is clamp-only (size-down / wait — it can never un-veto, flip direction,
or increase size). Active only when both `--advisor` is passed and
`MAGIC_AGENT_LLM_API_KEY` is set. Returns `None` on any failure. Deterministic mode
is the default and the safe baseline.

---

## Cross-references

- `deploy/README.md` — VPS install, packaging, systemd units, nginx setup (read this first).
- `deploy/midas-agent.service` — canonical LIVE unit (twak, `--live-frames --live-cmc`, `/etc/midas/agent.env`).
- `deploy/systemd/magic-agent-run.service` — PAPER-ONLY unit (no funds).
- `deploy/.env.example` — full env var reference with inline comments.
- `docs/track1-spot-runbook.md` — activation gates, supervised canary protocol, known live-execution gaps, emergency procedures.
- `src/magic_agent/risk_policy.py` `RiskConfig.defaults()` — authoritative risk model values.
