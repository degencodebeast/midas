# tests/test_scanner_gateway.py
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from magic_agent.scanner_gateway import authorized_setup_from_scan


def _scan_result(state: str = "authorized", *, campaign_dol: bool = True):
    entry = SimpleNamespace(
        entry=97.0, qml_reclaim_time=pd.Timestamp("2026-06-21T00:00:00Z"),
        qml_id="Long:10:97", qml_state="active",
    )
    levels = SimpleNamespace(
        stop=SimpleNamespace(level=89.5, source="h12_pivot", anchor=90.0, anchor_bar=8),
        campaign_dol=(SimpleNamespace(level=120.0, source="prior_week") if campaign_dol else None),
    )
    return SimpleNamespace(
        authorization=SimpleNamespace(
            state=state, authorized_direction="Long" if state == "authorized" else None,
            raw_grade="C", effective_grade="B-",
            grade_promotion_reason="track1_counter_bias_structural",
            governing_poi=SimpleNamespace(
                kind="Breaker", origin_bar=5, bottom=95.0, top=100.0,
            ),
        ),
        levels=levels, entry=entry, symbol="ZEC/USDT",
        result=SimpleNamespace(rating="C", regime="counter-bias"),
        inputs=SimpleNamespace(draw_on_liquidity=False),
    )


def test_only_authorized_scan_maps_to_setup():
    result = _scan_result()
    setup = authorized_setup_from_scan(result, identity_key="zec-bsc", scanner_commit="abc")
    assert setup is not None
    assert setup.entry == Decimal("97.0")
    assert setup.structural_stop == Decimal("89.5")
    assert setup.campaign_dol == Decimal("120.0")
    assert setup.grade == "B-"
    assert setup.raw_grade == "C"
    assert setup.grade_promotion_reason == "track1_counter_bias_structural"
    assert setup.qml_state == "active"
    assert setup.qml_id == "Long:10:97"
    assert setup.governing_poi_id == "12h:Breaker:5:95:100"
    assert setup.bias_alignment == "counter_bias"


def test_monitor_only_and_unresolved_dol_create_no_setup():
    assert authorized_setup_from_scan(_scan_result("monitor_only"), identity_key="zec-bsc", scanner_commit="abc") is None
    assert authorized_setup_from_scan(_scan_result(campaign_dol=False), identity_key="zec-bsc", scanner_commit="abc") is None


def test_unknown_scanner_regime_fails_closed():
    scan = _scan_result()
    scan.result.regime = "none"
    with pytest.raises(ValueError, match="unsupported scanner regime"):
        authorized_setup_from_scan(scan, identity_key="zec-bsc", scanner_commit="abc")
