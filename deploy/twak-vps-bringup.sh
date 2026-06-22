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

twak swap 1 USDC "$WALLET_ADDRESS" --chain bsc --quote-only --json
twak swap 1 "$GOLD_CONTRACT" "$WALLET_ADDRESS" --chain bsc --quote-only --sell --json

echo "TWAK quote-only bring-up complete. Live trading still requires operator approval."
