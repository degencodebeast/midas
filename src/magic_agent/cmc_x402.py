from urllib.parse import urlencode

from magic_agent.cmc_source import RawCmcQuote


class CmcX402QuoteClient:
    def __init__(self, transport, *, chunk_size: int = 50) -> None:
        self.transport = transport
        self.chunk_size = chunk_size

    def fetch(self, *, symbols: tuple[str, ...], observed_at) -> tuple[RawCmcQuote, ...]:
        rows = []
        for start in range(0, len(symbols), self.chunk_size):
            chunk = symbols[start:start + self.chunk_size]
            query = urlencode({"symbol": ",".join(chunk), "convert": "USD"})
            payload = self.transport.get(
                f"https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?{query}",
            )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("CMC response missing data object")
            for key, item in data.items():
                quote = item.get("quote", {}).get("USD", {})
                required = (item.get("symbol"), quote.get("percent_change_7d"), quote.get("percent_change_30d"))
                if any(value is None for value in required):
                    raise ValueError(f"CMC quote {key} missing momentum fields")
                rows.append(RawCmcQuote(
                    int(item.get("id", key)), str(item["symbol"]),
                    str(quote["percent_change_7d"]), str(quote["percent_change_30d"]),
                ))
        return tuple(rows)
