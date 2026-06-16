# Venue Spike Findings (resolves spec §8)

Date: 2026-06-15
Scope: Confirm the execution venue surface for the agent layer (Aster default, ApolloX alt)
and the CMC / Trust Wallet (TWAK) auth model. Each §8 item is marked **CONFIRMED** or
**STILL UNKNOWN** with its source.

Verification basis:
- ccxt `4.5.58` installed in `midas` (via the editable `magic-scanner` dep). All `ccxt`
  output below is from live `uv run python -c ...` inspection, not from memory.
- Aster docs: `github.com/asterdex/api-docs` (V1 Legacy + V3 Recommended trees) and the
  hosted docs site.

---

## CRITICAL HEADLINE — read before Tasks 9–10

The "REST-HMAC" working assumption for the Aster executor is **half right and half wrong**,
and the wrong half changes the executor design:

- Aster exposes **TWO** independent perp API surfaces:
  - **V1 (Legacy)** = classic Binance-style **HMAC SHA256** (`X-MBX-APIKEY` + `secretKey`
    signature). Base: `https://fapi.asterdex.com`. **No documented testnet base URL.**
  - **V3 (Recommended)** = **EIP-712 wallet-signed** (`user` main wallet + `signer` API
    wallet + signer `privateKey`, EIP-712 typed data, `chainId`). Base:
    `https://fapi.asterdex.com`; **testnet `https://fapi.asterdex-testnet.com`.**
- **The ccxt `aster` class implements ONLY the V3 wallet-signed path.** Its
  `requiredCredentials` is `privateKey: True` (apiKey/secret are `False`), and `ex.sign()`
  builds an `AsterSignTransaction` EIP-712 message and calls `sign_message(..., privateKey)`.
  So "ccxt-REST" is true, but it is **REST-wallet-signed, not REST-HMAC.**

Net: a testnet exists **only on the V3 (wallet-signed) surface**, which is exactly the surface
ccxt supports. If Tasks 9–10 want HMAC, they must hand-roll the V1 REST client AND give up the
documented testnet. If they accept wallet-signed, ccxt works as-is against testnet. **Recommend
defaulting to the V3 wallet-signed path** (see final line). This is the contradiction the task
asked me to flag.

---

## §8.1 — Auth flow

**CONFIRMED.**

- **Aster V3 (ccxt default, recommended):** wallet/EIP-712 signed. The wallet (signer
  `privateKey`) **is in the order path** — every TRADE/USER_DATA request is signed per-request
  with EIP-712 over `nonce + user + signer + params`. ccxt `ex.requiredCredentials` =
  `{'privateKey': True, ...}` (apiKey/secret = False); `ex.sign()` source builds domain
  `{name:'AsterSignTransaction', version:'1', chainId:1666, verifyingContract:0x0}` and
  `sign_message(encodedMessage, self.privateKey)`.
  Roles: `user` = main wallet address, `signer` = API wallet address, `privateKey` = signer's
  key. Source: ccxt live inspection + `api-docs/V3(Recommended)/EN/aster-finance-futures-api-testnet.md`
  (lines ~226–259, "API_WALLET authentication... a signer should be included").
- **Aster V1 (Legacy):** HMAC SHA256, API key in `X-MBX-APIKEY` header, signature =
  HMAC-SHA256(secretKey, totalParams), plus `timestamp` + `recvWindow`. Wallet NOT in path.
  Source: `api-docs/V1(Legacy)/EN/aster-finance-futures-api.md` lines 119/192/214–215.
- **ApolloX:** Aster is the rebrand/successor of ApolloX; ApolloX is **NOT a separate ccxt
  exchange** (`"apollox" in ccxt.exchanges` → `False`). The legacy ApolloX HMAC API is the same
  Binance-style shape as Aster V1. Source: ccxt live inspection.

## §8.2 — Testnet / sandbox

**CONFIRMED (with a caveat).**

- **Aster V3 perp testnet base URL: `https://fapi.asterdex-testnet.com`** (WS:
  `wss://fstream.asterdex-testnet.com`). AGENT/API-wallet creation at
  `https://www.asterdex-testnet.com/en/api-wallet`. Source:
  `api-docs/V3(Recommended)/EN/aster-finance-futures-api-testnet.md` lines 128–129, 2026–2028.
