class _Twak:
    def __init__(self) -> None:
        self.calls = []

    def json(self, args):
        self.calls.append(args)
        return {"success": True, "data": {}}


from decimal import Decimal

import pytest

from magic_agent.x402 import X402Budget, X402Client, X402Denied


def test_non_cmc_destination_and_over_budget_are_denied():
    client = X402Client(_Twak(), X402Budget(Decimal("0.01"), Decimal("0.10")))
    with pytest.raises(X402Denied):
        client.get("https://evil.example/data")
    with pytest.raises(X402Denied):
        client.get("https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?id=1", quoted_usd=Decimal("0.02"))
