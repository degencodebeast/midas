#!/usr/bin/env python3
"""Build ``data/track1_identities.json`` from the authoritative boomerang token list.

The source of truth for the Track-1 BSC execution universe is the boomerang
``eligible_tokens.json`` (schema ``{"base": {...}, "tokens": {SYMBOL: contract}}``),
resolved upstream from CoinMarketCap ``/info``. This is a PURE transform — it does
NOT call the network. ``cmc_id`` is resolved at runtime by the live CMC client
(symbol + contract), so it is only stamped here for the 6 historically-known tokens
to keep precise id matching available.

Run:
    uv run python scripts/build_identity_registry.py

Writes one record per line of valid JSON (array) that ``IdentityRegistry.load``
accepts, with EVERY token ``verification_status="gold"`` (full execution universe).
"""
from __future__ import annotations

import json
from pathlib import Path

# Repo-relative paths (this file lives in midas/scripts/).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT_PATH = _REPO_ROOT / "data" / "track1_identities.json"

# The authoritative boomerang eligible-tokens list (sibling workspace repo).
_BOOMERANG_PATH = (
    _REPO_ROOT.parent / "boomerang-ai" / "data" / "eligible_tokens.json"
)

# verified_at carried forward from the existing registry (no new verification run).
_VERIFIED_AT = "2026-06-21T00:00:00Z"

# The 6 tokens whose CMC ids were already resolved + shipped. Every other token
# omits cmc_id and is resolved at runtime by the live CMC client (symbol+contract).
_KNOWN_CMC_IDS = {
    "APE": 18876,
    "ZEC": 1437,
    "DEXE": 7326,
    "TRX": 1958,
    "LINK": 1975,
    "XRP": 52,
}


def build_records(tokens: dict[str, str]) -> list[dict]:
    """Transform a ``{SYMBOL: contract}`` map into identity records (all gold)."""
    records: list[dict] = []
    for symbol, contract in tokens.items():
        record: dict = {
            "competition_symbol": symbol,
            "chain_id": 56,
            "contract_address": contract,
            "decimals": 18,  # not used in execution; 18 is a safe default
            "onchain_symbol": symbol,
            "market_data_source": "gateio",
            "market_data_symbol": f"{symbol}_USDT",
            "coverage_status": "scannable",
            "verification_status": "gold",
            "verified_at": _VERIFIED_AT,
            "sources": ["boomerang_cmc_registry"],
        }
        if symbol in _KNOWN_CMC_IDS:
            # Stamp cmc_id only for the already-resolved 6 (omit otherwise).
            record["cmc_id"] = _KNOWN_CMC_IDS[symbol]
        records.append(record)
    return records


def main() -> None:
    source = json.loads(_BOOMERANG_PATH.read_text(encoding="utf-8"))
    tokens = source["tokens"]
    records = build_records(tokens)

    # One record per line inside a JSON array — valid JSON, diff-friendly.
    lines = ["["]
    for index, record in enumerate(records):
        suffix = "," if index < len(records) - 1 else ""
        lines.append("  " + json.dumps(record, ensure_ascii=False) + suffix)
    lines.append("]")
    _OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    gold = sum(1 for r in records if r["verification_status"] == "gold")
    with_id = sum(1 for r in records if "cmc_id" in r)
    print(
        f"wrote {len(records)} records to {_OUTPUT_PATH} "
        f"({gold} gold, {with_id} with cmc_id)"
    )


if __name__ == "__main__":
    main()
