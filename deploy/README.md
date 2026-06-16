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
.magic_agent/status.json        ◄── magic-agent-run (writes) ───────┘
.magic_agent/decisions.jsonl

ONLY nginx is publicly reachable.  serve and web bind to 127.0.0.1.
```

---

## CRITICAL: shared working directory

`magic-agent run` **writes** `.magic_agent/status.json` and `.magic_agent/decisions.jsonl`.  
`magic-agent serve` **reads** those same files to power `/api/status` and `/api/decisions`.

**Both processes must run with the same `WorkingDirectory`** (`/opt/midas`).  
If they diverge, the API returns stale or missing data with no error — this is the
number-one VPS deployment footgun. The systemd units enforce this by both setting
`WorkingDirectory=/opt/midas`.

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

## Step 3 — Build the web dashboard

```bash
cd /opt/midas/web
npm ci
npm run build
cd /opt/midas
```

---

## Step 4 — Configure environment

```bash
cp /opt/midas/deploy/.env.example /opt/midas/.env
# Edit .env — fill in real values. See subsystem notes inside the file.
nano /opt/midas/.env
```

Key decisions to make in `.env`:

| Var | Decision |
|---|---|
| `ASTER_PRIVATE_KEY` | Leave blank unless using `--executor aster` in the run unit |
| `MAGIC_AGENT_LLM_API_KEY` | Leave blank to keep deterministic mode (default, safe) |
| `MAGIC_AGENT_CMC_API_KEY` | Optional; observe-only today — does not gate trades |
| `NEXT_PUBLIC_API_BASE` | Set to `https://your-domain.example.com/api` |
| `MAGIC_AGENT_ERC8004_*` | Optional; identity stays `unregistered` pending wiring |

---

## Step 5 — Install systemd units

```bash
sudo cp /opt/midas/deploy/systemd/magic-agent-run.service   /etc/systemd/system/
sudo cp /opt/midas/deploy/systemd/magic-agent-serve.service /etc/systemd/system/
sudo cp /opt/midas/deploy/systemd/magic-agent-web.service   /etc/systemd/system/
sudo systemctl daemon-reload
```

Edit each unit file to confirm:
- `User=midas` matches your OS service user.
- `WorkingDirectory=/opt/midas` is correct for run + serve (must be the same).
- The path to `uv` (`/usr/local/bin/uv`) matches `which uv` for your service user.
- The path to `npm` (`/usr/bin/npm`) matches `which npm` for your service user.

---

## Step 6 — Enable and start services

Start in this order:

```bash
# 1. Poll loop and API share the working directory — start either order.
sudo systemctl enable --now magic-agent-run.service
sudo systemctl enable --now magic-agent-serve.service

# 2. Dashboard (reads API via the proxy).
sudo systemctl enable --now magic-agent-web.service

# 3. Verify all three are running before enabling the proxy.
sudo systemctl status magic-agent-run magic-agent-serve magic-agent-web
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
1.  magic-agent-run.service    (writes .magic_agent/*)
2.  magic-agent-serve.service  (reads  .magic_agent/* — same WorkingDirectory)
    ↑ either order for 1 & 2
3.  magic-agent-web.service    (Next.js dashboard, port 3000, localhost)
4.  nginx                      (public TLS proxy, port 443)
```

---

## API binding note (REQ-015)

The CLI default for `magic-agent serve` is `--host 0.0.0.0`, which would bind the
API publicly. The systemd unit **overrides this with `--host 127.0.0.1`** so the
API is private and reachable only through nginx. Never remove `--host 127.0.0.1`
from the serve unit's `ExecStart`.

---

## Honesty notes

- **Paper is default** — the run unit defaults to `--executor paper` (no real funds).
  Switch to `--executor aster` deliberately and only after setting `ASTER_PRIVATE_KEY`.
- **LLM advisor is off by default** — `MAGIC_AGENT_LLM_API_KEY` unset → deterministic
  mode. No LLM calls are made unless you opt in.
- **CMC is observe-only** — CMC data is fetched and logged but does not gate trades
  or influence sizing today. See root `README.md` for details.
- **Identity stays `unregistered`** — even with `[identity]` extra installed and env
  vars set, production ERC-8004 wiring is pending. The dashboard will show
  `unregistered` until that wiring ships.

---

## Logs

```bash
sudo journalctl -u magic-agent-run   -f    # poll loop
sudo journalctl -u magic-agent-serve -f    # FastAPI
sudo journalctl -u magic-agent-web   -f    # Next.js
```

---

## Cross-references

- Root `README.md` — install, scanner pinning, identity extra, CMC, shared state.
- `deploy/.env.example` — full env var reference with inline comments.
- `deploy/systemd/` — systemd unit files.
- `deploy/nginx.conf.example` — nginx reverse-proxy template.
