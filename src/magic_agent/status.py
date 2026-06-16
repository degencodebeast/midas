"""Live status provider for /api/status + the dashboard.

``build_status`` reads executor state and returns a JSON-able dict.  Pure —
no network, no new dependencies, stdlib only.
"""
from __future__ import annotations

from magic_agent.models import Side


def build_status(
    executor,
    *,
    symbol: str,
    mode: str,
    venue: str,
    mark_price: float,
    starting_equity: float,
    halted: bool = False,
    daily_loss: float | None = None,
    max_daily_loss: float | None = None,
    agent_id: str | None = None,
) -> dict:
    """Return a JSON-serialisable status dict matching the dashboard contract.

    Parameters
    ----------
    executor:
        Duck-typed against the ``PerpExecutor`` interface — must expose
        ``get_position() -> PositionState`` and
        ``get_account(mark_price=…) -> AccountState``.
    symbol:
        The traded symbol (e.g. ``"BNB/USDT"``) — surfaced per-position so the
        dashboard can label positions.
    mode:
        ``"paper"`` or ``"live"``.
    venue:
        Exchange / venue name (e.g. ``"binance"``).
    mark_price:
        Current mark price used to value any open position.
    starting_equity:
        The session's starting equity (realized cash baseline). Used to derive
        ``realized_pnl`` as ``account.available - starting_equity``.
    halted:
        Whether the agent loop is currently halted (the daily-loss kill-switch
        state). Surfaced as the dashboard's kill-switch pill.
    daily_loss:
        Current realized loss magnitude for the session (``0.0`` when in profit;
        ``None`` when not reported). Backs the dashboard's daily-loss meter.
    max_daily_loss:
        The daily-loss kill-switch cap (``None`` when no cap is configured).
    agent_id:
        The ERC-8004 on-chain agent identity (``None`` when unregistered — the
        dashboard then shows an honest "unregistered" badge, never a fake id).

    Returns
    -------
    dict
        JSON-able dict with keys: ``mode``, ``venue``, ``halted``,
        ``equity``, ``available``, ``currency``, ``realized_pnl``,
        ``open_pnl``, ``daily_loss``, ``max_daily_loss``, ``agent_id``,
        ``positions``.

        ``realized_pnl`` = ``account.available - starting_equity`` (realized
        cash delta); ``open_pnl`` = ``account.equity - account.available``
        (unrealized mark-to-market on any open position).

        ``positions`` is an empty list when flat; each entry has
        ``side`` (plain string, not an enum), ``size``, ``entry_price``,
        ``stop_loss``, ``take_profit``, plus the dashboard fields ``symbol``,
        ``qty`` (= ``size``), and ``pnl`` (the position's unrealized PnL,
        ``sign * (mark_price - entry_price) * size`` with ``sign`` +1 for long,
        -1 for short).
    """
    pos = executor.get_position()
    account = executor.get_account(mark_price=mark_price)

    positions: list[dict] = []
    if pos.side is not Side.FLAT:
        sign = 1.0 if pos.side is Side.LONG else -1.0
        pnl = sign * (mark_price - pos.entry_price) * pos.size
        positions.append(
            {
                "symbol": symbol,
                "side": pos.side.value,          # str, not enum — JSON-safe
                "size": pos.size,
                "qty": pos.size,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "pnl": pnl,
            }
        )

    return {
        "mode": mode,
        "venue": venue,
        "halted": halted,
        "equity": account.equity,
        "available": account.available,
        "currency": account.currency,
        "realized_pnl": account.available - starting_equity,
        "open_pnl": account.equity - account.available,
        "daily_loss": daily_loss,
        "max_daily_loss": max_daily_loss,
        "agent_id": agent_id,
        "positions": positions,
    }
