# Rollout Runbook — paper → testnet → live

This runbook covers the **staged rollout** from paper simulation through Aster testnet to
small-notional live trading. It complements `deploy/README.md`, which documents install,
packaging, and VPS setup — read and follow that guide first. This document starts where
installation ends.

**Principle:** advance only on green smoke + explicit go/no-go. Never skip a stage.

---

## Overview — the three stages

| Stage | Executor | Funds at risk | Purpose |
|---|---|---|---|
| 1 — Paper | `PaperExecutor` | None (simulated, starting equity 1 000 USDT) | Validate the full stack end-to-end with zero financial exposure |
| 2 — Testnet | `AsterRestExecutor` + `ASTER_TESTNET` | None (testnet keys / no real value) | Validate Aster signing, order routing, and fill reconciliation on real infra |
| 3 — Live | `AsterRestExecutor` (production) | Real funds — start small | First live orders; conservative risk, kill-switch conservatively set |

Each stage requires its own smoke checklist pass. A **go/no-go gate** at the end of each
stage must be explicitly cleared before proceeding to the next.

---

## Stage 1 — Paper

### 1.1 Start the services

**Via systemd (production VPS, recommended):**

```bash
sudo systemctl start magic-agent-run.service
sudo systemctl start magic-agent-serve.service
sudo systemctl start magic-agent-web.service
```

Confirm all three are active:

```bash
sudo systemctl status magic-agent-run magic-agent-serve magic-agent-web
```

The default `ExecStart` in `magic-agent-run.service` already passes `--executor paper
--symbol BNB/USDT` — no edits required for Stage 1.

**Or directly (local dev / quick validation):**

```bash
# Terminal 1 — poll loop (paper, no funds)
uv run magic-agent run --executor paper --symbol BNB/USDT

# Terminal 2 — read-only API
uv run magic-agent serve --host 127.0.0.1 --port 8000
```

`--executor paper` runs `PaperExecutor(starting_equity=1000.0)` — sign-aware simulated
PnL, no network calls, no funds.

---

### 1.2 Smoke Checklist (Stage 1 — Paper)

Work through every item in order. Record the timestamp and result for each check.

---

#### CHECK 1 — Poll loop starts

**Command:**

```bash
sudo journalctl -u magic-agent-run -n 50 --no-pager
```

(Or `tail -f` the log if running directly.)

**Expected result:** Log lines indicating the loop is alive (a periodic poll / candle
/ status-write message — exact wording depends on the log config). The process should
not exit or throw an unhandled exception.

---

#### CHECK 2 — Status snapshot updates

**Command (check mtime advances across two polls):**

```bash
stat /opt/midas/.magic_agent/status.json
# wait for at least one NEW CLOSED CANDLE, then:
stat /opt/midas/.magic_agent/status.json
```

**Cadence — important:** the loop *polls* every ~15 s, but it only writes new state when
a **5-minute candle closes** (it drops the forming bar and dedupes on candle timestamp).
So the snapshot/log advance roughly every ~5 minutes, NOT every poll — allow up to ~5–6
minutes between the two checks. Re-checking after only ~60 s will show no change on a
healthy system; that is expected, not a failure.

**Expected result:** The `Modify` timestamp advances across a 5m-candle boundary.

**Command (verify API surface):**

```bash
curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool
```

**Expected result:** HTTP 200, valid JSON object containing at minimum `halted`,
`daily_loss`, and `max_daily_loss`. (The snapshot has no embedded timestamp field —
freshness is the file mtime checked above.) The `halted` field must be `false` in a
fresh paper session with no simulated losses.

---

#### CHECK 3 — Decision log writes

**Command (confirm file grows):**

```bash
wc -l /opt/midas/.magic_agent/decisions.jsonl
# wait for at least one new closed candle (~5 min, see CHECK 2 cadence note), then:
wc -l /opt/midas/.magic_agent/decisions.jsonl
```

**Expected result:** Line count increases (or file is created and non-empty) after at
least one closed candle is processed. (Same ~5-minute write cadence as CHECK 2 — a
60-second wait will usually show no change on a healthy system.)

**Command (verify API surface):**

```bash
curl -s http://127.0.0.1:8000/api/decisions | python3 -m json.tool
```

