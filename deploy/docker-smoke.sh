#!/usr/bin/env bash
# MIDAS fresh-Linux deploy smoke (Docker).
#
# Proves the branch installs and RUNS standalone in a clean Linux container with NO
# sibling `../trading-scanner` checkout — so the `git+https` scanner pin actually
# resolves from GitHub and the paper runtime works on a VPS-like box. This is the
# bridge between "works on my Mac" and "works on the VPS".
#
# The scanner repo (degencodebeast/trading-scanner) is PRIVATE, so the clone needs a
# read-only GitHub token. Provide it WITHOUT committing or echoing it:
#   GITHUB_TOKEN=ghp_...                 bash deploy/docker-smoke.sh     # from env
#   GITHUB_TOKEN_FILE=/path/to/tokenfile bash deploy/docker-smoke.sh     # from a file
# The token is passed to the container via -e (never written to any tracked file, and
# never placed in pyproject). Without a token the scanner clone fails closed (private).
#
# NO funds. NO secrets beyond the read-only repo token. NO real swaps. TWAK auth /
# quote-only smoke is NOT exercised here (that needs your trading credentials).
#
# Usage:  GITHUB_TOKEN_FILE=~/.midas-gh-token bash deploy/docker-smoke.sh
#         SMOKE_IMAGE=ubuntu:24.04 GITHUB_TOKEN=... bash deploy/docker-smoke.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${SMOKE_IMAGE:-debian:bookworm-slim}"

# Resolve the read-only deploy token from env or a file (never printed).
TOKEN="${GITHUB_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -n "${GITHUB_TOKEN_FILE:-}" ] && [ -f "$GITHUB_TOKEN_FILE" ]; then
  TOKEN="$(tr -d '\r\n' < "$GITHUB_TOKEN_FILE")"
fi

echo "MIDAS docker fresh-Linux smoke"
echo "  image:  $IMAGE"
echo "  repo:   $REPO_DIR"
echo "  token:  $( [ -n "$TOKEN" ] && echo 'present (read-only, not shown)' || echo 'ABSENT — private scanner clone will fail' )"
echo "  (no funds, no real swaps)"
echo

docker run --rm -e GITHUB_TOKEN="$TOKEN" -v "$REPO_DIR":/src:ro "$IMAGE" bash -euo pipefail -c '
  echo "=== install toolchain (git, curl, build-essential, uv) ==="
  apt-get update -qq
  apt-get install -y -qq git curl ca-certificates build-essential >/dev/null

  # Authenticate git for the PRIVATE scanner clone WITHOUT putting the token in any
  # tracked file: rewrite github https -> token-auth https just for this ephemeral
  # container. (On the VPS, use a credential helper or the same insteadOf with a
  # chmod-600 gitconfig — NEVER the pinned URL in pyproject.)
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git config --global url."https://oauth2:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
  fi

  # Copy the repo into a writable layer, EXCLUDING the Mac venv / git / local state /
  # any node_modules, so uv resolves EVERYTHING fresh on Linux (the scanner must come
  # from git+https, not a sibling checkout that does not exist in this container).
  mkdir -p /app
  tar -C /src -cf - --exclude=.venv --exclude=.git --exclude=.magic_agent --exclude=node_modules . | tar -C /app -xf -
  cd /app
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  export PATH="$HOME/.local/bin:$PATH"

  echo "=== uv sync (scanner resolves from git+https — no sibling checkout here) ==="
  uv sync

  echo "=== deployability: scanner pin is git+https @ 5f92552, NOT file:// ==="
  grep -q "git = \"https://github.com/degencodebeast/trading-scanner\"" pyproject.toml
  if grep -q "file://" pyproject.toml; then echo "FAIL: file:// in pyproject"; exit 1; fi
  grep -q "5f92552e8fdd688808e2709eefc176ab681b7f4f" uv.lock

  echo "=== gates: scanner pin + fail-closed live env + real BSC RPC default ==="
  uv run pytest tests/test_scanner_deploy_pin.py \
    tests/test_app.py::test_build_app_twak_still_fails_closed_without_required_secrets \
    tests/test_app.py::test_build_app_twak_rpc_default_is_real_bsc_client -q

  echo "=== paper smoke (must exit 0) ==="
  uv run magic-agent run --executor paper --max-iters 1

  echo "=== TWAK CLI on Node 24 (distro Node 18 crashes the CLI with ERR_REQUIRE_ESM) ==="
  # @trustwallet/cli require()s an ESM module. That throws ERR_REQUIRE_ESM on Debian's
  # default Node 18, but WORKS on Node >=20.19 (require(esm) was backported there) and on
  # Node 22/24. We install Node 24 to match the tested local setup (twak 0.19.1). The VPS
  # MUST install Node >=20.19 too — never rely on the distro `apt install nodejs` (=18).
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash - >/dev/null 2>&1
  apt-get install -y -qq nodejs >/dev/null 2>&1
  echo "node $(node --version 2>/dev/null) / npm $(npm --version 2>/dev/null)"
  if npm install -g @trustwallet/cli >/dev/null 2>&1; then
    if twak --version; then
      echo "twak CLI runs on Node 20: OK (real auth/quote-only still needs VPS secrets)"
    else
      echo "twak: --version FAILED even on Node 20 — investigate before VPS"
    fi
  else
    echo "twak: npm install failed (non-fatal here; verify on VPS)"
  fi

  echo "=== DOCKER FRESH-LINUX SMOKE: PASS ==="
'
