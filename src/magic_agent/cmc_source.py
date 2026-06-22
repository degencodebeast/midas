from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from magic_agent.cmc_selector import CandidateSnapshot

_log = logging.getLogger(__name__)


NON_DIRECTIONAL = {
    "USDT", "USDC", "DAI", "USD1", "USDE", "USDD", "TUSD", "FDUSD",
    "FRAX", "FRXUSD", "USDF", "USDF", "LISUSD", "DUSD", "XUSD", "EURI",
    "BILL", "STABLE", "XAUT", "XAUM",
}


@dataclass(frozen=True)
class RawCmcQuote:
    cmc_id: int
    symbol: str
    momentum_7d: str
    momentum_30d: str


@dataclass(frozen=True)
class CmcExclusion:
    eligibility_id: str
    symbol: str
    reason_code: str


@dataclass(frozen=True)
class CmcBatch:
    snapshots: dict[str, CandidateSnapshot]
    exclusions: tuple[CmcExclusion, ...]


class CmcCandidateSource:
    def __init__(self, ledger, registry, client) -> None:
        self.ledger, self.registry, self.client = ledger, registry, client

    def snapshot(self, now: datetime) -> CmcBatch:
        symbols = tuple(dict.fromkeys(row.competition_symbol for row in self.ledger.records))
        quotes = self.client.fetch(symbols=symbols, observed_at=now)
        by_symbol: dict[str, list[RawCmcQuote]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote.symbol, []).append(quote)
        chosen, exclusions = [], []
        for row in self.ledger.records:
            if row.competition_symbol.upper() in NON_DIRECTIONAL:
                exclusions.append(CmcExclusion(row.eligibility_id, row.competition_symbol, "non_directional_asset"))
                continue
            identity = self.registry.get_by_symbol(row.competition_symbol)
            matches = by_symbol.get(row.competition_symbol, [])
            if identity is not None and identity.cmc_id is not None:
                matches = [quote for quote in matches if quote.cmc_id == identity.cmc_id]
            if len(matches) != 1:
                reason = "cmc_missing" if not matches else "cmc_symbol_ambiguous"
                exclusions.append(CmcExclusion(row.eligibility_id, row.competition_symbol, reason))
                continue
            chosen.append((row, identity, matches[0]))
        count = Decimal(len(chosen))
        rank_7d = {
            item[0].eligibility_id: Decimal(rank) / count
            for rank, item in enumerate(
                sorted(chosen, key=lambda value: (-Decimal(value[2].momentum_7d), value[0].eligibility_id)),
                start=1,
            )
        }
        rank_30d = {
            item[0].eligibility_id: Decimal(rank) / count
            for rank, item in enumerate(
                sorted(chosen, key=lambda value: (-Decimal(value[2].momentum_30d), value[0].eligibility_id)),
                start=1,
            )
        }
        snapshots = {}
        for row, identity, quote in chosen:
            identity_key = f"{row.competition_symbol.lower()}-bsc" if identity else row.eligibility_id
            snapshots[row.competition_symbol] = CandidateSnapshot(
                identity_key, now, now + timedelta(minutes=15),
                Decimal(quote.momentum_7d), Decimal(quote.momentum_30d),
                rank_7d[row.eligibility_id], rank_30d[row.eligibility_id],
                False, (), Decimal("1"),
            )
        return CmcBatch(snapshots, tuple(exclusions))


# Base host + endpoint path for the CMC Pro REST API (confirmed via Context7).
_CMC_BASE_URL = "https://pro-api.coinmarketcap.com"
_CMC_QUOTES_PATH = "/v2/cryptocurrency/quotes/latest"


