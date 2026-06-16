"""Live status provider for /api/status + the dashboard.

``build_status`` reads executor state and returns a JSON-able dict.  Pure —
no network, no new dependencies, stdlib only.
"""
from __future__ import annotations

from magic_agent.models import Side


def build_status(
    executor,
    *,
    mode: str,
    venue: str,
    mark_price: float,
    halted: bool = False,
) -> dict:
    """Return a JSON-serialisable status dict.

    Parameters
    ----------
    executor:
        Duck-typed against the ``PerpExecutor`` interface — must expose
        ``get_position() -> PositionState`` and
        ``get_account(mark_price=…) -> AccountState``.
    mode:
        ``"paper"`` or ``"live"``.
    venue:
        Exchange / venue name (e.g. ``"binance"``).
    mark_price:
        Current mark price used to value any open position.
    halted:
        Whether the agent loop is currently halted.

    Returns
    -------
    dict
        JSON-able dict with keys: ``mode``, ``venue``, ``halted``,
        ``equity``, ``available``, ``currency``, ``positions``.
        ``positions`` is an empty list when flat; each entry has
        ``side`` (plain string, not an enum), ``size``, ``entry_price``,
        ``stop_loss``, ``take_profit``.
    """
    pos = executor.get_position()
    account = executor.get_account(mark_price=mark_price)

    positions: list[dict] = []
    if pos.side is not Side.FLAT:
        positions.append(
            {
                "side": pos.side.value,          # str, not enum — JSON-safe
                "size": pos.size,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
            }
        )

    return {
        "mode": mode,
        "venue": venue,
        "halted": halted,
        "equity": account.equity,
        "available": account.available,
        "currency": account.currency,
        "positions": positions,
    }