**Expected result:** HTTP 200, valid JSON array (may be empty on a fresh deploy with no
signals yet — that is acceptable).

---

#### CHECK 4 — API responds (both endpoints, 200 + JSON)

```bash
curl -o /dev/null -s -w "%{http_code}" http://127.0.0.1:8000/api/status
# expected: 200

curl -o /dev/null -s -w "%{http_code}" http://127.0.0.1:8000/api/decisions
# expected: 200
```

**Expected result:** Both commands print `200`.

---

#### CHECK 5 — Dashboard reads the API

**Precondition:** `NEXT_PUBLIC_API_BASE` in `/opt/midas/.env` must point at the correct
API base URL (e.g. `https://your-domain.example.com/api` for a VPS, or
`http://127.0.0.1:8000/api` for local validation). The Next.js build bakes this value
in at build time — if you change it you must rebuild (`cd /opt/midas/web && npm run
build`).

**Command (via nginx proxy on VPS):**

```bash
curl -o /dev/null -s -w "%{http_code}" https://your-domain.example.com/
# expected: 200

curl -o /dev/null -s -w "%{http_code}" https://your-domain.example.com/api/status
# expected: 200
```

**Expected result:** Both return 200. Load the dashboard URL in a browser and confirm
the status panel reflects real data (not `undefined` or stale values). The API calls
visible in the browser network panel must target the URL in `NEXT_PUBLIC_API_BASE`.

---

#### CHECK 6 — Advisor key-gating behaves

The advisor is active **only when both** `--advisor` is passed **and**
`MAGIC_AGENT_LLM_API_KEY` is set. Two sub-checks:

**Sub-check A — No key → deterministic mode (default, safe):**

Ensure `MAGIC_AGENT_LLM_API_KEY` is absent from the environment (the default `.env`
leaves it blank). Restart the run service and check the log:

```bash
sudo systemctl restart magic-agent-run
sudo journalctl -u magic-agent-run -n 30 --no-pager
```

**Expected result:** No log lines mentioning LLM or advisor initialisation. The loop
proceeds using the pure deterministic scanner path.

**Sub-check B — `--advisor` with no key → still deterministic (factory returns None):**

Edit the run unit's `ExecStart` temporarily to add `--advisor`:

```
ExecStart=/usr/local/bin/uv run magic-agent run --executor paper --symbol BNB/USDT --advisor
```

Reload and restart (without setting `MAGIC_AGENT_LLM_API_KEY`):

```bash
sudo systemctl daemon-reload && sudo systemctl restart magic-agent-run
sudo journalctl -u magic-agent-run -n 30 --no-pager
```

**Expected result:** The log should indicate the advisor factory returned `None` (key
absent), and the loop continues on the deterministic path — no crash, no exception.
Behaviour must be identical to Sub-check A.

**Sub-check C — `--advisor` + key → advisor active (optional):**

Only perform this check if you have a valid `MAGIC_AGENT_LLM_API_KEY`. Set it in `.env`
and restart the service. Check the log for an advisor initialisation message and confirm
the loop still emits normal poll output (advisor is clamp-only — size-down/wait — and
returns `None` on any failure; the loop never crashes because of it).

Restore `ExecStart` to the default (no `--advisor`) after this sub-check.

---

#### CHECK 7 — Daily-loss kill-switch observable

The kill-switch logic in `run_live` computes realised PnL for the day and halts new
entries when `daily_loss` reaches `--max-daily-loss`. The status file carries the live
fields: `halted`, `daily_loss`, `max_daily_loss`.

**Normal state (no losses):**

```bash
curl -s http://127.0.0.1:8000/api/status | python3 -c "
import sys, json; s = json.load(sys.stdin)
print('halted:', s.get('halted'), '| daily_loss:', s.get('daily_loss'), '| max_daily_loss:', s.get('max_daily_loss'))
"
```

**Expected result:** `halted: False`, `daily_loss: 0.0` (or a small negative paper
value), `max_daily_loss: 50.0` (default, or whatever was passed via `--max-daily-loss`).

**Exercising the kill-switch in paper mode:**

The `PaperExecutor` tracks simulated PnL. To observe the kill-switch trip:

