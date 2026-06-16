# src/magic_agent/treasury.py
"""TWAK self-custody treasury — moves USDT collateral to the perp venue.

``twak`` is injected (the Trust Wallet Agent Kit client/CLI wrapper in the CLI, a
stub in tests) exposing ``transfer(to, amount, token)``. TWAK is the self-custody
treasury/collateral layer — NOT the perp executor (see design spec §4.6).
"""
from __future__ import annotations

from typing import Any


class TwakTreasury:
    def __init__(self, twak: Any, *, venue_address: str, token: str = "USDT") -> None:
        self._twak = twak
        self._venue_address = venue_address
        self._token = token

    def move_collateral(self, amount: float) -> bool:
        if amount <= 0:
            return False
        self._twak.transfer(self._venue_address, amount, self._token)
        return True
