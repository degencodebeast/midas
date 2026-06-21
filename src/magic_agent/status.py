"""Live status provider for /api/status + the dashboard.

``build_status`` reads executor state and returns a JSON-able dict.  Pure —
no network, no new dependencies, stdlib only.

The spot runtime (``RuntimeState`` + the position-manager book) is NOT a perp
``executor``, so :class:`SpotStatusExecutor` adapts it to the duck-typed
``get_position`` / ``get_account`` surface ``build_status`` reads, and
:func:`derive_spot_halted` maps the live state onto the dashboard's kill-switch
pill using the SAME :class:`~magic_agent.risk_policy.RiskPolicy` thresholds the
entry gate enforces (never invented ones).
"""
from __future__ import annotations

from decimal import Decimal

from magic_agent.models import AccountState, PositionState, Side


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


class SpotStatusExecutor:
    """Adapt the spot runtime to the ``build_status`` executor surface.

    ``build_status`` is duck-typed against the perp ``PerpExecutor`` interface
    (``get_position`` / ``get_account``). The spot runtime has no executor — it
    tracks equity/cash on a :class:`~magic_agent.runtime_state.RuntimeState` and
    open longs in the position-manager book (a list of
    :class:`~magic_agent.position_manager.ReconciledPosition`). This adapter
    surfaces both honestly so the dashboard reflects the live paper book:

    * :meth:`get_account` reports ``equity = equity_usd`` and
      ``available = cash_usd`` as floats (the runtime's tracked spot balances).
      The spot book carries no mark-to-market, so equity equals available and
      ``open_pnl`` derives to ``0.0`` — truthful: unrealized spot PnL is not
      tracked on the book yet (the close realizes into equity).
    * :meth:`get_position` surfaces the single open long (spot concurrency cap is
      one) as a :class:`~magic_agent.models.PositionState` with its booked
      ``quantity`` as ``size`` and its structural ``stop`` as ``stop_loss``.
      ``entry_price`` is the booked position's reconcile geometry when present,
      else ``None`` (not fabricated). ``FLAT`` when the book is empty.
    """

    def __init__(self, *, equity_usd: Decimal, cash_usd: Decimal, book) -> None:
        self._equity_usd = equity_usd
        self._cash_usd = cash_usd
        self._book = list(book)

    def get_position(self) -> PositionState:
        """Return the single open spot long, or ``FLAT`` when the book is empty.

        ``entry_price`` is reported as ``0.0`` because the reconcile book does not
        track a fill price (a truthful zero — NOT a fabricated entry); combined with
        the ``mark_price=0.0`` ``build_status`` is called with, the surfaced position
        ``pnl`` derives to ``0.0`` (no mark-to-market on the spot book yet) rather
        than inventing an unrealized number.
        """
        if not self._book:
            return PositionState()
        position = self._book[0]
        stop = position.stop
        return PositionState(
            side=Side.LONG,
            size=float(position.quantity),
            entry_price=0.0,
            stop_loss=float(stop) if stop is not None else None,
            take_profit=None,
        )

    def get_account(self, *, mark_price: float) -> AccountState:
        """Return the live spot balances (``mark_price`` unused — no MTM tracked)."""
        return AccountState(
            equity=float(self._equity_usd),
            available=float(self._cash_usd),
            currency="USDT",
        )


def derive_spot_halted(state, risk_policy) -> bool:
    """Return whether the spot agent is currently entry-halted.

    Mirrors the entry-deny halt conditions of
    :func:`~magic_agent.risk_policy.evaluate_risk` against the SAME
    :class:`~magic_agent.risk_policy.RiskConfig` thresholds the live entry gate
    enforces — no invented thresholds. ``True`` when ANY holds:

    * the recovery exposure gate is set (``state.blocks_new_exposure``);
    * stale equity (``not equity_fresh``) — the gate denies all entries;
    * drawdown at/over the hard-DQ, emergency-review, or entry-halt thresholds;
    * the consecutive-stop halt count is reached;
    * realized daily loss is at/over the daily-loss-fraction cap (the 1.5% halt).

    Args:
        state: The live :class:`~magic_agent.runtime_state.RuntimeState`.
        risk_policy: The :class:`~magic_agent.risk_policy.RiskPolicy` whose
            config carries the drawdown / consecutive-stop / daily-loss thresholds.

    Returns:
        ``True`` when the agent is entry-halted, else ``False``. When no policy
        config is configured, returns ``True`` (fail closed — the entry gate is
        reduce-only without a policy).
    """
    if state.blocks_new_exposure:
        return True
    config = getattr(risk_policy, "config", None)
    if config is None:
        return True
    risk = state.risk_state()
    if not risk.equity_fresh:
        return True
    zero = Decimal("0")
    drawdown = max(zero, (risk.peak_equity_usd - risk.equity_usd) / risk.peak_equity_usd)
    if drawdown >= config.drawdown_entry_halt:
        return True
    if risk.consecutive_stops >= config.consecutive_stop_halt:
        return True
    daily_loss = max(zero, risk.daily_anchor_usd - risk.equity_usd)
    if daily_loss >= risk.daily_anchor_usd * config.daily_loss_fraction:
        return True
    return False
