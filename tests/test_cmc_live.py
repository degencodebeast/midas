"""Tests for the LIVE CoinMarketCapClient (read-only market-data adapter).

The client is a drop-in for ``FixtureCmcClient``: same
``fetch(*, symbols, observed_at) -> tuple[RawCmcQuote, ...]`` port. It resolves
the requested competition symbols to CMC **ids** via the identity registry (NOT
symbols — symbols collide), batches them into ONE
``GET /v2/cryptocurrency/quotes/latest?id=<comma>&convert=USD`` call with the
``X-CMC_PRO_API_KEY`` header, and normalizes ``percent_change_7d/30d`` into
``RawCmcQuote``.

Schema confirmed via Context7 (`/v2/cryptocurrency/quotes/latest`):
``data[id].quote.USD.percent_change_7d``, ``...percent_change_30d``,
``data[id].cmc_rank`` and ``status.error_code`` (0 == success).

FAIL-SOFT (load-bearing): never raise into the trade path, never fabricate a
momentum value. A bad transport / non-200 / ``error_code != 0`` / malformed body
returns empty (caller journals ``cmc_unavailable``); a requested id absent from a
good response is omitted (caller journals ``cmc_missing``).

NO TEST MAKES A REAL NETWORK CALL — the urllib transport is faked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from magic_agent.cmc_source import CoinMarketCapClient, RawCmcQuote


_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def _registry(mapping: dict[str, int]):
    """Minimal registry stub: get_by_symbol -> record with .cmc_id (or None)."""
    return SimpleNamespace(
        get_by_symbol=lambda symbol: (
            SimpleNamespace(cmc_id=mapping[symbol]) if symbol in mapping else None
        )
    )


def _ok_payload(entries: dict[int, tuple[str, float, float, int]]) -> dict:
    """Build a documented-shape CMC /v2 quotes/latest response."""
    data = {}
    for cmc_id, (symbol, p7, p30, rank) in entries.items():
        data[str(cmc_id)] = {
            "id": cmc_id,
            "symbol": symbol,
            "cmc_rank": rank,
            "quote": {"USD": {"percent_change_7d": p7, "percent_change_30d": p30}},
        }
    return {"status": {"error_code": 0, "error_message": None}, "data": data}


class _FakeTransport:
    """Records the url it was asked to GET and returns a canned payload."""

    def __init__(self, payload, *, raise_exc: Exception | None = None) -> None:
        self.payload = payload
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, *, headers, timeout):
        self.calls.append((url, headers))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.payload


def test_fetch_normalizes_into_raw_cmc_quote_by_id():
    payload = _ok_payload({1437: ("ZEC", 0.12, 0.20, 42), 1027: ("ETH", 3.0, -1.0, 2)})
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": 1437, "ETH": 1027}),
        api_key="fake-key",
        transport=transport,
    )

    rows = client.fetch(symbols=("ZEC", "ETH"), observed_at=_NOW)

    by_id = {row.cmc_id: row for row in rows}
    assert by_id[1437] == RawCmcQuote(1437, "ZEC", "0.12", "0.2")
    assert by_id[1027] == RawCmcQuote(1027, "ETH", "3.0", "-1.0")


def test_fetch_queries_by_id_comma_batched_with_auth_header_and_convert_usd():
    payload = _ok_payload({1437: ("ZEC", 0.12, 0.20, 42), 1027: ("ETH", 3.0, -1.0, 2)})
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": 1437, "ETH": 1027}),
        api_key="fake-key",
        transport=transport,
    )

    client.fetch(symbols=("ZEC", "ETH"), observed_at=_NOW)

    assert len(transport.calls) == 1  # ONE batched call
    url, headers = transport.calls[0]
    assert "/v2/cryptocurrency/quotes/latest" in url
    assert "id=1437%2C1027" in url or "id=1437,1027" in url  # comma-batched ids
    assert "convert=USD" in url
    assert "symbol=" not in url  # by id, never by colliding symbol
    assert headers["X-CMC_PRO_API_KEY"] == "fake-key"


def test_missing_api_key_fails_closed_no_silent_fixture():
    with pytest.raises(Exception) as exc:
        CoinMarketCapClient(registry=_registry({"ZEC": 1437}), api_key="")
    assert "CMC_API_KEY" in str(exc.value)


def test_non_200_is_failsoft_returns_empty_no_raise_no_fabrication():
    import urllib.error

    transport = _FakeTransport(
        None, raise_exc=urllib.error.HTTPError("u", 500, "err", {}, None)
    )
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": 1437}), api_key="k", transport=transport
    )

    rows = client.fetch(symbols=("ZEC",), observed_at=_NOW)

    assert rows == ()  # caller journals cmc_unavailable; NO fabricated momentum


def test_error_code_nonzero_is_failsoft_returns_empty():
    payload = {"status": {"error_code": 1001, "error_message": "bad key"}, "data": {}}
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": 1437}), api_key="k", transport=transport
    )

    assert client.fetch(symbols=("ZEC",), observed_at=_NOW) == ()


def test_malformed_body_is_failsoft_returns_empty():
    transport = _FakeTransport({"unexpected": "shape"})
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": 1437}), api_key="k", transport=transport
    )

    assert client.fetch(symbols=("ZEC",), observed_at=_NOW) == ()


def test_requested_id_absent_from_response_is_omitted_not_fabricated():
    # ZEC present, ETH requested but missing from the data map -> ETH omitted.
    payload = _ok_payload({1437: ("ZEC", 0.12, 0.20, 42)})
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": 1437, "ETH": 1027}),
        api_key="k",
        transport=transport,
    )

    rows = client.fetch(symbols=("ZEC", "ETH"), observed_at=_NOW)

    symbols = {row.symbol for row in rows}
    assert symbols == {"ZEC"}  # ETH omitted -> caller journals cmc_missing


def test_symbol_with_no_registry_id_is_skipped_no_call_when_empty():
    # An unresolvable symbol (no registry id) cannot be queried by id; with no
    # resolvable ids there is nothing to fetch -> no call, empty result.
    transport = _FakeTransport(_ok_payload({}))
    client = CoinMarketCapClient(
        registry=_registry({}), api_key="k", transport=transport
    )

    rows = client.fetch(symbols=("MADEUP",), observed_at=_NOW)

    assert rows == ()
    assert transport.calls == []  # no point querying with zero ids


def test_entry_missing_momentum_is_omitted_not_fabricated():
    payload = {
        "status": {"error_code": 0},
        "data": {
            "1437": {
                "id": 1437,
                "symbol": "ZEC",
                "cmc_rank": 42,
                "quote": {"USD": {"percent_change_7d": None, "percent_change_30d": 0.2}},
            }
        },
    }
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": 1437}), api_key="k", transport=transport
    )

    # Missing momentum -> omit that entry rather than invent a value.
    assert client.fetch(symbols=("ZEC",), observed_at=_NOW) == ()
