# tests/test_scanner_gateway.py
from magic_agent.models import Side
from magic_agent.scanner_gateway import ScannerGateway


class _Inputs:
    def __init__(self, d): self.trade_direction = d
class _Result:
    def __init__(self, r, g="risk_on"): self.rating = r; self.regime = g
class _Entry:
    def __init__(self, e, q): self.entry = e; self.qml_key_level = q; self.confirmation_kind = "chained_scob"
class _Scan:
    def __init__(self, sym, d, r, e, q):
        self.symbol = sym; self.inputs = _Inputs(d); self.result = _Result(r)
        self.entry = _Entry(e, q); self.confirmation_kind = "chained_scob"


def test_scan_returns_mapped_setup():
    gw = ScannerGateway(scan_fn=lambda syms, **k: [_Scan("BNB/USDT", "Long", "A", 600.0, 594.0)])
    s = gw.scan("BNB/USDT")
    assert s is not None and s.symbol == "BNB/USDT" and s.direction is Side.LONG


def test_scan_returns_none_when_no_qualifying_result():
    gw = ScannerGateway(scan_fn=lambda syms, **k: [])
    assert gw.scan("BNB/USDT") is None


def test_scan_passes_symbol_through_to_scan_fn():
    seen = {}
    def fake(syms, **k):
        seen["syms"] = syms
        return []
    ScannerGateway(scan_fn=fake).scan("ETH/USDT")
    assert seen["syms"] == ["ETH/USDT"]
