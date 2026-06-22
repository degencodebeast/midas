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
    resolves the requested competition symbols to CMC **ids** via the identity
    registry (NOT symbols — symbols collide across listings), batches them into
    ONE ``GET /v2/cryptocurrency/quotes/latest?id=<comma>&convert=USD`` call with
    the ``X-CMC_PRO_API_KEY`` header, and normalizes
    ``data[id].quote.USD.percent_change_7d/30d`` into :class:`RawCmcQuote`.

    AUTHORITY: this client only supplies rank/momentum (observe / veto / clamp
    inputs). It carries NO setup-authorizing role — the scanner remains the sole
    setup authority. Read-only market data: no funds, no orders, no secrets in code
    (the key is read from env by the caller and injected here).

    FAIL-SOFT (load-bearing): it NEVER raises into the trade path and NEVER
    fabricates a momentum value. On HTTP error / non-200 / CMC
    ``status.error_code != 0`` / parse failure it returns what it can (empty on a
    total failure) and lets the caller journal ``cmc_unavailable``; a requested id
    absent from a good response is simply omitted (caller journals ``cmc_missing``).
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
        # Resolve symbols -> CMC ids via the registry. A symbol with no registry id
        # cannot be queried by id and is dropped here (the caller journals it as
        # cmc_missing once it is absent from the snapshot). Keep an id -> symbol map
        # so the response (keyed by id) normalizes back to the competition symbol.
        id_to_symbol: dict[int, str] = {}
        for symbol in symbols:
            record = self._registry.get_by_symbol(symbol)
            cmc_id = getattr(record, "cmc_id", None) if record is not None else None
            if cmc_id is None:
                continue
            id_to_symbol.setdefault(int(cmc_id), symbol)
        if not id_to_symbol:
            # Nothing resolvable -> nothing to query (no point spending a credit).
            return ()

        ids = ",".join(str(cmc_id) for cmc_id in id_to_symbol)
        query = urllib.parse.urlencode({"id": ids, "convert": "USD"})
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

        return self._normalize(payload, id_to_symbol)

    def _normalize(
        self, payload: Any, id_to_symbol: dict[int, str]
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
        for cmc_id, requested_symbol in id_to_symbol.items():
            # CMC keys the data map by stringified id; a requested id absent from
            # the response is OMITTED (caller journals cmc_missing), not invented.
            entry = data.get(str(cmc_id))
            if not isinstance(entry, dict):
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
            # Normalize to the competition symbol (the registry resolved id->symbol);
            # fall back to the API symbol only if the request map lost it.
            symbol = requested_symbol or str(entry.get("symbol", ""))
            quotes.append(RawCmcQuote(int(cmc_id), symbol, str(p7), str(p30)))
        return tuple(quotes)
