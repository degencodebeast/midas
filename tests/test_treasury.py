# tests/test_treasury.py
import pytest
from magic_agent.treasury import TwakTreasury


class FakeTwak:
    def __init__(self):
        self.transfers = []

    def transfer(self, to, amount, token):
        self.transfers.append({"to": to, "amount": amount, "token": token})
        return {"status": "ok"}


def test_move_collateral_transfers_to_venue():
    twak = FakeTwak()
    t = TwakTreasury(twak, venue_address="0xVENUE", token="USDT")
    ok = t.move_collateral(250.0)
    assert ok is True
    assert twak.transfers == [{"to": "0xVENUE", "amount": 250.0, "token": "USDT"}]


def test_non_positive_amount_is_rejected():
    twak = FakeTwak()
    t = TwakTreasury(twak, venue_address="0xVENUE", token="USDT")
    assert t.move_collateral(0.0) is False
    assert twak.transfers == []
