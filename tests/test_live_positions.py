import logging
from dataclasses import asdict
from decimal import Decimal

from magic_agent.live_positions import (
    intents_from_journal,
    rebuild_positions_from_chain,
)
from magic_agent.position_manager import ReconciledPosition
from magic_agent.spot_models import AuthorizedSetup, SpotIntent, ActionPurpose


class FakeBalances:
    def __init__(self, token):
        self.token = token

    def snapshot(self, identity_key):
        return {"stable": Decimal("100"), "token": self.token}


class PerTokenBalances:
    """Balances keyed by identity_key, with an optional held_tokens() enumeration."""

    def __init__(self, by_key, held=None):
        self._by_key = by_key
        self._held = held

    def snapshot(self, identity_key):
        return {"stable": Decimal("100"), "token": self._by_key.get(identity_key, Decimal("0"))}

    def held_tokens(self):
        return self._held or []


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


def _reconciled_record(intent):
    """A RECONCILED journal record carrying ``evidence['intent'] = asdict(intent)``,
    JSON-shaped (Decimals serialized to strings, as a real restart reload yields)."""
    import json

    from magic_agent.state_journal import _json_default

    evidence = json.loads(json.dumps({"intent": asdict(intent)}, default=_json_default))
    return type("Record", (), {
        "intent_id": intent.intent_id,
        "state": "RECONCILED",
        "evidence": evidence,
    })()


def test_intents_from_journal_reconstructs_typed_intent_from_serialized_evidence():
    setup = AuthorizedSetup.example(identity_key="0xtoken", symbol="ZEC/USDT")
    intent = SpotIntent("intent-1", setup, Decimal("5"), "buy", ActionPurpose.STRATEGY)
    record = _reconciled_record(intent)

    intents = intents_from_journal([record])

    assert set(intents) == {"intent-1"}
    rebuilt = intents["intent-1"]
    assert rebuilt.setup.entry == Decimal("100")
    assert rebuilt.setup.structural_stop == Decimal("90")
    assert rebuilt.setup.campaign_dol == Decimal("120")
    assert rebuilt.setup.identity_key == "0xtoken"
    assert rebuilt.quantity == Decimal("5")


def test_position_no_longer_held_on_chain_is_dropped(caplog):
    setup = AuthorizedSetup.example(identity_key="0xtoken", symbol="ZEC/USDT")
    intent = SpotIntent("intent-2", setup, Decimal("5"), "buy", ActionPurpose.STRATEGY)
    record = _reconciled_record(intent)

    with caplog.at_level(logging.INFO, logger="magic_agent.live_positions"):
        rebuilt = rebuild_positions_from_chain(
            records=[record],
            intents={"intent-2": intent},
            balances=FakeBalances(Decimal("0")),  # wallet no longer holds the token
        )

    assert rebuilt == []
    assert any("no longer held" in r.message for r in caplog.records)


def test_unmanaged_on_chain_balance_is_warned_and_left_out(caplog):
    setup = AuthorizedSetup.example(identity_key="0xtoken-a", symbol="ZEC/USDT")
    intent = SpotIntent("intent-a", setup, Decimal("5"), "buy", ActionPurpose.STRATEGY)
    record = _reconciled_record(intent)
    balances = PerTokenBalances(
        by_key={"0xtoken-a": Decimal("3")},
        held=[
            {"identity_key": "0xtoken-a", "quantity": Decimal("3")},     # managed
            {"identity_key": "0xtoken-c", "quantity": Decimal("7")},     # UNMANAGED
        ],
    )

    with caplog.at_level(logging.WARNING, logger="magic_agent.live_positions"):
        rebuilt = rebuild_positions_from_chain(
            records=[record],
            intents={"intent-a": intent},
            balances=balances,
        )

    # Only the journal-backed token is in the book; the unmanaged one is left out.
    assert [p.identity_key for p in rebuilt] == ["0xtoken-a"]
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("unmanaged on-chain balance" in m and "0xtoken-c" in m for m in warnings)


def _sell_record(buy_intent_id, state="RECONCILED", reason="stop", qty="3"):
    """A protective-sell journal record keyed sell:{buy_intent_id}:{reason}:{qty}."""
    return type("Record", (), {
        "intent_id": f"sell:{buy_intent_id}:{reason}:{qty}",
        "state": state,
        "evidence": {"side": "sell", "identity_key": "0xtoken"},
    })()


def test_position_with_reconciled_sell_record_is_not_rebuilt(caplog):
    # A reconciled BUY whose token STILL shows on the wallet snapshot but which has
    # a RECONCILED protective-SELL record must be treated as CLOSED (a restart
    # mid-exit must not rebuild a just-sold position and re-drive the exit).
    setup = AuthorizedSetup.example(identity_key="0xtoken", symbol="ZEC/USDT")
    intent = SpotIntent("intent-1", setup, Decimal("3"), "buy", ActionPurpose.STRATEGY)
    buy_record = _reconciled_record(intent)
    sell_record = _sell_record("intent-1", state="RECONCILED")

    with caplog.at_level(logging.INFO, logger="magic_agent.live_positions"):
        rebuilt = rebuild_positions_from_chain(
            records=[buy_record, sell_record],
            intents={"intent-1": intent},
            balances=FakeBalances(Decimal("3")),  # wallet STILL shows the token
        )

    assert rebuilt == []  # excluded despite a positive wallet balance
    assert any("protective sell is journaled" in r.message for r in caplog.records)


def test_position_with_submitted_sell_record_is_not_rebuilt():
    # A SUBMITTED (in-flight, not yet reconciled) sell still closes the position.
    setup = AuthorizedSetup.example(identity_key="0xtoken", symbol="ZEC/USDT")
    intent = SpotIntent("intent-1", setup, Decimal("3"), "buy", ActionPurpose.STRATEGY)
    buy_record = _reconciled_record(intent)
    sell_record = _sell_record("intent-1", state="SUBMITTED")

    rebuilt = rebuild_positions_from_chain(
        records=[buy_record, sell_record],
        intents={"intent-1": intent},
        balances=FakeBalances(Decimal("3")),
    )

    assert rebuilt == []


def test_position_with_broadcast_unknown_sell_is_still_rebuilt():
    # A BROADCAST_UNKNOWN sell is NOT a confirmed close; the position stays managed.
    setup = AuthorizedSetup.example(identity_key="0xtoken", symbol="ZEC/USDT")
    intent = SpotIntent("intent-1", setup, Decimal("3"), "buy", ActionPurpose.STRATEGY)
    buy_record = _reconciled_record(intent)
    sell_record = _sell_record("intent-1", state="BROADCAST_UNKNOWN")

    rebuilt = rebuild_positions_from_chain(
        records=[buy_record, sell_record],
        intents={"intent-1": intent},
        balances=FakeBalances(Decimal("3")),
    )

    assert [p.intent_id for p in rebuilt] == ["intent-1"]