1. Pass a very conservative `--max-daily-loss` that the simulated session will breach
   quickly (e.g. `--max-daily-loss 0.01` on a dev restart). This is a test-only flag
   change — do NOT use a near-zero daily loss limit in production.
2. Let the loop process one or more **closed candles** (~5 min each — see CHECK 2
   cadence note) that generate a simulated loss exceeding the limit.
3. Poll the API and inspect `halted`:

```bash
curl -s http://127.0.0.1:8000/api/status | python3 -c "
import sys, json; s = json.load(sys.stdin)
print('halted:', s.get('halted'))
"
```

**Expected result:** `halted: True` once the simulated `daily_loss` exceeds
`max_daily_loss`. The dashboard status pill should reflect `halted`.

Note: in paper mode, the conditions that produce a large-enough simulated loss depend
on market signals. If signals remain quiet, use the `--max-daily-loss 0.01` override as
described above just for this test, then restore the real default (`50.0`) before
advancing to Stage 2.

Policy is **fail-closed**: the live path always receives a non-empty policy config from
the CLI flags. An empty policy config raises — this cannot happen by accident.

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

**If any item is red: do not advance. Investigate and fix before proceeding.**

---

## Stage 2 — Testnet

### 2.1 Testnet preconditions — verify BEFORE starting

The ccxt `aster` class implements the **V3 wallet-signed path** (EIP-712, not HMAC).
Two items must be verified manually before placing any testnet order:

**Precondition A — Testnet base URL override:**

ccxt does not ship the Aster testnet base URL — `ex.urls['test']` is `None` and
`ex.set_sandbox_mode(True)` does NOT work out of the box. The executor must override
`ex.urls['api']` to the testnet hosts (`https://fapi.asterdex-testnet.com`) manually.
Confirm the Aster executor in the codebase performs this override when `ASTER_TESTNET`
is set. If it does not, stop here and fix the executor before continuing.

**Precondition B — chainId reconciliation:**

ccxt hardcodes `v3ChainId: 1666` in its `options`. The Aster testnet EIP-712 example
uses `chainId: 714`. Signatures with the wrong `chainId` will be rejected by the
testnet. KNOWN PENDING: the executor today overrides only the base URL (`urls['api']`)
and does NOT yet set `v3ChainId` — the code carries an explicit "reconcile chainId 1666
vs 714 before signing on testnet" TODO. You MUST reconcile it (likely by setting
`ex.options['v3ChainId']` to the testnet value) and confirm a minimal testnet order is
ACCEPTED (see 2.3) before advancing. Treat an unreconciled chainId as a hard blocker.

Source for both: `docs/superpowers/specs/2026-06-15-venue-spike-findings.md` §8.2.

---

### 2.2 Testnet environment setup

Set the following in `/opt/midas/.env`:

```bash
ASTER_PRIVATE_KEY=<your-testnet-signer-private-key>
ASTER_WALLET_ADDRESS=<your-testnet-main-wallet-address>   # optional; executor may derive it
ASTER_TESTNET=1      # any non-empty value activates the testnet base URL override
```

Obtain testnet credentials at `https://www.asterdex-testnet.com/en/api-wallet`.

Edit the run unit `ExecStart` to switch executor:

```
ExecStart=/usr/local/bin/uv run magic-agent run --executor aster --symbol BNB/USDT
```

Start conservatively — use a tight daily-loss limit for testnet:

```
ExecStart=/usr/local/bin/uv run magic-agent run --executor aster --symbol BNB/USDT --max-daily-loss 5.0
```

Reload and start:

```bash
sudo systemctl daemon-reload
sudo systemctl restart magic-agent-run
```

---

### 2.3 Testnet order verification

Allow the poll loop to run until a signal fires and an order is placed, or trigger a
minimal test order through a dev/test harness if the strategy provides one. Then:

**Check the executor placed an order:**

```bash
sudo journalctl -u magic-agent-run -n 100 --no-pager | grep -iE "order|fill|aster|exec"
```

**Expected result:** Log lines showing an order placed and a response from the Aster
testnet API. Confirm no `chainId` or signing errors appear.

**Reconcile the fill:**

Log in to the Aster testnet UI (`https://www.asterdex-testnet.com`) and confirm the
order appears in the order history. The executor's reported fill should match.

