# tests/test_context.py
from magic_agent.context import CmcContextAdapter


def test_no_client_degrades_to_unavailable():
    ctx = CmcContextAdapter(client=None).get_context("BNB/USDT")
    assert ctx.status == "unavailable" and ctx.regime == "neutral"


def test_client_result_is_mapped():
    def fake(symbol):
        assert symbol == "BNB/USDT"
        return {"regime": "risk_off", "risk_flag": "elevated"}
    ctx = CmcContextAdapter(client=fake).get_context("BNB/USDT")
    assert ctx.status == "ok" and ctx.regime == "risk_off" and ctx.risk_flag == "elevated"


def test_client_error_degrades_to_unavailable():
    def boom(symbol):
        raise RuntimeError("network down")
    ctx = CmcContextAdapter(client=boom).get_context("BNB/USDT")
    assert ctx.status == "unavailable"
