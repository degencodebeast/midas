# Deploying magic-agent on a VPS

This guide covers a production VPS deployment using systemd + nginx.  
For install, scanner, identity, and CMC details see the **root `README.md`**.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Linux VPS (Ubuntu 22.04+ recommended) | Debian/RHEL variants work; commands use `systemctl` |
| Python 3.11+ | `python3 --version` |
| [uv](https://docs.astral.sh/uv/) | `curl -Lsf https://astral.sh/uv/install.sh \| sh` |
| Node.js 20+ and npm | For the Next.js dashboard |
| nginx | `sudo apt install nginx`; or any reverse proxy |
| A domain name with DNS pointing at the VPS | Required for TLS via certbot |

---

## Deployment topology

```
Internet
   │  (HTTPS :443)
   ▼
nginx  ──── TLS termination ─────────────────────────────────────────
   │                                                                 │
   │  /api/*                                          /*            │
   ▼                                                  ▼             │
magic-agent-serve                          magic-agent-web          │
127.0.0.1:8000 (FastAPI)                  127.0.0.1:3000 (Next.js) │
   │                                                                 │
   │  reads                                                          │
   ▼                                                                 │
.magic_agent/twak/status.json     ◄── midas-agent (live, writes) ───┘
.magic_agent/twak/decisions.jsonl     (paper writes .magic_agent/paper/*)

ONLY nginx is publicly reachable.  serve and web bind to 127.0.0.1.
```

---

## CRITICAL: shared working directory + per-mode status path

`magic-agent run` **writes** a **per-mode** snapshot — live (twak) under
`.magic_agent/twak/status.json` (+ `decisions.jsonl`), paper under
`.magic_agent/paper/status.json`.  
`magic-agent serve` **reads** the snapshot named by its `--status` flag to power
`/api/status` (and the decision log via `--log`).

**Both processes must run with the same `WorkingDirectory`** (`/opt/midas`) **and** the
serve unit's `--status` must point at the SAME per-mode path the run loop writes
(`.magic_agent/twak/status.json` for the live unit). If either diverges, the API silently
reverts to the demo fallback — this is the number-one VPS deployment footgun. The
systemd units enforce `WorkingDirectory=/opt/midas`, and the serve unit pins
`--status .magic_agent/twak/status.json` to match the canonical live run unit.

`.magic_agent/` is gitignored and must persist across restarts. Do not place it on
a tmpfs or ephemeral volume.

---

## Step 1 — Check out the repo

```bash
sudo mkdir -p /opt/midas
sudo chown midas:midas /opt/midas       # replace 'midas' with your service user
git clone https://github.com/your-org/midas /opt/midas
cd /opt/midas
```

---

## Step 2 — Install Python dependencies

```bash
uv sync                          # magic-agent + commit-pinned scanner
# Optional: on-chain identity (currently stays 'unregistered' — see README.md)
# uv sync --extra identity
```

---

## Step 3 — Configure environment

Do this **before** building the web dashboard (Step 4) — `NEXT_PUBLIC_API_BASE` is baked
into the build.

There are TWO env files. The paper run/serve/web units read `/opt/midas/.env`; the
**canonical LIVE unit** (`deploy/midas-agent.service`) reads a separate root-owned
`/etc/midas/agent.env`.

```bash
# Paper/serve/web env (NEXT_PUBLIC_API_BASE etc.):
cp /opt/midas/deploy/.env.example /opt/midas/.env
nano /opt/midas/.env

# LIVE secrets (only for the canonical twak unit) — keep OUT of /opt/midas:
sudo mkdir -p /etc/midas
sudo cp /opt/midas/deploy/.env.example /etc/midas/agent.env
sudo chown root:root /etc/midas/agent.env && sudo chmod 600 /etc/midas/agent.env
sudo nano /etc/midas/agent.env
```

The LIVE unit fails closed unless ALL SIX live vars are present and non-empty (these are
exactly what `app.py` `_require_live_env` requires):

| Var | Decision |
|---|---|
| `TWAK_ACCESS_ID` | REQUIRED for `--executor twak` — TWAK API access id |
| `TWAK_HMAC_SECRET` | REQUIRED for live — TWAK HMAC secret (KEEP SECRET) |
| `TWAK_WALLET_PASSWORD` | REQUIRED for live — TWAK wallet password (KEEP SECRET) |
| `BSC_RPC_URL` | REQUIRED for live — BSC mainnet JSON-RPC endpoint |
| `CMC_API_KEY` | REQUIRED for live (`--live-cmc` fails closed without it); rank/momentum only |
| `WALLET_ADDRESS` | REQUIRED for live — the TWAK wallet address |
| `NEXT_PUBLIC_API_BASE` | `/opt/midas/.env` only — `https://your-domain.example.com/api` (baked into the web build — see Step 4) |

> **`.env` format matters.** Both files are consumed verbatim by systemd's
> `EnvironmentFile=`, which does **not** strip inline comments — for `KEY=value # note`
> the `# note` becomes part of the value. Keep every line a clean `KEY=value` (no
> `export`, no quotes; the comments in `.env.example` live on their own `#` lines for
> exactly this reason).

---

## Step 4 — Build the web dashboard

`NEXT_PUBLIC_API_BASE` is a **build-time** value: Next.js inlines it into the browser
bundle during `npm run build`. It must be present in the environment **before** you
build — the web unit's `EnvironmentFile` only sets the server process env and does **not**
change an already-built client bundle. Source the `.env` from Step 3 into the build shell:

```bash
cd /opt/midas/web
set -a; . /opt/midas/.env; set +a   # exports NEXT_PUBLIC_API_BASE (and the rest) for the build
npm ci
npm run build
cd /opt/midas
```

> **Rebuild on change:** any time you change `NEXT_PUBLIC_API_BASE`, re-run `npm run
> build` — editing `.env` or restarting the web service will not update an
> already-built dashboard.

---

## Step 5 — Install systemd units

The **canonical LIVE poll-loop unit is `deploy/midas-agent.service`** (runs
`--executor twak --live-frames --live-cmc` off `/etc/midas/agent.env`, writing
`.magic_agent/twak/*`). `deploy/systemd/magic-agent-run.service` is a separate
**PAPER-ONLY** loop (no funds) — install it only if you want a paper dashboard.

```bash
# Canonical live run unit (the one you enable for live trading):
sudo cp /opt/midas/deploy/midas-agent.service             /etc/systemd/system/
# Read-only API + dashboard:
sudo cp /opt/midas/deploy/systemd/magic-agent-serve.service /etc/systemd/system/
sudo cp /opt/midas/deploy/systemd/magic-agent-web.service   /etc/systemd/system/
# OPTIONAL: paper-only loop (no funds) — only if you want a paper run instead of/alongside live:
# sudo cp /opt/midas/deploy/systemd/magic-agent-run.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Edit each unit file to confirm:
- `User=midas` matches your OS service user.
- `WorkingDirectory=/opt/midas` is correct for run + serve (must be the same).
- The live unit's `serve --status .magic_agent/twak/status.json` matches the run unit's
  per-mode tree (paper-only loop writes `.magic_agent/paper/status.json` — change serve's
  `--status` accordingly).
- The path to `uv` (`/usr/local/bin/uv`) / the live binary (`/opt/midas/.venv/bin/magic-agent`)
  matches your install; `which uv` for the service user.
- The path to `npm` (`/usr/bin/npm`) matches `which npm` for your service user.

---

## Step 6 — Enable and start services

> Do NOT enable the live `midas-agent.service` until every gate in
> `docs/track1-spot-runbook.md` (incl. the supervised live canary) is cleared and
> `/etc/midas/agent.env` holds the six live secrets.

Start in this order:

```bash
# 1. Canonical LIVE poll loop + API share the working directory — start either order.
#    (For a paper dashboard, enable magic-agent-run.service instead and point serve's
#     --status at .magic_agent/paper/status.json.)
sudo systemctl enable --now midas-agent.service
sudo systemctl enable --now magic-agent-serve.service

# 2. Dashboard (reads API via the proxy).
sudo systemctl enable --now magic-agent-web.service

# 3. Verify all three are running before enabling the proxy.
sudo systemctl status midas-agent magic-agent-serve magic-agent-web
```

---

## Step 7 — Configure nginx

```bash
# Obtain a TLS certificate first (certbot):
sudo apt install certbot python3-certbot-nginx
sudo certbot certonly --nginx -d your-domain.example.com

# Install the proxy config.
sudo cp /opt/midas/deploy/nginx.conf.example /etc/nginx/sites-available/magic-agent
sudo nano /etc/nginx/sites-available/magic-agent   # edit server_name + cert paths
sudo ln -s /etc/nginx/sites-available/magic-agent /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Startup order summary

```
1.  midas-agent.service        (LIVE poll loop; writes .magic_agent/twak/*)
2.  magic-agent-serve.service  (reads .magic_agent/twak/status.json via --status; same WorkingDirectory)
    ↑ either order for 1 & 2
3.  magic-agent-web.service    (Next.js dashboard, port 3000, localhost)
4.  nginx                      (public TLS proxy, port 443)

(Paper instead of live: run magic-agent-run.service, which writes .magic_agent/paper/*,
 and point serve's --status at .magic_agent/paper/status.json.)
```

---

## API binding note (REQ-015)

The CLI default for `magic-agent serve` is `--host 0.0.0.0`, which would bind the
API publicly. The systemd unit **overrides this with `--host 127.0.0.1`** so the
API is private and reachable only through nginx. Never remove `--host 127.0.0.1`
from the serve unit's `ExecStart`.

---

## Honesty notes

- **Two executors only: `paper` (default, no funds) and `twak` (live).** There is no
  `aster` executor. The canonical live unit (`midas-agent.service`) runs `--executor twak
  --live-frames --live-cmc`; the paper-only unit runs `--executor paper`.
- **Live data flags are mandatory for live** — without `--live-frames --live-cmc` the loop
  scans only the committed offline ZEC fixture, not the real Track-1 universe.
- **CMC is rank/veto only** — CMC supplies rank/momentum context; it NEVER creates or
  overrides a scanner setup. See root `README.md` for details.
- **Identity stays `unregistered`** — even with `[identity]` extra installed and env
  vars set, production ERC-8004 wiring is pending. The dashboard will show
  `unregistered` until that wiring ships.

---

## Logs

```bash
sudo journalctl -u midas-agent       -f    # LIVE poll loop (paper: magic-agent-run)
sudo journalctl -u magic-agent-serve -f    # FastAPI
sudo journalctl -u magic-agent-web   -f    # Next.js
```

---

## Cross-references

- Root `README.md` — install, scanner pinning, identity extra, CMC, shared state.
- `deploy/.env.example` — full env var reference with inline comments.
- `deploy/systemd/` — systemd unit files.
- `deploy/nginx.conf.example` — nginx reverse-proxy template.