---

### 2.4 Smoke Checklist (Stage 2 — Testnet)

Re-run the full Stage 1 smoke checklist (CHECKs 1–7) against the testnet deployment.
In addition:

**CHECK 2T — Status reflects testnet executor:**

```bash
curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool
```

Confirm the status object reflects the live-venue session — `mode` is `live` (the
non-paper executor) and `venue` is set — and that `halted` is `false` before any loss
is accumulated.

**CHECK 6T — Advisor key-gating on testnet:**

Same as Stage 1 CHECK 6. The advisor constraint (clamp-only, `None`-on-failure) applies
identically on testnet.

---

### 2.5 Go/No-Go Gate — Stage 2 to Stage 3

All of the following must be true before advancing:

- [ ] Precondition A: testnet base URL override confirmed in executor code
- [ ] Precondition B: chainId reconciled; at least one testnet order accepted without signing error
- [ ] Full Stage 1 smoke checklist (CHECKs 1–7) re-passed against testnet
- [ ] Testnet order placed and reconciled against Aster testnet UI
- [ ] Kill-switch (`halted`) observable on testnet
- [ ] Rollback procedure rehearsed (see §5 below) — at least one manual stop + restart cycle completed

**If any item is red: do not advance to live. Real funds are at risk in Stage 3.**

---

## Stage 3 — Small-notional live

### 3.1 Preconditions

All of the following must be satisfied before going live:

- Stage 1 (paper) and Stage 2 (testnet) smoke checklists fully green.
- Rollback procedure rehearsed at least once on testnet.
- `ASTER_PRIVATE_KEY` and `ASTER_WALLET_ADDRESS` set in `.env` for the **production**
  Aster account (NOT the testnet key).
- `ASTER_TESTNET` is **unset or empty** — any non-empty value activates the testnet
  path, which would use the testnet base URL with your production key. Verify:

  ```bash
  grep ASTER_TESTNET /opt/midas/.env
  # must be absent, blank, or commented out
  ```

### 3.2 Start with conservative limits

Edit the run unit `ExecStart` for live:

```
ExecStart=/usr/local/bin/uv run magic-agent run \
  --executor aster \
  --symbol BNB/USDT \
  --risk-pct 0.005 \
  --leverage 1.0 \
  --max-daily-loss 20.0 \
  --require-stop
```

Start with `--risk-pct 0.005` (half the default 1%) and `--max-daily-loss 20.0` (below
the 50 USDT default) for the first live session. Increase only after confirming fills
reconcile and the kill-switch is reachable.

`--require-stop` is on by default — keep it. It ensures every entry has a stop.

`--leverage 1.0` is the default. Do not increase until you have confirmed fills and PnL
accounting are correct.

### 3.3 Monitor

```bash
sudo journalctl -u magic-agent-run   -f    # poll loop + executor output
sudo journalctl -u magic-agent-serve -f    # API
sudo systemctl status magic-agent-run magic-agent-serve magic-agent-web

# Watch status live
watch -n 10 "curl -s http://127.0.0.1:8000/api/status | python3 -c \
  \"import sys,json; s=json.load(sys.stdin); print('halted:', s.get('halted'), '| daily_loss:', s.get('daily_loss'))\""
```

---

## Rollback (REQ-018)

### Stopping safely

To halt new entries immediately:

```bash
sudo systemctl stop magic-agent-run
```

Stopping `magic-agent-run` prevents the poll loop from opening new entries. Open
positions on Aster are **not** automatically closed — the executor does not issue a
close-all on shutdown. Manage open positions manually.

`magic-agent-serve` and `magic-agent-web` can remain running — they are read-only and
harmless. The API and dashboard will serve the last written `status.json` and
`decisions.jsonl`.

### Open positions

After stopping the run service, log in to the Aster UI (or use the Aster API directly)
to inspect and flatten any open positions if desired. The agent will not re-enter them
when restarted.

### Revert to paper

To revert to paper executor without touching live credentials:

1. Stop the run service: `sudo systemctl stop magic-agent-run`
2. Edit the run unit `ExecStart` back to paper:
   ```
   ExecStart=/usr/local/bin/uv run magic-agent run --executor paper --symbol BNB/USDT
   ```
