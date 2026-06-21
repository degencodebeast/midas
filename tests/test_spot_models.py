from decimal import Decimal

import pytest

from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent


def test_spot_intent_rejects_short_and_leverage():
    setup = AuthorizedSetup.example()
    with pytest.raises(ValueError, match="spot-long"):
        SpotIntent("i-1", setup, Decimal("1"), "sell", ActionPurpose.STRATEGY)


def test_authorized_setup_requires_campaign_dol():
    with pytest.raises(ValueError, match="campaign DOL"):
        AuthorizedSetup.example(campaign_dol=None)


def test_authorized_setup_preserves_scanner_lifecycle_provenance():
    setup = AuthorizedSetup.example()
    assert setup.grade == "B"
    assert setup.raw_grade == "B"
    assert setup.grade_promotion_reason is None
    assert setup.qml_state == "active"
    assert setup.qml_id == "qml-1"
    assert setup.governing_poi_id == "h12-poi-1"
    assert setup.governing_poi_timeframe == "12h"
    assert setup.bias_alignment == "aligned"