def _urllib_json_get(url: str, *, headers: dict[str, str], timeout: float) -> dict:
    """Stdlib JSON GET (mirrors ``BscRpcClient._urllib_json_post`` style).

    Injectable so tests never touch the network. Any HTTP/transport error
    propagates to the caller, which fails soft.
    """
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CoinMarketCapClient:
    """LIVE, read-only CMC adapter — drop-in for :class:`FixtureCmcClient`.

    Same ``fetch(*, symbols, observed_at) -> tuple[RawCmcQuote, ...]`` port. It
    resolves the requested competition symbols to their registry identity (SYMBOL
    + BSC CONTRACT ADDRESS), batches them into ONE
    ``GET /v2/cryptocurrency/quotes/latest?symbol=<comma>&convert=USD&aux=platform``
    call with the ``X-CMC_PRO_API_KEY`` header, and normalizes
    ``quote.USD.percent_change_7d/30d`` into :class:`RawCmcQuote`.

    DISAMBIGUATION (load-bearing): when queried by symbol, CMC keys ``data`` by
    SYMBOL and maps each to a LIST of matching coins (single-ticker collisions —
    e.g. "B", "M", "H", "Q", "U"). The right coin is selected by matching its BSC
    ``platform.token_address`` against the registry record's ``contract_address``
    (case-insensitive). A symbol whose contract matches NO entry is OMITTED — never
    guessed. If the registry record has a positive ``cmc_id`` it tightens the match
    further (id must also agree). A single match with no registry contract is used
    as-is. ``cmc_id`` is READ from the matched CMC entry, never required as input.

    AUTHORITY: this client only supplies rank/momentum (observe / veto / clamp
    inputs). It carries NO setup-authorizing role — the scanner remains the sole
    setup authority. Read-only market data: no funds, no orders, no secrets in code
    (the key is read from env by the caller and injected here).

    FAIL-SOFT (load-bearing): it NEVER raises into the trade path and NEVER
    fabricates a momentum value. On HTTP error / non-200 / CMC
    ``status.error_code != 0`` / parse failure it returns what it can (empty on a
    total failure) and lets the caller journal ``cmc_unavailable``; a requested
    symbol absent from a good response, or one no entry's contract disambiguates,
    is simply omitted (caller journals ``cmc_missing``).
    """

    def __init__(
        self,
        *,
        registry: Any,
        api_key: str,
        transport: Callable[..., dict] | None = None,
        base_url: str = _CMC_BASE_URL,
        request_timeout: float = 10.0,
    ) -> None:
        # Fail CLOSED on a missing key — never silently degrade to a fixture. The
        # caller (build_app live wiring) reads CMC_API_KEY from env; an empty value
        # here means the live universe cannot be fetched and that must be loud.
        if not api_key:
            raise ValueError(
                "CMC_API_KEY is required for the live CoinMarketCapClient "
                "(set it in the environment); refusing to run live CMC without a key"
            )
        self._registry = registry
        self._api_key = api_key
        self._transport = transport or _urllib_json_get
        self._base_url = base_url
        self._request_timeout = request_timeout

    def fetch(
        self, *, symbols: tuple[str, ...], observed_at: datetime
    ) -> tuple[RawCmcQuote, ...]:
        """Return live quotes for the resolvable ``symbols`` (fail-soft)."""
        # Resolve symbols -> registry identity (symbol + contract [+ optional id]).
        # A symbol with no registry record cannot be disambiguated and is dropped
        # here (the caller journals it as cmc_missing once absent from the snapshot).
        # Keep an ordered symbol -> identity map; CMC is queried BY SYMBOL and the
        # collision among matches is resolved by contract, not by guessing.
        resolved: dict[str, Any] = {}
        for symbol in symbols:
            record = self._registry.get_by_symbol(symbol)
            if record is None:
                continue
            resolved.setdefault(symbol, record)
        if not resolved:
            # Nothing resolvable -> nothing to query (no point spending a credit).
            return ()

        query = urllib.parse.urlencode(
            {"symbol": ",".join(resolved), "convert": "USD", "aux": "platform"}
        )
        url = f"{self._base_url}{_CMC_QUOTES_PATH}?{query}"
        headers = {
            "X-CMC_PRO_API_KEY": self._api_key,
            "Accept": "application/json",
        }
        try:
            payload = self._transport(
                url, headers=headers, timeout=self._request_timeout
            )
        except Exception:
            # HTTP / non-200 / transport / timeout: fail soft. The caller sees an
            # empty batch and journals cmc_unavailable — NEVER a fabricated quote.
            _log.warning("CMC quotes fetch failed (transport); returning empty", exc_info=True)
            return ()

        return self._normalize(payload, resolved)

    @staticmethod
    def _entry_contract(entry: dict) -> str | None:
        """Return the chain ``platform.token_address`` for a CMC coin entry, if any."""
        platform = entry.get("platform")
        if not isinstance(platform, dict):
            return None
        token_address = platform.get("token_address")
        return token_address if isinstance(token_address, str) else None

    @classmethod
    def _select_match(cls, matches: list, record: Any) -> dict | None:
        """Pick the one CMC coin that IS this registry identity (or None).

        Disambiguation order:
          * by contract: the entry whose BSC ``platform.token_address`` equals the
            registry ``contract_address`` (case-insensitive) — and, if the registry
            also carries a positive ``cmc_id``, the entry's id must agree;
          * else (registry has no contract) a single match is taken as-is;
          * else (collision with no contract / no disambiguator) -> None (omit).
        Never guesses among colliding symbols.
        """
        coins = [m for m in matches if isinstance(m, dict)]
        contract = getattr(record, "contract_address", None)
        cmc_id = getattr(record, "cmc_id", None)

        if contract:
            want = contract.lower()
            contract_hits = [
                c for c in coins
                if (cls._entry_contract(c) or "").lower() == want
            ]
            if cmc_id:
                contract_hits = [
                    c for c in contract_hits if c.get("id") == cmc_id
                ]
            if len(contract_hits) == 1:
                return contract_hits[0]
            return None

        # No registry contract to disambiguate by. If the registry carries a cmc_id,
        # use it to pick; otherwise only an unambiguous single match is acceptable.
        if cmc_id:
            id_hits = [c for c in coins if c.get("id") == cmc_id]
            return id_hits[0] if len(id_hits) == 1 else None
        if len(coins) == 1:
            return coins[0]
        return None

    def _normalize(
        self, payload: Any, resolved: dict[str, Any]
    ) -> tuple[RawCmcQuote, ...]:
        """Parse a CMC response into RawCmcQuotes; fail soft on any bad shape."""
        if not isinstance(payload, dict):
            _log.warning("CMC response not an object; returning empty")
            return ()
        status = payload.get("status")
        # error_code != 0 (e.g. invalid key, rate-limit) -> treat as unavailable.
        if isinstance(status, dict) and status.get("error_code"):
            _log.warning(
                "CMC status.error_code=%s; returning empty", status.get("error_code")
            )
            return ()
        data = payload.get("data")
        if not isinstance(data, dict):
            _log.warning("CMC response missing data object; returning empty")
            return ()

        quotes: list[RawCmcQuote] = []
        for requested_symbol, record in resolved.items():
            # CMC keys the data map by SYMBOL -> LIST of colliding coins. A requested
            # symbol absent from the response is OMITTED (caller journals cmc_missing).
            raw_matches = data.get(requested_symbol)
            if isinstance(raw_matches, dict):
                # Defensive: a single-match symbol may arrive as a bare object.
                raw_matches = [raw_matches]
            if not isinstance(raw_matches, list):
                continue
            entry = self._select_match(raw_matches, record)
            if entry is None:
                # No contract-disambiguated match -> omit (caller journals cmc_missing).
                continue

            quote_usd = entry.get("quote", {})
            if isinstance(quote_usd, dict):
                quote_usd = quote_usd.get("USD", {})
            if not isinstance(quote_usd, dict):
                continue
            p7 = quote_usd.get("percent_change_7d")
            p30 = quote_usd.get("percent_change_30d")
            # NEVER fabricate momentum: a missing field -> omit this entry.
            if p7 is None or p30 is None:
                continue
            # cmc_id is READ from the matched CMC entry (not required as input).
            cmc_id = entry.get("id")
            if cmc_id is None:
                continue
            quotes.append(
                RawCmcQuote(int(cmc_id), requested_symbol, str(p7), str(p30))
            )
        return tuple(quotes)
