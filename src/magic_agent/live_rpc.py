"""Real BSC JSON-RPC client (reads ``BSC_RPC_URL``) for the live booking path.

``BscRpcClient`` queries transaction receipts and confirmation depth over
JSON-RPC using only the Python standard library (``urllib``). The transport is
INJECTABLE so tests never touch the network.

Fail-closed contract (load-bearing): a receipt-poll timeout or any RPC/transport
error RAISES :class:`RpcError`. It never fabricates a success or revert receipt.
The execution coordinator maps a ``wait_receipt`` raise to a ``BROADCAST_UNKNOWN``
(exposure-blocking) outcome — a fabricated result would be mistaken for chain truth.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable


class RpcError(RuntimeError):
    pass


def _urllib_json_post(url: str, payload: dict, *, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class BscRpcClient:
    """Real BSC JSON-RPC client (reads ``BSC_RPC_URL``). Fail-closed: a receipt
    timeout or RPC/transport error RAISES :class:`RpcError` so the execution
    coordinator maps it to a ``BROADCAST_UNKNOWN`` (exposure-blocking) outcome
    rather than fabricating a result. Construction is lazy (no connection); the
    transport is injectable so tests never touch the network."""

    def __init__(
        self,
        *,
        rpc_url: str,
        wallet_address: str,
        required_poll_timeout: float = 120.0,
        poll_interval: float = 3.0,
        request_timeout: float = 10.0,
        transport: Callable[..., dict] | None = None,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._url = rpc_url
        self._wallet = wallet_address
        self._poll_timeout = required_poll_timeout
        self._poll_interval = poll_interval
        self._request_timeout = request_timeout
        self._transport = transport or _urllib_json_post
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic
        self._id = 0

    def _call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        try:
            response = self._transport(self._url, payload, timeout=self._request_timeout)
        except Exception as exc:  # network/transport failure
            raise RpcError(f"rpc transport error for {method}") from exc
        if not isinstance(response, dict) or response.get("error"):
            raise RpcError(
                f"rpc error for {method}: "
                f"{response.get('error') if isinstance(response, dict) else 'malformed'}"
            )
        if "result" not in response:
            raise RpcError(f"rpc missing result for {method}")
        return response["result"]

    def wallet_nonce(self) -> int:
        result = self._call("eth_getTransactionCount", [self._wallet, "pending"])
        try:
            return int(str(result), 16)
        except (TypeError, ValueError) as exc:
            raise RpcError("malformed nonce") from exc

    def wait_receipt(self, tx_hash: str) -> dict:
        deadline = self._monotonic() + self._poll_timeout
        while True:
            result = self._call("eth_getTransactionReceipt", [tx_hash])
            if result is not None:
                if not isinstance(result, dict):
                    raise RpcError("malformed receipt")
                return result
            if self._monotonic() >= deadline:
                raise RpcError(f"receipt timeout for {tx_hash}")
            self._sleep(self._poll_interval)

    def confirmations(self, receipt: dict) -> int:
        block_hex = receipt.get("blockNumber")
        if block_hex is None:
            return 0
        head_hex = self._call("eth_blockNumber", [])
        try:
            head = int(str(head_hex), 16)
            block = int(str(block_hex), 16)
        except (TypeError, ValueError) as exc:
            raise RpcError("malformed block number") from exc
        return max(0, head - block + 1)
