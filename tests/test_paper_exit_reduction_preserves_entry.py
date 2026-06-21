"""Regression: a PARTIAL paper risk-reduction must PRESERVE the position's entry.

THE BUG. When ``RiskPolicy.reduction_for`` returns a non-zero PARTIAL
``reduction_qty`` (a reduce-only shrink, not a full close), ``PaperExitPorts.execute``
shrinks the position by REPLACING the :class:`ReconciledPosition` in the book with a
freshly reconstructed one that re-specifies ``quantity``/``stop``/``campaign_dol`` but
NOT ``entry`` (nor is it guaranteed to carry every other field). ``entry`` becomes
``None`` -> :meth:`SpotStatusExecutor.get_position` falls back to ``entry_price=0.0``,
so after a partial reduce the dashboard regresses to a fake "Entry 0.00".

This test drives a REAL partial reduction end-to-end through the real evaluator +
pipeline + ``PaperExitPorts`` (price in range so neither the stop nor the campaign-DOL
fires; the risk state makes ``reduction_for`` return a partial, non-full reduction) and
asserts the post-reduce projection still surfaces entry 100 / stop 90 / take-profit 120
(NOT 0.0/None), the book entry is the reduced quantity carrying ``entry == Decimal("100")``,
and the reduced position survives a persist -> restart still projecting its real entry.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.lifecycle import LifecycleEvaluator
from magic_agent.paper_exits import PaperExitPorts
from magic_agent.position_manager import PositionManager, ReconciledPosition
from magic_agent.position_store import PositionStore
from magic_agent.risk_policy import PortfolioRiskState, RiskConfig, RiskPolicy
from magic_agent.status import SpotStatusExecutor


class _InRangeFrameSource:
    """A frame source whose H1 bar sits strictly inside the position's geometry.

    With ``low=95`` (> stop 90) and ``high=105`` (< campaign-DOL 120), neither a stop
    hit nor a campaign-DOL hit fires, so the ONLY protective exit reachable is the
    partial risk reduction -- exactly the buggy shrink path under test.
    """

    def _frame(self) -> pd.DataFrame:
        index = pd.DatetimeIndex(
            [pd.Timestamp("2026-06-21T11:00:00Z")], name="open_time"
        )
        return pd.DataFrame(
            {
                "open": [100.0],
                "high": [105.0],
                "low": [95.0],
                "close": [100.0],
                "volume": [1.0],
                "close_time": [pd.Timestamp("2026-06-21T12:00:00Z")],
            },
            index=index,
        )

    def closed_frames(self, candidate) -> dict[str, pd.DataFrame]:
        frame = self._frame()
        return {"1w": frame, "12h": frame, "1h": frame}


def _gold_position() -> ReconciledPosition:
    """A booked gold position: qty 4, entry 100 / stop 90 / campaign-DOL 120.

    ``stressed_loss_per_unit`` is ``entry - stop == 10`` so the reduction math below
    yields a PARTIAL reduce (2 of 4), never a full close.
    """
    return ReconciledPosition(
        "intent:gold-1",
        Decimal("4"),
        Decimal("10"),
        symbol="ZEC/USDT",
        identity_key="zec-bsc",
        entry=Decimal("100"),
        stop=Decimal("90"),
        campaign_dol=Decimal("120"),
    )


def _partial_reduction_state() -> PortfolioRiskState:
    """A risk state that makes ``reduction_for`` return a PARTIAL (not full) reduce.

    With defaults (``daily_loss_fraction=0.015``, ``max_open_risk=0.01``) and
    equity/anchor 1000, no realized daily loss:
        daily_room      = 1000 * 0.015 = 15
        open_risk_room  = 1000 * 0.01  = 10
        allowed         = min(15, 10)  = 10
        excess          = open_stressed_loss_usd (30) - allowed (10) = 20
        reduce_qty      = min(qty 4, excess 20 / stressed_loss_per_unit 10) = 2
    => a PARTIAL reduction of 2 of 4 (the in-place shrink path), not a full close.
    """
    return PortfolioRiskState(
        equity_usd=Decimal("1000"),
        cash_usd=Decimal("1000"),
        peak_equity_usd=Decimal("1000"),
        daily_anchor_usd=Decimal("1000"),
        open_stressed_loss_usd=Decimal("30"),
    )


def _build_manager(book: list[ReconciledPosition], frame_source) -> PositionManager:
    """Wire a real manager + real PaperExitPorts + real evaluator/pipeline/policy."""
    ports = PaperExitPorts(frame_source=frame_source, book=book)
    risk_policy = RiskPolicy(RiskConfig.defaults())
    state = _partial_reduction_state()
    manager = PositionManager(
        positions=lambda: list(book),
        observe=ports.observe,
        evaluator=LifecycleEvaluator(),
        pipeline=DecisionPipeline(),
        risk_policy=risk_policy,
        risk_state=lambda: state,
        sell_probe=ports.sell_probe,
        execute=ports.execute,
    )
    manager.book = book
    return manager


def test_partial_reduction_drives_a_real_partial_reduce_not_a_full_close():
    """Guard the fixture: the reduction is genuinely PARTIAL (book stays non-empty)."""
    book = [_gold_position()]
    manager = _build_manager(book, _InRangeFrameSource())

    executed = manager.process_exits("2026-06-21T12:00:00Z")

    assert executed == 1
    assert len(book) == 1, "partial reduce must keep the position open"
    assert book[0].quantity == Decimal("2"), "qty 4 reduced by 2 -> remaining 2"


def test_partial_reduction_preserves_entry_on_the_book():
    """The shrunk book entry must still carry its real entry (not None)."""
    book = [_gold_position()]
    manager = _build_manager(book, _InRangeFrameSource())

    manager.process_exits("2026-06-21T12:00:00Z")

    reduced = book[0]
    assert reduced.quantity == Decimal("2")
    assert reduced.entry == Decimal("100"), "entry must survive a partial reduce"
    assert reduced.stop == Decimal("90")
    assert reduced.campaign_dol == Decimal("120")
    assert reduced.symbol == "ZEC/USDT"
    assert reduced.identity_key == "zec-bsc"
    assert reduced.stressed_loss_per_unit == Decimal("10")
    assert reduced.intent_id == "intent:gold-1"


def test_status_projection_still_shows_real_geometry_after_partial_reduce():
    """The dashboard projection must NOT regress to entry 0.0 after a partial reduce."""
    book = [_gold_position()]
    manager = _build_manager(book, _InRangeFrameSource())

    manager.process_exits("2026-06-21T12:00:00Z")

    executor = SpotStatusExecutor(
        equity_usd=Decimal("10000"), cash_usd=Decimal("10000"), book=book
    )
    pos = executor.get_position()
    assert pos.entry_price == pytest.approx(100.0), "regressed to fake Entry 0.00"
    assert pos.stop_loss == pytest.approx(90.0)
    assert pos.take_profit == pytest.approx(120.0)
    assert pos.size == pytest.approx(2.0)


def test_reduced_position_survives_persist_then_restart(tmp_path):
    """A reduce -> persist -> restart must STILL project the real entry / take-profit."""
    book = [_gold_position()]
    manager = _build_manager(book, _InRangeFrameSource())
    manager.process_exits("2026-06-21T12:00:00Z")

    store = PositionStore(tmp_path / "positions.json")
    store.save(book)
    restored = store.load()

    assert len(restored) == 1
    assert restored[0].quantity == Decimal("2")
    assert restored[0].entry == Decimal("100"), "persisted reduce must keep entry"

    executor = SpotStatusExecutor(
        equity_usd=Decimal("10000"), cash_usd=Decimal("10000"), book=restored
    )
    pos = executor.get_position()
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.stop_loss == pytest.approx(90.0)
    assert pos.take_profit == pytest.approx(120.0)
