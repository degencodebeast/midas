from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class TwakBalanceReader:
    def __init__(self, *, twak, stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
        self._stable_symbol = stable_symbol
        self._chain = chain

    def snapshot(self, identity_key: str) -> dict[str, Decimal]:
        payload = self._twak.json([
            "wallet", "balance",
            "--chain", self._chain,
            "--token", identity_key,
            "--json",
        ])
        data = payload.get("data", payload)
        return {
            "stable": Decimal(str(data["stable"])),
            "token": Decimal(str(data["token"])),
        }


@dataclass
class StaticRpcClient:
    wallet_nonce_value: int = 0
    receipt: dict | None = None
    confirmation_count: int = 2

    def wallet_nonce(self) -> int:
        return self.wallet_nonce_value

    def wait_receipt(self, tx_hash: str) -> dict:
        return dict(self.receipt or {"status": "0x1", "transactionHash": tx_hash})

    def confirmations(self, receipt: dict) -> int:
        return self.confirmation_count
