from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from magic_agent.twak import TwakError


def _to_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return default


class TwakBalanceReader:
    """Reads ``wallet balance --chain bsc --json`` and projects the coordinator's
    ``{"stable": Decimal, "token": Decimal}`` snapshot contract.

    Reconciled to twak 0.19.1: there is NO per-token flag. The command returns
    the native gas balance at the top level (``symbol``/``available``, e.g. BNB)
    plus a ``tokens`` LIST. ``stable`` is the held USDC amount and ``token`` is the
    held target-token amount, both derived from ``tokens[]`` (zero when absent —
    an empty wallet is safe, not an error).
    """

    def __init__(self, *, twak, registry, stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
        self._registry = registry
        self._stable_symbol = stable_symbol
        self._chain = chain

    def snapshot(self, identity_key: str) -> dict:
        payload = self._twak.json([
            "wallet", "balance",
            "--chain", self._chain,
            "--json",
        ])
        data = payload.get("data", payload) if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise TwakError("twak balance payload is not an object")
        if data.get("error") is not None:
            raise TwakError(f"twak balance reported error: {data.get('error')!r}")

        target_contract = self._registry.by_contract_key(identity_key).contract_address

        tokens = data.get("tokens")
        if tokens is None:
            tokens = []
        if not isinstance(tokens, list):
            raise TwakError("twak balance 'tokens' is not a list")

        stable = self._extract_token_amount(tokens, symbol=self._stable_symbol, contract=None)
        token = self._extract_token_amount(tokens, symbol=None, contract=target_contract)

        return {
            "stable": stable,
            "token": token,
            "native": _to_decimal(data.get("available")),
            "native_symbol": str(data.get("symbol", "")),
        }

    def wallet_equity(self) -> dict[str, Decimal]:
        """Read the live wallet and return ``{'equity_usd', 'cash_usd'}``.

        ``equity_usd`` = native USD (top-level ``totalUsd``) + the deployable stable
        (USDC) ``balance`` from ``tokens[]``. ``cash_usd`` = that stable balance.

        ``totalUsd`` is the USD value of the NATIVE gas coin (e.g. BNB); ``tokens[]``
        entries carry no USD, so the stable (USDC, ~1:1 USD) ``balance`` is used as its
        USD value. NON-STABLE token USD valuation is a FOLLOW-UP (no on-chain pricing
        here — we never fabricate a price); at canary start no non-stable tokens are
        held, so equity = native_usd + usdc. Empty ``tokens`` -> cash 0, equity =
        native_usd. Fails CLOSED (raises :class:`TwakError`) on a malformed payload or
        a non-numeric ``totalUsd``/stable ``balance``.
        """
        payload = self._twak.json([
            "wallet", "balance",
            "--chain", self._chain,
            "--json",
        ])
        data = payload.get("data", payload) if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise TwakError("twak balance payload is not an object")
        if data.get("error") is not None:
            raise TwakError(f"twak balance reported error: {data.get('error')!r}")

        raw_total = data.get("totalUsd")
        if raw_total is None:
            raise TwakError("twak balance payload is missing 'totalUsd'")
        try:
            native_usd = Decimal(str(raw_total))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise TwakError(
                f"twak balance has non-numeric totalUsd {raw_total!r}"
            ) from exc

        tokens = data.get("tokens")
        if tokens is None:
            tokens = []
        if not isinstance(tokens, list):
            raise TwakError("twak balance 'tokens' is not a list")

        usdc_balance = self._extract_token_amount(tokens, symbol=self._stable_symbol, contract=None)
        return {
            "equity_usd": native_usd + usdc_balance,
            "cash_usd": usdc_balance,
        }

    def _extract_token_amount(self, tokens, *, symbol: str | None, contract: str | None) -> Decimal:
        # A populated tokens[] entry is {"symbol", "contract", "balance"} (verified
        # against a real funded BSC balance). Match by symbol (case-insensitive) OR
        # contract (case-insensitive address), and read the quantity from "balance".
        # A token absent from tokens[] -> Decimal("0") (not-held / empty wallet safe);
        # a malformed/non-numeric "balance" fails CLOSED (raises).
        for entry in tokens:
            if not isinstance(entry, dict):
                continue
            entry_symbol = str(entry.get("symbol", "")).upper()
            entry_contract = str(entry.get("contract", "")).lower()
            matched = False
            if symbol is not None and entry_symbol == symbol.upper():
                matched = True
            if contract is not None and entry_contract == str(contract).lower():
                matched = True
            if not matched:
                continue
            raw = entry.get("balance")
            try:
                return Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise TwakError(
                    f"twak balance token has non-numeric balance {raw!r}"
                ) from exc
        return Decimal("0")


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
