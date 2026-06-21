from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse


class X402Denied(RuntimeError):
    pass


@dataclass(frozen=True)
class X402Budget:
    max_request_usd: Decimal
    max_daily_usd: Decimal


class X402Client:
    def __init__(self, twak, budget: X402Budget) -> None:
        self.twak = twak
        self.budget = budget
        self.spent_today = Decimal("0")

    def get(self, url: str, *, quoted_usd: Decimal = Decimal("0.01")) -> dict:
        if urlparse(url).hostname != "pro-api.coinmarketcap.com":
            raise X402Denied("destination not allowlisted")
        if quoted_usd > self.budget.max_request_usd or self.spent_today + quoted_usd > self.budget.max_daily_usd:
            raise X402Denied("x402 budget exceeded")
        atomic = int(quoted_usd * Decimal("1000000"))
        payload = self.twak.json([
            "x402", "request", url, "--max-payment", str(atomic),
            "--prefer-network", "base", "--prefer-method", "eip3009",
            "--prefer-asset", "USDC", "--yes", "--json",
        ])
        self.spent_today += quoted_usd
        return payload
