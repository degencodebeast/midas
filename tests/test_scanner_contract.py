"""Track-1 consumption contract against the PINNED aggressive scanner.

This is the Task-1 acceptance gate. It proves that the magic-scanner commit
``midas`` is pinned to exposes the FULL surface the MIDAS spot runtime consumes:
the ``ScanResult`` package, the ``scan_pair`` execution-mode/allowed-side
profile knobs, and the scanner-owned authorization verdict
(``raw_grade``/``effective_grade``/``grade_promotion_reason``/``qml_state``) plus
the entry-provenance and canonical trade-level fields.

Every assertion is unconditional and pinned to a REAL symbol/field name verified
against the scanner source at the pin. A failing import here is a genuine
contract regression, not a thing to stub around.
"""

import inspect
from dataclasses import fields

from magic_scanner.authorization import SetupAuthorization, authorize_track1_setup
from magic_scanner.detectors.entry import EntrySetup
from magic_scanner.detectors.trade_levels import TradeLevels
from magic_scanner.scan import ScanResult, scan_pair


def test_pinned_scanner_exposes_track1_contract():
    """Baseline plan contract: ScanResult fields + scan_pair profile signature."""
    scan_result_fields = {f.name for f in fields(ScanResult)}
    assert scan_result_fields >= {"entry", "levels", "authorization"}
    # The runtime also reads the assembled inputs and the engine grade off the
    # same package, so confirm they are part of the surface.
    assert scan_result_fields >= {"inputs", "result"}

    signature = inspect.signature(scan_pair)
    assert signature.parameters["execution_mode"].default == "research_confirmation"
    assert "allowed_side" in signature.parameters


def test_authorization_verdict_exposes_grade_and_qml_surface():
    """SetupAuthorization carries the scanner-owned effective-grade verdict."""
    assert callable(authorize_track1_setup)
    auth_fields = {f.name for f in fields(SetupAuthorization)}
    assert auth_fields >= {
        "state",
        "authorized_direction",
        "raw_grade",
        "effective_grade",
        "grade_promotion_reason",
        "qml_state",
    }


def test_entry_setup_exposes_qml_provenance():
    """EntrySetup mirrors the QML identity/lifecycle the authorization gate reads."""
    entry_fields = {f.name for f in fields(EntrySetup)}
    assert entry_fields >= {"qml_id", "qml_state"}


def test_trade_levels_exposes_stop_and_campaign_dol():
    """TradeLevels resolves the canonical stop + campaign DOL the gate requires."""
    level_fields = {f.name for f in fields(TradeLevels)}
    assert level_fields >= {"stop", "campaign_dol"}
