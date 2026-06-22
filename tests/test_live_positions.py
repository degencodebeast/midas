from decimal import Decimal

from magic_agent.live_positions import rebuild_positions_from_chain
from magic_agent.position_manager import ReconciledPosition
from magic_agent.spot_models import AuthorizedSetup, SpotIntent, ActionPurpose


class FakeBalances:
    def __init__(self, token):
        self.token = token

    def snapshot(self, identity_key):
        return {"stable": Decimal("100"), "token": self.token}


def test_rebuild_positions_from_reconciled_journal_and_chain_balance():
    setup = AuthorizedSetup.example(identity_key="0xtoken", symbol="ZEC/USDT")
    intent = SpotIntent("intent-1", setup, Decimal("5"), "buy", ActionPurpose.STRATEGY)
    record = type("Record", (), {
        "state": "RECONCILED",
        "evidence": {"intent": {"intent_id": "intent-1"}},
    })()

    rebuilt = rebuild_positions_from_chain(
        records=[record],
        intents={"intent-1": intent},
        balances=FakeBalances(Decimal("3.5")),
    )

    assert rebuilt == [
        ReconciledPosition(
            "intent-1",
            Decimal("3.5"),
            Decimal("10"),
            symbol="ZEC/USDT",
            identity_key="0xtoken",
            entry=Decimal("100"),
            stop=Decimal("90"),
            campaign_dol=Decimal("120"),
        )
    ]
