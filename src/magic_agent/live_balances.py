from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from magic_agent.twak import TwakError


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
        result: dict[str, Decimal] = {}
        for key in ("stable", "token"):
            if key not in data:
                raise TwakError(f"twak balance payload missing {key!r}")
            try:
                result[key] = Decimal(str(data[key]))
            except (InvalidOperation, TypeError) as exc:
                raise TwakError(f"twak balance payload has non-numeric {key!r}") from exc
        return result


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
