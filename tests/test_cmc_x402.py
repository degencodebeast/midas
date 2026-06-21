from magic_agent.cmc_x402 import CmcX402QuoteClient


def test_cmc_x402_normalizes_id_symbol_and_momentum_fields():
    payload = {"data": {"18876": {"id": 18876, "symbol": "APE", "quote": {"USD": {
        "percent_change_7d": 2.5, "percent_change_30d": -5.0,
    }}}}}
    transport = type("Transport", (), {"get": lambda self, url: payload})()
    rows = CmcX402QuoteClient(transport).fetch(symbols=("APE",), observed_at=None)
    assert rows[0].cmc_id == 18876
    assert rows[0].symbol == "APE"
    assert rows[0].momentum_7d == "2.5"