3. Reload and restart:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start magic-agent-run
   ```
4. Re-run the Stage 1 smoke checklist to confirm the paper session is healthy.

### Safe-failure expectations

These are deliberate safe-degradation behaviours, not bugs:

| Failure | Behaviour |
|---|---|
| Transient network error during a poll | Current poll is skipped; the loop logs the error and waits for the next poll interval. No crash, no kill of open positions. |
| Advisor LLM call fails (any reason) | Advisor returns `None`; the loop proceeds on the deterministic scanner path. Never crashes the loop. |
| CMC context fetch fails | Context degrades to `status="unavailable"`; the deterministic scanner path remains authoritative. The loop does not block or halt. |
| ERC-8004 identity unavailable | `agent_id` is `None`; dashboard shows `unregistered`. Not a blocker for trading. |
| `--advisor` flag set but `MAGIC_AGENT_LLM_API_KEY` absent | Advisor factory returns `None`; loop runs pure deterministic. Identical to advisor-off mode. |

### Logs for diagnosis

```bash
sudo journalctl -u magic-agent-run   -n 200 --no-pager    # poll loop
sudo journalctl -u magic-agent-serve -n 100 --no-pager    # API
sudo journalctl -u magic-agent-web   -n 50  --no-pager    # dashboard
```

---

## Best-effort / External-boundary notes

The following subsystems have **known limitations or pending wiring** as of this phase.
They are documented here honestly rather than hidden.

### ERC-8004 identity (PENDING)

Production wiring against the `ERC8004Agent` SDK API is **pending**. Even with the
`[identity]` extra installed (`uv sync --extra identity`) and all three
`MAGIC_AGENT_ERC8004_*` env vars set, the dashboard will show `unregistered`. This is
intentional and honest — `cli._resolve_agent_id()` returns `None` when the wiring is
absent, and never fabricates an ID. **This is not a blocker for trading.** No smoke
checklist item depends on identity resolution.

### CMC context (OBSERVE-ONLY)

The CoinMarketCap client is wired for observe-only mode. When configured (key present),
CMC Fear & Greed data is fetched and logged. When unconfigured, context degrades to
`status="unavailable"`. In both cases, the CMC reading **does not gate trades** — the
deterministic scanner path is authoritative, and the CMC-to-`regime`/`risk_flag` mapping
is a deliberate deferred policy decision. "CMC unavailable" is a full passthrough (no
veto, no boost).

### LLM advisor (OPTIONAL, OFF BY DEFAULT)

The advisor is clamp-only (size-down / wait — it can never un-veto, flip direction, or
increase size). It is active only when both `--advisor` is passed and
`MAGIC_AGENT_LLM_API_KEY` is set. It returns `None` on any failure. Deterministic mode
is the default and the safe baseline.

### Aster venue layer (LEAST-PROVEN BOUNDARY)

The Aster executor is the least-proven boundary in the stack:

- **Testnet base URL** must be overridden manually in the executor (`ASTER_TESTNET` env
  var triggers this); ccxt does not ship the testnet URL natively.
- **chainId mismatch**: ccxt hardcodes `v3ChainId: 1666`; the Aster testnet EIP-712
  examples use `chainId: 714`. This must be reconciled in the executor before testnet
  signing will succeed. Verify in Stage 2 before advancing to live.
- **Builder fee**: ccxt's `options` include `builderFee: True` and `builderRate: 0.001`
  by default. Review and disable in the executor config if undesired.
- **Real funds**: Stage 3 uses real funds. Do not proceed until Stage 2 is fully green.

Source: `docs/superpowers/specs/2026-06-15-venue-spike-findings.md` §8.1–§8.2.

---

## Cross-references

- `deploy/README.md` — VPS install, packaging, systemd units, nginx setup (read this first).
- `deploy/.env.example` — full env var reference with inline comments.
- `deploy/systemd/` — `magic-agent-run.service`, `magic-agent-serve.service`, `magic-agent-web.service`.
- `deploy/nginx.conf.example` — nginx reverse-proxy template.
- `docs/superpowers/specs/2026-06-15-venue-spike-findings.md` — Aster venue findings (chainId, testnet URL, ccxt fit).
- Root `README.md` — scanner pinning, identity extra, CMC, shared state.
