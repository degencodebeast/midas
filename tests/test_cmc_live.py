"""Tests for the LIVE CoinMarketCapClient (read-only market-data adapter).

The client is a drop-in for ``FixtureCmcClient``: same
``fetch(*, symbols, observed_at) -> tuple[RawCmcQuote, ...]`` port. It resolves
the requested competition symbols to their registry identity (SYMBOL + BSC
CONTRACT ADDRESS), batches them into ONE
``GET /v2/cryptocurrency/quotes/latest?symbol=<comma>&convert=USD&aux=platform``
call with the ``X-CMC_PRO_API_KEY`` header, and normalizes
``percent_change_7d/30d`` into ``RawCmcQuote``.

Disambiguation (load-bearing): single-ticker collisions (e.g. "B", "M") are
resolved by matching the CMC entry's BSC ``platform.token_address`` against the
registry contract (case-insensitive) — NEVER guessed. A symbol whose contract
matches no CMC entry is OMITTED.

Schema confirmed via Context7 (`/v2/cryptocurrency/quotes/latest`): when queried
by SYMBOL, ``data`` is keyed by SYMBOL and maps to a LIST of matching coins.
Each coin carries ``id``, ``cmc_rank``, ``platform.token_address`` (the chain
contract; ``aux=platform``), and ``quote.USD.percent_change_7d/30d``.
``status.error_code`` (0 == success).

FAIL-SOFT (load-bearing): never raise into the trade path, never fabricate a
momentum value. A bad transport / non-200 / ``error_code != 0`` / malformed body
returns empty (caller journals ``cmc_unavailable``); a requested symbol absent
from a good response (or undisambiguated by contract) is omitted (caller journals
``cmc_missing``).

NO TEST MAKES A REAL NETWORK CALL — the urllib transport is faked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from magic_agent.cmc_source import CoinMarketCapClient, RawCmcQuote


_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)

# BSC platform slug/name as CMC returns it for binance-smart-chain tokens.
_BSC_PLATFORM = {"id": 1839, "name": "BNB Smart Chain", "slug": "binance-smart-chain", "symbol": "BNB"}


def _record(symbol: str, contract: str | None, cmc_id: int | None = None):
    return SimpleNamespace(
        competition_symbol=symbol,
        contract_address=contract,
        cmc_id=cmc_id,
    )


def _registry(records: dict[str, object]):
    """Registry stub exposing get_by_symbol -> record(symbol, contract, cmc_id)."""
    return SimpleNamespace(
        get_by_symbol=lambda symbol: records.get(symbol),
    )


def _entry(cmc_id: int, symbol: str, token_address: str | None, p7, p30, rank: int = 1) -> dict:
    platform = None
    if token_address is not None:
        platform = {**_BSC_PLATFORM, "token_address": token_address}
    return {
        "id": cmc_id,
        "symbol": symbol,
        "cmc_rank": rank,
        "platform": platform,
        "quote": {"USD": {"percent_change_7d": p7, "percent_change_30d": p30}},
    }


def _ok_payload(by_symbol: dict[str, list[dict]]) -> dict:
    return {"status": {"error_code": 0, "error_message": None}, "data": by_symbol}


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


def test_fetch_normalizes_single_match_into_raw_cmc_quote():
    payload = _ok_payload({
        "ZEC": [_entry(1437, "ZEC", "0x1ba42e5193dfa8b03d15dd1b86a3113bbbef8eeb", 0.12, 0.20, 42)],
        "ETH": [_entry(1027, "ETH", "0x2170ed0880ac9a755fd29b2688956bd959f933f8", 3.0, -1.0, 2)],
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({
            "ZEC": _record("ZEC", "0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb", 1437),
            "ETH": _record("ETH", "0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 1027),
        }),
        api_key="fake-key",
        transport=transport,
    )

    rows = client.fetch(symbols=("ZEC", "ETH"), observed_at=_NOW)

    by_id = {row.cmc_id: row for row in rows}
    assert by_id[1437] == RawCmcQuote(1437, "ZEC", "0.12", "0.2")
    assert by_id[1027] == RawCmcQuote(1027, "ETH", "3.0", "-1.0")


def test_fetch_queries_by_symbol_comma_batched_with_auth_header_and_platform_aux():
    payload = _ok_payload({
        "ZEC": [_entry(1437, "ZEC", "0x1ba42e5193dfa8b03d15dd1b86a3113bbbef8eeb", 0.12, 0.20)],
        "ETH": [_entry(1027, "ETH", "0x2170ed0880ac9a755fd29b2688956bd959f933f8", 3.0, -1.0)],
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({
            "ZEC": _record("ZEC", "0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb", 1437),
            "ETH": _record("ETH", "0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 1027),
        }),
        api_key="fake-key",
        transport=transport,
    )

    client.fetch(symbols=("ZEC", "ETH"), observed_at=_NOW)

    assert len(transport.calls) == 1  # ONE batched call
    url, headers = transport.calls[0]
    assert "/v2/cryptocurrency/quotes/latest" in url
    assert "symbol=ZEC%2CETH" in url or "symbol=ZEC,ETH" in url  # comma-batched symbols
    assert "convert=USD" in url
    assert "aux=platform" in url  # need the platform contract to disambiguate
    assert headers["X-CMC_PRO_API_KEY"] == "fake-key"


def test_collision_is_resolved_by_contract_not_guessed():
    # Symbol "B" has TWO CMC matches with different BSC contracts. The registry's
    # contract for B picks exactly one; the other (wrong-contract) is dropped.
    registry_contract = "0x6bdcCe4A559076e37755a78Ce0c06214E59e4444"
    decoy_contract = "0xDEADBEEF00000000000000000000000000000000"
    payload = _ok_payload({
        "B": [
            _entry(99901, "B", decoy_contract, 9.9, 9.9, 500),
            _entry(36784, "B", registry_contract.lower(), 1.1, 2.2, 80),
        ],
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"B": _record("B", registry_contract)}),  # no cmc_id
        api_key="k",
        transport=transport,
    )

    rows = client.fetch(symbols=("B",), observed_at=_NOW)

    assert len(rows) == 1
    assert rows[0] == RawCmcQuote(36784, "B", "1.1", "2.2")  # contract-matched entry


def test_symbol_whose_contract_matches_no_entry_is_omitted_not_fabricated():
    # "M" returns two matches, NEITHER with the registry contract -> omit (no guess).
    payload = _ok_payload({
        "M": [
            _entry(1, "M", "0x1111111111111111111111111111111111111111", 5.0, 5.0),
            _entry(2, "M", "0x2222222222222222222222222222222222222222", 6.0, 6.0),
        ],
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"M": _record("M", "0x22b1458e780F8fA71E2F84502cEe8B5A3cc731Fa")}),
        api_key="k",
        transport=transport,
    )

    rows = client.fetch(symbols=("M",), observed_at=_NOW)

    assert rows == ()  # contract disambiguation failed -> caller journals cmc_missing


def test_single_match_with_no_registry_contract_resolves_normally():
    # Registry record carries no contract; a single CMC match is used as-is.
    payload = _ok_payload({
        "LINK": [_entry(1975, "LINK", "0xf8a0bf9cf54bb92f17374d9e9a321e6a111a51bd", 0.5, 0.6)],
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"LINK": _record("LINK", None, 1975)}),
        api_key="k",
        transport=transport,
    )

    rows = client.fetch(symbols=("LINK",), observed_at=_NOW)

    assert rows == (RawCmcQuote(1975, "LINK", "0.5", "0.6"),)


def test_six_known_tokens_resolve_by_contract():
    # The 6 currently-shipped tokens still resolve (contract match, cmc_id present).
    known = {
        "APE": ("0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79", 18876),
        "ZEC": ("0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb", 1437),
        "DEXE": ("0x6E88056E8376Ae7709496Ba64d37fa2f8015ce3e", 7326),
        "TRX": ("0xCE7de646e7208a4Ef112cb6ed5038FA6cC6b12e3", 1958),
        "LINK": ("0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD", 1975),
        "XRP": ("0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE", 52),
    }
    payload = _ok_payload({
        sym: [_entry(cid, sym, contract.lower(), 1.0, 2.0)]
        for sym, (contract, cid) in known.items()
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({sym: _record(sym, c, cid) for sym, (c, cid) in known.items()}),
        api_key="k",
        transport=transport,
    )

    rows = client.fetch(symbols=tuple(known), observed_at=_NOW)

    got = {row.symbol: row.cmc_id for row in rows}
    assert got == {sym: cid for sym, (_c, cid) in known.items()}


def test_missing_api_key_fails_closed_no_silent_fixture():
    with pytest.raises(Exception) as exc:
        CoinMarketCapClient(registry=_registry({}), api_key="")
    assert "CMC_API_KEY" in str(exc.value)


def test_non_200_is_failsoft_returns_empty_no_raise_no_fabrication():
    import urllib.error

    transport = _FakeTransport(
        None, raise_exc=urllib.error.HTTPError("u", 500, "err", {}, None)
    )
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": _record("ZEC", "0x01", 1437)}),
        api_key="k",
        transport=transport,
    )

    rows = client.fetch(symbols=("ZEC",), observed_at=_NOW)

    assert rows == ()  # caller journals cmc_unavailable; NO fabricated momentum


def test_error_code_nonzero_is_failsoft_returns_empty():
    payload = {"status": {"error_code": 1001, "error_message": "bad key"}, "data": {}}
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": _record("ZEC", "0x01", 1437)}),
        api_key="k",
        transport=transport,
    )

    assert client.fetch(symbols=("ZEC",), observed_at=_NOW) == ()


def test_malformed_body_is_failsoft_returns_empty():
    transport = _FakeTransport({"unexpected": "shape"})
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": _record("ZEC", "0x01", 1437)}),
        api_key="k",
        transport=transport,
    )

    assert client.fetch(symbols=("ZEC",), observed_at=_NOW) == ()


def test_requested_symbol_absent_from_response_is_omitted_not_fabricated():
    # ZEC present, ETH requested but missing from the data map -> ETH omitted.
    payload = _ok_payload({
        "ZEC": [_entry(1437, "ZEC", "0x1ba42e5193dfa8b03d15dd1b86a3113bbbef8eeb", 0.12, 0.20)],
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({
            "ZEC": _record("ZEC", "0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb", 1437),
            "ETH": _record("ETH", "0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 1027),
        }),
        api_key="k",
        transport=transport,
    )

    rows = client.fetch(symbols=("ZEC", "ETH"), observed_at=_NOW)

    assert {row.symbol for row in rows} == {"ZEC"}  # ETH omitted -> cmc_missing


def test_symbol_with_no_registry_record_is_skipped_no_call_when_empty():
    # An unresolvable symbol (no registry record) cannot be queried; with nothing
    # resolvable there is nothing to fetch -> no call, empty result.
    transport = _FakeTransport(_ok_payload({}))
    client = CoinMarketCapClient(
        registry=_registry({}), api_key="k", transport=transport
    )

    rows = client.fetch(symbols=("MADEUP",), observed_at=_NOW)

    assert rows == ()
    assert transport.calls == []  # no point querying with zero symbols


def test_entry_missing_momentum_is_omitted_not_fabricated():
    payload = _ok_payload({
        "ZEC": [_entry(1437, "ZEC", "0x1ba42e5193dfa8b03d15dd1b86a3113bbbef8eeb", None, 0.2)],
    })
    transport = _FakeTransport(payload)
    client = CoinMarketCapClient(
        registry=_registry({"ZEC": _record("ZEC", "0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb", 1437)}),
        api_key="k",
        transport=transport,
    )

    # Missing momentum -> omit that entry rather than invent a value.
    assert client.fetch(symbols=("ZEC",), observed_at=_NOW) == ()