- **Caveat / STILL UNKNOWN:** ccxt's `aster` describe lists a `test` URL key but it resolves to
  `None` (`ex.urls['test']` is `None`) — ccxt does **not** ship the testnet base URL, so
  `ex.set_sandbox_mode(True)` will NOT work out of the box. The executor must override
  `ex.urls['api']` to the `*-testnet.com` hosts manually. Source: ccxt live inspection.
- **V1 (HMAC) testnet: STILL UNKNOWN / likely none** — the V1 legacy doc only documents the
  production base `https://fapi.asterdex.com` and contains no testnet endpoint. Source:
  `api-docs/V1(Legacy)/EN/aster-finance-futures-api.md` (only `https://fapi.asterdex.com`).
- **chainId mismatch to verify before signing on testnet:** ccxt hardcodes `v3ChainId: 1666`;
  the testnet doc's EIP-712 example shows `chainId: 714`. This must be reconciled (likely set
  `ex.options['v3ChainId']` for testnet) or testnet signatures will be rejected. Source: ccxt
  `options` dump vs testnet doc line 303.

## §8.3 — Leverage / positionSide (hedge mode)

**CONFIRMED.**

- **positionSide values:** `BOTH` (one-way mode, default), `LONG` (buy side of hedge mode),
  `SHORT` (sell side of hedge mode). Must be sent in hedge mode. Source: ccxt `create_order`
  docstring + `aster-finance-futures-api-testnet.md` line 2239/2397.
- **Hedge-mode toggle:** `POST/GET /fapi/v3/positionSide/dual`. ccxt exposes
  `set_position_mode` (present = True). Source: testnet doc lines 2053/2078 + ccxt inspection.
- **Leverage:** `POST /fapi/v3/leverage` (V3) / `POST /fapi/v1/leverage` (V1). ccxt
  `set_leverage` present = True. Margin type: `POST .../marginType` (ISOLATED|CROSSED), ccxt
  `set_margin_mode` present = True. Source: testnet doc line 3060 + hosted docs + ccxt inspection.

## §8.4 — ccxt fit

**CONFIRMED.**

- `"aster" in ccxt.exchanges` → **True**; class `ccxt.aster` (`ccxt 4.5.58`).
- Methods present: `create_order`, `set_leverage`, `fetch_positions`, `set_margin_mode`,
  `set_position_mode`, `fetch_balance` — all **True**.
- `load_markets()` → 537 markets, **481 perp/contract** (linear USDT-settled swaps, e.g.
  `BTC/USDT:USDT`, `ASTER/USDT:USDT`); sample market `type=swap, linear=True, settle=USDT`.
- Base URLs (ccxt): `fapiPublic/fapiPrivate = https://fapi.asterdex.com/fapi`,
  `sapi* = https://sapi.asterdex.com/api`.
- **Fit verdict:** ccxt CAN place perp orders, set leverage, set hedge mode, and fetch positions
  — but **only via the V3 wallet-signed path** (it has no HMAC/V1 code path). Builder-fee defaults
  are baked into `options` (`builderFee: True`, `builderRate: 0.001`) — review/disable for the
  executor if undesired. Source: ccxt live inspection.
- **`"apollox" in ccxt.exchanges` → False** — no ccxt fit for ApolloX as a distinct venue.

## §8.5 — CMC / TWAK auth (de-garnish x402)

**CONFIRMED.**

- **CoinMarketCap AI Agent Hub:** two paths. (1) **Standard API key** via `X-CMC_PRO_API_KEY`
  (MCP path) — fully supported, the normal route. (2) **x402** keyless pay-per-request
  ($0.01 USDC/request on Base; payment = auth). **x402 is OPTIONAL / bonus, NOT required** — the
  API-key path is sufficient. Source: `coinmarketcap.com/api/documentation/ai-agent-hub` +
  `pro.coinmarketcap.com/api/documentation/ai-agent-hub/x402`.
- **Trust Wallet Agent Kit (TWAK):** **HMAC-SHA256 signing**, NOT a plain Bearer token. Four
  headers per request: `X-TW-Credential` (access ID), `X-TW-Nonce`, `X-TW-Date` (ISO 8601, ±5
  min), `Authorization` (base64 HMAC-SHA256 signature over
  `METHOD + PATH + QUERY + ACCESS_ID + NONCE + DATE`). The CLI/TS SDK signs automatically. **No
  x402 anywhere in TWAK.** Source: `developer.trustwallet.com/developer/agent-sdk/authentication`.
- **x402 overall:** required nowhere. Only an optional CMC convenience. De-garnished. Source: as above.

---

Default venue = Aster; executor shape = REST-wallet-signed
