#!/usr/bin/env bash
# MIDAS TWAK VPS bring-up. Layer 1 only: verify installed/authenticated TWAK,
# existing registered wallet, balances, competition status, and quote-only shape.
# No real swaps. No wallet creation. No default competition registration.
set -euo pipefail

echo "No real swaps. This script performs status checks and QUOTE-ONLY smoke only."

: "${TWAK_ACCESS_ID:?export TWAK_ACCESS_ID}"
: "${TWAK_HMAC_SECRET:?export TWAK_HMAC_SECRET}"
: "${TWAK_WALLET_PASSWORD:?export TWAK_WALLET_PASSWORD for headless unlock}"
: "${WALLET_ADDRESS:?export WALLET_ADDRESS for the already-registered BSC wallet}"
: "${GOLD_CONTRACT:?export GOLD_CONTRACT for quote-only sell smoke}"

# TWAK CLI requires a modern Node: @trustwallet/cli require()s an ESM module, which
# throws ERR_REQUIRE_ESM on Debian/Ubuntu's DEFAULT Node 18 (`apt install nodejs`). It
# works on Node >=20.19 (require(esm) backported) and on Node 22/24. Verified in
# deploy/docker-smoke.sh. Fail closed here rather than crash mid-bring-up.
if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node not found. Install Node 24:" >&2
  echo "  curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && apt-get install -y nodejs" >&2
  exit 3
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
NODE_MINOR="$(node -p 'process.versions.node.split(".")[1]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -lt 20 ] || { [ "$NODE_MAJOR" -eq 20 ] && [ "$NODE_MINOR" -lt 19 ]; }; then
  echo "ERROR: Node $(node --version) is too old — @trustwallet/cli throws ERR_REQUIRE_ESM on Node <20.19." >&2
  echo "Install Node 24 (matches the tested setup):" >&2
  echo "  curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && apt-get install -y nodejs" >&2
  exit 3
fi
node --version
npm --version
npm install -g @trustwallet/cli
twak --version

if ! twak auth status --json >/tmp/twak-auth-status.json 2>/dev/null; then
  twak init
fi
twak auth status --json
twak wallet status --json

TWAK_ADDRESS_JSON="$(twak wallet address --chain bsc --json)"
echo "$TWAK_ADDRESS_JSON"
if ! echo "$TWAK_ADDRESS_JSON" | grep -qi "$WALLET_ADDRESS"; then
  echo "TWAK wallet address does not match WALLET_ADDRESS=$WALLET_ADDRESS" >&2
  exit 2
fi

twak wallet balance --json
echo "Verify BNB gas reserve and USDC/USDT trading capital manually before live."

twak compete status --json
if [ "${RUN_COMPETE_REGISTER:-0}" = "1" ]; then
  twak compete register
  twak compete status --json
fi

# Quote-only smoke against the real twak 0.19.1 CLI. The swap token is the GOLD
# CONTRACT (NOT the wallet address); a sell is the <from> <to> order token->USDC.
# Never run a swap without --quote-only here.
twak swap 1 USDC "$GOLD_CONTRACT" --chain bsc --quote-only --json
twak swap 1 "$GOLD_CONTRACT" USDC --chain bsc --quote-only --json

echo "TWAK quote-only bring-up complete. Live trading still requires operator approval."
