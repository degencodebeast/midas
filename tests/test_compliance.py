"""Tests for the compliance ledger (Task 14 — TDD).

``ComplianceLedger`` counts confirmed eligible swaps against verified
competition-day boundaries and emits alerts ONLY. It has no execution method:
it can never authorize, size, or place a trade — that authority lives solely in
the scanner -> risk -> pipeline -> coordinator path.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from magic_agent.compliance import ComplianceAlert, ComplianceLedger


def _reconciled(intent_id: str = "intent:1"):
    return SimpleNamespace(state="RECONCILED", intent=SimpleNamespace(intent_id=intent_id))


def test_no_confirmed_swaps_emits_daily_qualification_at_risk():
    ledger = ComplianceLedger()
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    alerts = ledger.observe([], now)
    assert [a.code for a in alerts] == ["daily_qualification_at_risk"]


def test_a_confirmed_swap_clears_the_daily_alert():
    ledger = ComplianceLedger()
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    alerts = ledger.observe([_reconciled()], now)
    assert alerts == []


def test_only_reconciled_swaps_count_toward_qualification():
    ledger = ComplianceLedger()
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    # A non-reconciled record (e.g. BROADCAST_UNKNOWN) is NOT a confirmed swap.
    pending = SimpleNamespace(state="BROADCAST_UNKNOWN", intent=SimpleNamespace(intent_id="x"))
    alerts = ledger.observe([pending], now)
    assert [a.code for a in alerts] == ["daily_qualification_at_risk"]


def test_ledger_has_no_execution_method():
    # The ledger emits alerts only; it must NOT expose any trade/execute/submit API.
    ledger = ComplianceLedger()
    for forbidden in ("execute", "submit", "authorize", "trade", "place_order"):
        assert not hasattr(ledger, forbidden)


def test_alert_is_an_alert_value_object():
    alert = ComplianceAlert("daily_qualification_at_risk")
    assert alert.code == "daily_qualification_at_risk"
