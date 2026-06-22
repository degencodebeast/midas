"""BscRpcClient tests with a FAKE transport (NO network, NO real URL/key).

The client speaks JSON-RPC over an injectable transport so these tests never
touch the network. The fail-closed contract is the load-bearing property: a
receipt-poll timeout or any RPC/transport error must RAISE ``RpcError`` rather
than fabricate a success/revert result (which the execution coordinator would
otherwise mistake for chain truth).
"""

import pytest

from magic_agent.live_rpc import BscRpcClient, RpcError


def _client(responses, **kwargs):
    """Build a client whose transport replays a list of canned JSON-RPC dicts.

    No real URL/key: a placeholder URL is used and the transport never reads it.
    """
    it = iter(responses)
    calls: list[dict] = []

    def transport(url, payload, *, timeout):
        calls.append({"url": url, "payload": payload, "timeout": timeout})
        return next(it)

    client = BscRpcClient(
        rpc_url="http://fake.invalid/rpc",
        wallet_address="0xWALLET",
        transport=transport,
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return client, calls


def test_wallet_nonce_decodes_hex_result():
    client, calls = _client([{"jsonrpc": "2.0", "id": 1, "result": "0x7"}])
    assert client.wallet_nonce() == 7
    assert calls[0]["payload"]["method"] == "eth_getTransactionCount"
    assert calls[0]["payload"]["params"] == ["0xWALLET", "pending"]


def test_wallet_nonce_raises_on_malformed_result():
    client, _ = _client([{"jsonrpc": "2.0", "id": 1, "result": "not-hex"}])
    with pytest.raises(RpcError):
        client.wallet_nonce()


def test_wallet_nonce_raises_on_none_result():
    # A null result is malformed for a nonce query.
    client, _ = _client([{"jsonrpc": "2.0", "id": 1, "result": None}])
    with pytest.raises(RpcError):
        client.wallet_nonce()


def test_wait_receipt_returns_receipt_after_polling():
    # Transport returns null receipt twice, then a real receipt.
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": None},
        {"jsonrpc": "2.0", "id": 2, "result": None},
        {"jsonrpc": "2.0", "id": 3, "result": {"status": "0x1", "blockNumber": "0x10"}},
    ]
    # monotonic stays well inside the deadline so polling continues.
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    client, _ = _client(responses, monotonic=lambda: next(ticks))
    receipt = client.wait_receipt("0xabc")
    assert receipt == {"status": "0x1", "blockNumber": "0x10"}


def test_wait_receipt_raises_on_timeout_not_fabricated_receipt():
    # Transport ALWAYS returns a null receipt; monotonic advances past the
    # deadline so the poll loop must RAISE (fail closed), never fabricate "0x0".
    responses = ({"jsonrpc": "2.0", "id": i, "result": None} for i in range(1000))

    def transport(url, payload, *, timeout):
        return next(responses)

    times = iter([0.0, 1.0, 200.0, 300.0])
    client = BscRpcClient(
        rpc_url="http://fake.invalid/rpc",
        wallet_address="0xWALLET",
        transport=transport,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(times),
        required_poll_timeout=120.0,
    )
    with pytest.raises(RpcError):
        client.wait_receipt("0xabc")


def test_wait_receipt_raises_on_malformed_receipt():
    client, _ = _client([{"jsonrpc": "2.0", "id": 1, "result": "not-a-dict"}])
    with pytest.raises(RpcError):
        client.wait_receipt("0xabc")


def test_confirmations_computes_head_minus_block_plus_one():
    # head 0x12 (18), block 0x10 (16) => 18 - 16 + 1 = 3.
    client, _ = _client([{"jsonrpc": "2.0", "id": 1, "result": "0x12"}])
    assert client.confirmations({"blockNumber": "0x10"}) == 3


def test_confirmations_zero_when_no_block_number():
    # No eth_blockNumber call is needed when the receipt lacks a block.
    client, calls = _client([])
    assert client.confirmations({"status": "0x1"}) == 0
    assert calls == []


def test_call_raises_on_error_field():
    client, _ = _client([{"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}])
    with pytest.raises(RpcError):
        client.wallet_nonce()


def test_call_raises_when_result_missing():
    client, _ = _client([{"jsonrpc": "2.0", "id": 1}])
    with pytest.raises(RpcError):
        client.wallet_nonce()


def test_call_raises_on_transport_exception():
    def transport(url, payload, *, timeout):
        raise OSError("network down")

    client = BscRpcClient(
        rpc_url="http://fake.invalid/rpc",
        wallet_address="0xWALLET",
        transport=transport,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(RpcError):
        client.wallet_nonce()
