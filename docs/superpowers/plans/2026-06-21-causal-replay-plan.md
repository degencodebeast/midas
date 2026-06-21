# MIDAS Causal Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a causal, single-wallet replay that invokes MIDAS's live `DecisionPipeline` and RiskPolicy unchanged, models next-executable fills and costs, and produces reproducible ZEC/control and full-universe evidence.

**Architecture:** Replay substitutes only historical frames, clock, execution, wallet, and persistence adapters. Scanner authorization, Direct-QML geometry, canonical stop, campaign DOL, sizing, portfolio gates, and lifecycle decisions are imported from the live runtime. Every decision and accounting mutation is append-only and reproducible from a pinned manifest.

**Tech Stack:** Python 3.11+, `Decimal`, pandas, pytest, `magic-scanner`, shared MIDAS runtime contracts, JSONL/JSON artifacts.

---

## Prerequisites And File Structure

Prerequisite: runtime-plan Tasks 1-8 must exist so replay can import `AuthorizedSetup`, `DecisionPipeline`, `RiskPolicy`, identity records, and journals. Replay must never import `magic_agent.twak`, `subprocess`, or live signing code.

| File | Responsibility |
|---|---|
| `src/magic_agent/replay/contracts.py` | Manifest, event, fill, and report contracts |
| `src/magic_agent/replay/data.py` | UTC OHLCV audit and as-of slicing |
| `src/magic_agent/replay/clock.py` | Closed-H1 event clock |
| `src/magic_agent/replay/momentum.py` | Reconstructable 7d/30d selection lane |
| `src/magic_agent/replay/costs.py` | Versioned real-wallet/competition cost scenarios |
| `src/magic_agent/replay/execution.py` | Pending orders, next-executable fills, exits |
| `src/magic_agent/replay/wallet.py` | Single wallet, marks, positions, drawdown |
| `src/magic_agent/replay/engine.py` | Event orchestration through shared pipeline |
| `src/magic_agent/replay/events.py` | Append-only event journal and reconciliation |
| `src/magic_agent/replay/metrics.py` | Signal, trade, portfolio, uncertainty metrics |
| `src/magic_agent/replay/report.py` | Bias-first machine/human reports |
| `src/magic_agent/replay/cli.py` | Frozen diagnostic and full-universe commands |

### Task 1: Define Immutable Replay Contracts And Manifest Hashes

**Files:**
- Create: `src/magic_agent/replay/__init__.py`
- Create: `src/magic_agent/replay/contracts.py`
- Create: `tests/replay/test_contracts.py`

- [ ] **Step 1: Write manifest validation tests**

```python
from decimal import Decimal

from magic_agent.risk_policy import PortfolioRiskState

import pytest

from magic_agent.replay.contracts import ReplayConfig


def test_manifest_requires_pins_and_utc_window():
    config = ReplayConfig.diagnostic()
    assert config.start_utc.endswith("Z") and config.end_utc.endswith("Z")
    assert config.symbols == ("ZEC/USDT", "ETH/USDT", "TRX/USDT", "APE/USDT")
    assert config.initial_equity_usd == Decimal("1000")
    with pytest.raises(ValueError, match="commit"):
        ReplayConfig.diagnostic(scanner_commit="")
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_contracts.py -q`
Expected: FAIL because `magic_agent.replay` does not exist.

- [ ] **Step 3: Implement frozen contracts**

```python
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ReplayConfig:
    run_id: str
    start_utc: str
    end_utc: str
    symbols: tuple[str, ...]
    initial_equity_usd: Decimal
    scanner_commit: str
    midas_commit: str
    decision_config_hash: str
    risk_config_hash: str
    selector_mode: Literal["full_universe", "historical_momentum", "forward_shadow"]
    execution_model: str
    entry_ttl_bars: int
    cost_scenario: Literal["base", "stress", "zero_cost_diagnostic"]
    identity_snapshot_hash: str
    data_snapshot_hash: str

    def __post_init__(self) -> None:
        if not self.scanner_commit or not self.midas_commit:
            raise ValueError("scanner and MIDAS commit pins are required")
        if not self.start_utc.endswith("Z") or not self.end_utc.endswith("Z"):
            raise ValueError("replay window must be UTC")

    @classmethod
    def diagnostic(cls, **changes: object) -> "ReplayConfig":
        base = cls(
            "zec-diagnostic-v1", "2026-01-01T00:00:00Z", "2026-06-20T23:59:59Z",
            ("ZEC/USDT", "ETH/USDT", "TRX/USDT", "APE/USDT"), Decimal("1000"),
            "scanner-sha", "midas-sha", "decision-hash", "risk-hash",
            "full_universe", "next_open_v1", 4, "base", "identity-hash", "data-hash",
        )
        return replace(base, **changes)


@dataclass(frozen=True)
class ReplayEvent:
    run_id: str
    event_id: str
    event_type: str
    event_time_utc: str
    decision_time_utc: str | None
    identity_key: str | None
    symbol: str | None
    source_watermark: dict[str, str]
    reason_codes: tuple[str, ...]
    payload: dict
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_contracts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay tests/replay/test_contracts.py
git commit -m "feat(replay): add immutable run and event contracts"
```

### Task 2: Audit Historical Data And Build Causal As-Of Frames

**Files:**
- Create: `src/magic_agent/replay/data.py`
- Create: `src/magic_agent/replay/clock.py`
- Create: `tests/replay/test_data.py`
- Create: `tests/replay/test_clock.py`

- [ ] **Step 1: Write future-bar and invalid-data tests**

```python
import pandas as pd

from magic_agent.replay.data import audit_frame, as_of_frames


def _frame(times: list[str]) -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.to_datetime(times, utc=True), name="open_time")
    return pd.DataFrame({
        "open": [100.0] * len(index), "high": [101.0] * len(index),
        "low": [99.0] * len(index), "close": [100.0] * len(index),
        "close_time": index + pd.Timedelta(hours=1),
    }, index=index)


def test_as_of_excludes_still_forming_htf_bars():
    decision_time = pd.Timestamp("2026-01-02T12:00:00Z")
    frames = {"1h": _frame(["2026-01-02T10:00:00Z", "2026-01-02T12:00:00Z"])}
    sliced = as_of_frames(frames, decision_time)
    assert all((frame["close_time"] <= decision_time).all() for frame in sliced.values())


def test_invalid_ohlc_returns_reason_instead_of_disappearing():
    frame = _frame(["2026-01-02T10:00:00Z"])
    frame.loc[frame.index[0], "low"] = frame.loc[frame.index[0], "high"] + 1
    result = audit_frame(frame)
    assert result.valid is False
    assert "invalid_ohlc" in result.reason_codes
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_data.py tests/replay/test_clock.py -q`
Expected: FAIL because causal data adapters do not exist.

- [ ] **Step 3: Implement UTC audit and close-time slicing**

```python
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DataAudit:
    valid: bool
    reason_codes: tuple[str, ...]


def audit_frame(frame: pd.DataFrame) -> DataAudit:
    reasons: list[str] = []
    required = {"open", "high", "low", "close", "close_time"}
    if not required.issubset(frame.columns):
        reasons.append("missing_columns")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        reasons.append("timestamp_order")
    if frame.index.tz is None:
        reasons.append("timezone_naive")
    if required.issubset(frame.columns):
        invalid = (frame[["open", "high", "low", "close"]] <= 0).any(axis=None)
        invalid = invalid or (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any()
        invalid = invalid or (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any()
        if invalid:
            reasons.append("invalid_ohlc")
    return DataAudit(not reasons, tuple(reasons))


def as_of_frames(frames: dict[str, pd.DataFrame], decision_time: pd.Timestamp) -> dict[str, pd.DataFrame]:
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    return {tf: frame.loc[frame["close_time"] <= decision_time].copy() for tf, frame in frames.items()}
```

`clock.py` yields sorted unique H1 `close_time` values inside the manifest window. Missing/invalid required frames emit `UNSCANNABLE` or `DATA_INVALID` events and stay in coverage denominators.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_data.py tests/replay/test_clock.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/data.py src/magic_agent/replay/clock.py tests/replay/test_data.py tests/replay/test_clock.py
git commit -m "feat(replay): add causal clock and frame audit"
```

### Task 3: Reconstruct Point-In-Time Momentum Without Current CMC Leakage

**Files:**
- Create: `src/magic_agent/replay/momentum.py`
- Create: `tests/replay/test_momentum.py`

- [ ] **Step 1: Write as-of feature tests**

```python
import pandas as pd

from magic_agent.replay.momentum import momentum_snapshot


def test_future_prices_do_not_change_past_momentum():
    index = pd.date_range("2026-01-01", periods=90, freq="1D", tz="UTC")
    daily_frame = pd.DataFrame({
        "close": [100.0 + value for value in range(90)],
        "close_time": index + pd.Timedelta(days=1),
    }, index=index)
    as_of = pd.Timestamp("2026-03-01T00:00:00Z")
    original = momentum_snapshot(daily_frame, as_of)
    mutated = daily_frame.copy()
    mutated.loc[mutated.index > as_of, "close"] *= 100
    assert momentum_snapshot(mutated, as_of) == original
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_momentum.py -q`
Expected: FAIL because `momentum` does not exist.

- [ ] **Step 3: Implement closed-observation returns**

```python
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd


@dataclass(frozen=True)
class MomentumSnapshot:
    observed_at: str
    return_7d: Decimal | None
    return_30d: Decimal | None


def momentum_snapshot(frame: pd.DataFrame, as_of: pd.Timestamp) -> MomentumSnapshot:
    closed = frame.loc[frame["close_time"] <= as_of]
    if closed.empty:
        return MomentumSnapshot(as_of.isoformat(), None, None)
    current = Decimal(str(closed["close"].iloc[-1]))

    def window(days: int) -> Decimal | None:
        eligible = closed.loc[closed["close_time"] <= as_of - pd.Timedelta(days=days)]
        if eligible.empty:
            return None
        prior = Decimal(str(eligible["close"].iloc[-1]))
        return current / prior - Decimal("1")

    return MomentumSnapshot(as_of.isoformat(), window(7), window(30))
```

Historical mode may use these features plus reconstructable volume/volatility fields. Current narratives, sectors, and Agent Hub outputs are absent. Missing historical macro sets clamp `1.0` and adds `macro_forward_only` to report bias flags.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_momentum.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/momentum.py tests/replay/test_momentum.py
git commit -m "feat(replay): add reconstructable momentum lane"
```

### Task 4: Model Next-Executable Orders And Versioned Costs

**Files:**
- Create: `src/magic_agent/replay/costs.py`
- Create: `src/magic_agent/replay/execution.py`
- Create: `tests/replay/test_execution.py`
- Create: `tests/replay/test_costs.py`

- [ ] **Step 1: Write no-retrofill, expiry, and stop-first tests**

```python
from decimal import Decimal

from magic_agent.replay.execution import PendingOrder, simulate_bar


def test_decision_bar_touch_does_not_retroactively_fill():
    order = PendingOrder.example(decision_bar=4, entry=Decimal("100"))
    result = simulate_bar(order, bar_index=4, open_=101, high=102, low=99, close=100)
    assert result.event == "pending"


def test_later_untouched_order_expires_not_loses():
    order = PendingOrder.example(decision_bar=4, expires_after_bar=6)
    result = simulate_bar(order, bar_index=7, open_=105, high=106, low=104, close=105)
    assert result.event == "expired"


def test_same_bar_stop_and_target_is_stop_first():
    order = PendingOrder.example(filled=True, stop=Decimal("90"), target=Decimal("120"))
    result = simulate_bar(order, bar_index=8, open_=100, high=121, low=89, close=110)
    assert result.event == "stop"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_execution.py tests/replay/test_costs.py -q`
Expected: FAIL because execution/cost adapters do not exist.

- [ ] **Step 3: Implement deterministic order semantics**

```python
from dataclasses import dataclass, replace
from decimal import Decimal


@dataclass(frozen=True)
class PendingOrder:
    intent_id: str
    decision_bar: int
    expires_after_bar: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    filled: bool = False

    @classmethod
    def example(cls, **changes: object) -> "PendingOrder":
        return replace(cls("i-1", 4, 8, Decimal("100"), Decimal("90"), Decimal("120")), **changes)


@dataclass(frozen=True)
class ExecutionEvent:
    event: str
    price: Decimal | None = None


def simulate_bar(order: PendingOrder, *, bar_index: int, open_, high, low, close) -> ExecutionEvent:
    if bar_index <= order.decision_bar:
        return ExecutionEvent("pending")
    if not order.filled and bar_index > order.expires_after_bar:
        return ExecutionEvent("expired")
    if order.filled:
        if Decimal(str(low)) <= order.stop:
            return ExecutionEvent("stop", min(Decimal(str(open_)), order.stop))
        if Decimal(str(high)) >= order.target:
            return ExecutionEvent("target", max(Decimal(str(open_)), order.target))
        return ExecutionEvent("open")
    if Decimal(str(low)) <= order.entry <= Decimal(str(high)):
        return ExecutionEvent("fill", max(Decimal(str(open_)), order.entry))
    return ExecutionEvent("pending")
```

`CostModel` carries separately versioned LP fee, gas, spread, slippage, impact, and competition cost. It emits real-wallet and competition-adjusted views without double charging. Base and stress scenarios use timestamped TWAK quote calibration; absent historical pool depth is disclosed, never fabricated. Partial fills remain disabled.

- [ ] **Step 4: Run GREEN and monotonicity test**

Run: `uv run pytest tests/replay/test_execution.py tests/replay/test_costs.py -q`
Expected: PASS, including `stress.net_pnl <= base.net_pnl` for identical fills.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/costs.py src/magic_agent/replay/execution.py tests/replay/test_execution.py tests/replay/test_costs.py
git commit -m "feat(replay): add causal fills and versioned costs"
```

### Task 5: Implement The Single-Wallet Portfolio Ledger

**Files:**
- Create: `src/magic_agent/replay/wallet.py`
- Create: `tests/replay/test_wallet.py`

- [ ] **Step 1: Write cash-contention and drawdown tests**

```python
from decimal import Decimal

from magic_agent.replay.wallet import ReplayWallet


def test_simultaneous_intents_compete_for_one_cash_balance():
    wallet = ReplayWallet(Decimal("1000"), Decimal("0.30"))
    assert wallet.reserve("a", Decimal("600")) is True
    assert wallet.reserve("b", Decimal("200")) is False


def test_drawdown_uses_peak_total_equity():
    wallet = ReplayWallet(Decimal("1000"), Decimal("0.30"))
    wallet.mark(Decimal("1100"))
    wallet.mark(Decimal("990"))
    assert wallet.max_drawdown == Decimal("0.10")
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_wallet.py -q`
Expected: FAIL because `ReplayWallet` does not exist.

- [ ] **Step 3: Implement one ledger**

```python
from decimal import Decimal


class ReplayWallet:
    def __init__(self, initial_cash: Decimal, stable_reserve_fraction: Decimal) -> None:
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.reserve_floor = initial_cash * stable_reserve_fraction
        self.reserved: dict[str, Decimal] = {}
        self.peak_equity = initial_cash
        self.current_equity = initial_cash
        self.max_drawdown = Decimal("0")
        self.gas_reserve_bnb = Decimal("0")
        self.token_balances: dict[str, Decimal] = {}
        self.positions: dict[str, object] = {}
        self.realized_pnl = Decimal("0")
        self.unrealized_pnl = Decimal("0")
        self.open_stressed_loss_usd = Decimal("0")
        self.costs = Decimal("0")
        self.x402_spend = Decimal("0")
        self.hourly_marks: list[tuple[str, Decimal]] = []

    def reserve(self, intent_id: str, notional: Decimal) -> bool:
        if self.cash - sum(self.reserved.values()) - notional < self.reserve_floor:
            return False
        self.reserved[intent_id] = notional
        return True

    def mark(self, equity: Decimal) -> None:
        self.current_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            self.max_drawdown = max(self.max_drawdown, (self.peak_equity - equity) / self.peak_equity)

    def mark_hour(self, observed_at: str, token_prices: dict[str, Decimal]) -> None:
        missing = set(self.token_balances) - set(token_prices)
        if missing:
            raise ValueError(f"EQUITY_STALE:{','.join(sorted(missing))}")
        token_value = sum(self.token_balances[key] * token_prices[key] for key in self.token_balances)
        self.mark(self.cash + token_value)
        self.hourly_marks.append((observed_at, self.current_equity))

    def risk_state(self) -> PortfolioRiskState:
        return PortfolioRiskState(
            equity_usd=self.current_equity,
            cash_usd=self.cash,
            peak_equity_usd=self.peak_equity,
            daily_anchor_usd=self.initial_cash,
            open_stressed_loss_usd=self.open_stressed_loss_usd,
            reconciled_position=bool(self.positions),
        )
```

Fills move reserved cash into token balances and costs; reconciled exits reverse that mutation and
realize PnL. Missing fresh marks raise `EQUITY_STALE`, which the engine converts to an event and an
entry halt; holdings are never valued at zero.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_wallet.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/wallet.py tests/replay/test_wallet.py
git commit -m "feat(replay): add single-wallet portfolio ledger"
```

### Task 6: Orchestrate Replay Through The Shared DecisionPipeline

**Files:**
- Create: `src/magic_agent/replay/engine.py`
- Create: `tests/replay/test_engine.py`
- Create: `tests/replay/test_parity.py`

- [ ] **Step 1: Write import-boundary and future-invariance tests**

```python
import inspect

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.replay.engine import ReplayEngine


def test_replay_uses_the_live_pipeline_object():
    engine = ReplayEngine(
        config=None, identities=None, frames=None, scanner=None, selector=None,
        pipeline=DecisionPipeline(), risk_policy=None, execution=None,
        wallet=None, events=None,
    )
    assert isinstance(engine.pipeline, DecisionPipeline)
    source = inspect.getsource(ReplayEngine)
    assert "derive_entry" not in source
    assert "resolve_structural_stop" not in source
```

Future-bar invariance is already exercised against the production `as_of_frames` and momentum
functions in Tasks 2-3. Task 10's golden rerun exercises the complete engine twice.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_engine.py tests/replay/test_parity.py -q`
Expected: FAIL because the replay engine does not exist.

- [ ] **Step 3: Implement event ordering**

```python
class ReplayEngine:
    def __init__(self, *, config, identities, frames, scanner, selector, pipeline, risk_policy, execution, wallet, events) -> None:
        self.config = config
        self.identities = identities
        self.frames = frames
        self.scanner = scanner
        self.selector = selector
        self.pipeline = pipeline
        self.risk_policy = risk_policy
        self.execution = execution
        self.wallet = wallet
        self.events = events

    def step(self, decision_time) -> None:
        self.execution.process_open_orders(decision_time, self.wallet, self.events)
        self.execution.process_position_exits(decision_time, self.wallet, self.events)
        self.wallet.mark(self.frames.marks_as_of(decision_time))
        candidates = self.selector.select(decision_time)
        for candidate in candidates:
            sliced = self.frames.as_of(candidate.identity_key, decision_time)
            scan = self.scanner.scan(candidate, sliced)
            setup = self.scanner.authorized_setup(scan)
            if setup is None:
                continue
            caps = self.execution.quantity_caps(candidate.identity_key, decision_time)
            risk = self.risk_policy.evaluate(
                setup, self.wallet.risk_state(), ActionPurpose.STRATEGY,
                caps, candidate.macro_clamp,
            )
            decision = self.pipeline.decide(self.wallet.decision_inputs(setup, risk))
            self.events.append_decision(candidate, decision_time, decision)
            if decision.intent is not None:
                self.execution.place(decision.intent, decision_time, self.wallet, self.events)
```

Add `from magic_agent.spot_models import ActionPurpose` to `engine.py`. The replay execution adapter
derives `QuantityCaps` from the selected versioned quote-calibration tier; absent calibration denies
the intent and records `CAPACITY_UNAVAILABLE`.

Priority among simultaneous intents is deterministic: scanner grade tier descending, selector rank ascending, identity key ascending. Stops/targets are processed before new entries. The engine evaluates after each closed H1 bar and supplies only `close_time <= decision_time` frames.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_engine.py tests/replay/test_parity.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/engine.py tests/replay/test_engine.py tests/replay/test_parity.py
git commit -m "feat(replay): orchestrate shared live decision pipeline"
```

### Task 7: Add Append-Only Events And Accounting Reconciliation

**Files:**
- Create: `src/magic_agent/replay/events.py`
- Create: `tests/replay/test_events.py`

- [ ] **Step 1: Write deterministic event tests**

```python
from magic_agent.replay.events import EventJournal


def test_identical_input_reproduces_event_ids(tmp_path):
    event_payloads = [
        {"event_type": "decision", "event_time_utc": "2026-01-01T01:00:00Z", "payload": {"action": "hold"}},
        {"event_type": "mark", "event_time_utc": "2026-01-01T02:00:00Z", "payload": {"equity": "1000"}},
    ]
    first = EventJournal(tmp_path / "a.jsonl", "run-1")
    second = EventJournal(tmp_path / "b.jsonl", "run-1")
    for payload in event_payloads:
        first.append(**payload)
        second.append(**payload)
    assert first.read_all() == second.read_all()
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_events.py -q`
Expected: FAIL because `EventJournal` does not exist.

- [ ] **Step 3: Implement content-derived IDs**

```python
import hashlib
import json
from pathlib import Path


class EventJournal:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, **payload) -> dict:
        ordinal = len(self.read_all())
        canonical = json.dumps({"run_id": self.run_id, "ordinal": ordinal, **payload}, sort_keys=True, separators=(",", ":"))
        event = {"event_id": hashlib.sha256(canonical.encode()).hexdigest(), **json.loads(canonical)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
```

Add reconciliation that proves: initial cash + realized/unrealized PnL - all view-specific costs = final equity; trade rows aggregate from fills/exits; every position balance matches wallet token balances. Any unexplained difference sets replay validity to `invalid`.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_events.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/events.py tests/replay/test_events.py
git commit -m "feat(replay): add deterministic event journal and reconciliation"
```

### Task 8: Compute Metrics, Uncertainty, And Bias-First Reports

**Files:**
- Create: `src/magic_agent/replay/metrics.py`
- Create: `src/magic_agent/replay/report.py`
- Create: `tests/replay/test_metrics.py`
- Create: `tests/replay/test_report.py`

- [ ] **Step 1: Write Wilson and insufficient-sample tests**

```python
from magic_agent.replay.metrics import wilson_interval
from magic_agent.replay.report import build_report


def test_wilson_interval_is_bounded():
    low, high = wilson_interval(3, 5)
    assert 0 <= low < 0.6 < high <= 1


def test_report_leads_with_bias_and_labels_small_sample():
    artifacts = {
        "completed_trades": [{"pnl": "1"}] * 5,
        "bias_flags": ["zec_winner_selected"],
        "parity_status": "live_parity",
        "cost_scenario": "base",
    }
    report = build_report(artifacts)
    assert report["bias_flags"][0] == "zec_winner_selected"
    assert report["trade_metrics"]["edge_status"] == "insufficient_lt_30"
    assert "win_rate_wilson_95" in report["trade_metrics"]
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_metrics.py tests/replay/test_report.py -q`
Expected: FAIL because metrics/report modules do not exist.

- [ ] **Step 3: Implement Wilson interval**

```python
from math import sqrt


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 1.0)
    p = wins / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))
```

`build_report` must lead with parity status, sample size, survivorship/winner-selection/macro-exclusion flags, and cost scenario. Then report signal flow; win rate with Wilson interval; expectancy R/USD after costs; average/median R; profit factor; payoff; MAE/MFE; holding time; losing streak; fill rate; hourly drawdown; time underwater; exposure; turnover; cost drag; reserve; concentration; qualification coverage; per-symbol and equal-weight buy-and-hold; structural baseline versus momentum lane. Block bootstrap is enabled only when sample length supports the configured block size.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_metrics.py tests/replay/test_report.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/metrics.py src/magic_agent/replay/report.py tests/replay/test_metrics.py tests/replay/test_report.py
git commit -m "feat(replay): add uncertainty-aware bias-first reports"
```

### Task 9: Persist Forward CMC Shadow Snapshots

**Files:**
- Create: `src/magic_agent/replay/forward_shadow.py`
- Create: `tests/replay/test_forward_shadow.py`

- [ ] **Step 1: Write immutable snapshot tests**

```python
from magic_agent.replay.forward_shadow import ForwardShadowStore


def test_snapshot_persists_raw_normalized_ttl_and_payment(tmp_path):
    store = ForwardShadowStore(tmp_path)
    saved = store.capture(
        observed_at="2026-06-21T00:00:00Z", expires_at="2026-06-21T00:15:00Z",
        provider="cmc", endpoint="quotes/latest", schema_version="1",
        raw={"data": {"1": {"price": 1}}}, normalized={"rank": 1},
        selector_config_hash="cfg", output={"vetoed": False}, x402_payment_id="pay-1",
    )
    assert saved.exists()
    assert store.read(saved)["x402_payment_id"] == "pay-1"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_forward_shadow.py -q`
Expected: FAIL because `forward_shadow` does not exist.

- [ ] **Step 3: Implement content-addressed capture**

```python
import hashlib
import json
from pathlib import Path


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ForwardShadowStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def capture(self, *, observed_at: str, expires_at: str, provider: str, endpoint: str,
                schema_version: str, raw: dict, normalized: dict,
                selector_config_hash: str, output: dict,
                x402_payment_id: str | None) -> Path:
        request_hash = hashlib.sha256(_canonical({"provider": provider, "endpoint": endpoint}).encode()).hexdigest()
        raw_hash = hashlib.sha256(_canonical(raw).encode()).hexdigest()
        payload = {
            "observed_at_utc": observed_at, "expires_at_utc": expires_at,
            "provider": provider, "skill_or_endpoint": endpoint,
            "schema_version": schema_version, "request_hash": request_hash,
            "raw_response_hash": raw_hash, "normalized_payload": normalized,
            "selector_config_hash": selector_config_hash,
            "rank_veto_clamp_output": output, "x402_payment_id": x402_payment_id,
        }
        snapshot_id = hashlib.sha256(_canonical(payload).encode()).hexdigest()
        payload["snapshot_id"] = snapshot_id
        path = self.root / f"{snapshot_id}.json"
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
        return path

    @staticmethod
    def read(path: str | Path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))
```

Forward-shadow outputs never call `DecisionPipeline` with funds and never become historical
features before their observation time.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/replay/test_forward_shadow.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/forward_shadow.py tests/replay/test_forward_shadow.py
git commit -m "feat(replay): persist forward CMC shadow snapshots"
```

### Task 10: Add Frozen ZEC Diagnostic And Full-Universe CLI

**Files:**
- Create: `src/magic_agent/replay/cli.py`
- Modify: `pyproject.toml`
- Create: `tests/replay/test_cli.py`
- Create: `tests/replay/test_golden.py`

- [ ] **Step 1: Write frozen-manifest and golden tests**

```python
from magic_agent.replay.cli import diagnostic_config, required_coverage


def test_diagnostic_manifest_is_frozen_before_run():
    config = diagnostic_config(scanner_commit="scanner", midas_commit="midas", identity_hash="ids", data_hash="data")
    assert config.start_utc == "2026-01-01T00:00:00Z"
    assert config.end_utc == "2026-06-20T23:59:59Z"
    assert config.symbols == ("ZEC/USDT", "ETH/USDT", "TRX/USDT", "APE/USDT")


def test_missing_control_is_reported_not_replaced():
    config = diagnostic_config(scanner_commit="scanner", midas_commit="midas", identity_hash="ids", data_hash="data")
    coverage = required_coverage(config, available={"ZEC/USDT", "ETH/USDT", "TRX/USDT"})
    assert coverage["APE/USDT"] == "UNSCANNABLE"
    assert "APE/USDT" in config.symbols
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/replay/test_cli.py tests/replay/test_golden.py -q`
Expected: FAIL because no replay CLI exists.

- [ ] **Step 3: Add commands**

```toml
[project.scripts]
magic-agent = "magic_agent.cli:main"
magic-replay = "magic_agent.replay.cli:main"
```

Implement the CLI surface:

```python
import argparse

from magic_agent.replay.contracts import ReplayConfig


def diagnostic_config(*, scanner_commit: str, midas_commit: str,
                      identity_hash: str, data_hash: str) -> ReplayConfig:
    return ReplayConfig.diagnostic(
        scanner_commit=scanner_commit, midas_commit=midas_commit,
        identity_snapshot_hash=identity_hash, data_snapshot_hash=data_hash,
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="magic-replay")
    commands = root.add_subparsers(dest="command", required=True)
    diagnostic = commands.add_parser("diagnostic")
    diagnostic.add_argument("--data-dir", required=True)
    diagnostic.add_argument("--identity", required=True)
    diagnostic.add_argument("--output", required=True)
    universe = commands.add_parser("universe")
    universe.add_argument("--manifest", required=True)
    universe.add_argument("--output", required=True)
    return root


def required_coverage(config: ReplayConfig, *, available: set[str]) -> dict[str, str]:
    return {symbol: ("SCANNABLE" if symbol in available else "UNSCANNABLE")
            for symbol in config.symbols}
```

Diagnostic has no symbol/date override flags. Universe runs every gold scannable identity and
records missing data. Both write manifest, audit, events, fills, trades, hourly equity, report
JSON, and Markdown summary under a new run-ID directory; they never overwrite live state.

- [ ] **Step 4: Run the replay suite and deterministic rerun**

Run: `uv run pytest tests/replay -q`
Expected: PASS.

Run the synthetic golden diagnostic twice and compare event/report hashes.
Expected: identical hashes and reconciled final equity.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/replay/cli.py pyproject.toml tests/replay/test_cli.py tests/replay/test_golden.py
git commit -m "feat(replay): add frozen diagnostic and universe CLI"
```

### Task 11: Final Parity And Research-Integrity Gate

**Files:**
- Modify: `README.md`
- Create: `docs/causal-replay-runbook.md`

- [ ] **Step 1: Run all deterministic tests**

Run: `uv run pytest -q`
Expected: PASS for runtime and replay.

- [ ] **Step 2: Run source-boundary checks**

Run: `rg -n "derive_entry|resolve_structural_stop|fixed_2r|subprocess|TwakRunner" src/magic_agent/replay`
Expected: no matches outside explanatory strings/tests. Replay must import scanner gateway, `DecisionPipeline`, and RiskPolicy rather than duplicate them.

- [ ] **Step 3: Verify required golden cases**

```python
import pytest

from magic_agent.replay.execution import PendingOrder, simulate_bar


@pytest.mark.parametrize(
    ("case", "order_changes", "bar", "expected_event"),
    [
        ("direct_qml_winner", {"filled": True}, {"bar_index": 8, "open_": 110, "high": 121, "low": 105, "close": 120}, "target"),
        ("stop_loss", {"filled": True}, {"bar_index": 8, "open_": 95, "high": 101, "low": 89, "close": 90}, "stop"),
        ("unfilled", {}, {"bar_index": 9, "open_": 105, "high": 106, "low": 104, "close": 105}, "expired"),
        ("boundary_open", {"filled": True}, {"bar_index": 8, "open_": 105, "high": 110, "low": 95, "close": 106}, "open"),
        ("checklist_dol_false_campaign_known", {"filled": True}, {"bar_index": 8, "open_": 110, "high": 121, "low": 105, "close": 120}, "target"),
        ("same_bar_stop_and_target", {"filled": True}, {"bar_index": 8, "open_": 100, "high": 121, "low": 89, "close": 110}, "stop"),
    ],
)
def test_required_price_path_golden_cases(case, order_changes, bar, expected_event):
    order = PendingOrder.example(**order_changes)
    assert simulate_bar(order, **bar).event == expected_event


def test_unresolved_campaign_dol_creates_no_order():
    from magic_agent.scanner_gateway import authorized_setup_from_scan
    from types import SimpleNamespace

    scan = SimpleNamespace(
        authorization=SimpleNamespace(state="monitor_only", authorized_direction=None),
        levels=SimpleNamespace(stop=object(), campaign_dol=None), entry=object(),
    )
    assert authorized_setup_from_scan(scan, identity_key="zec-bsc", scanner_commit="scanner") is None
```

Run: `uv run pytest tests/replay/test_golden.py -q`
Expected: PASS.

- [ ] **Step 4: Run Opus review**

Apply `general-review-protocol`. Release blockers are future leakage, decision-bar fills, fixed-R fallback, missing unresolved positions, independent per-trade funding, double-counted costs, present CMC joined backward, or a `live_parity` label when runtime contracts differ.

- [ ] **Step 5: Commit documentation fixes**

```bash
git add README.md docs/causal-replay-runbook.md
git commit -m "docs: add causal replay methodology and runbook"
```

## Self-Review

- **Coverage:** parity REQ-001-006 -> Tasks 1/6/11; causal data REQ-010-017 -> Task 2; selection REQ-020-025 -> Tasks 3/9; order/lifecycle REQ-030-047 -> Tasks 4/6; costs REQ-050-056 -> Task 4; single wallet REQ-060-066 -> Tasks 5/6; diagnostic REQ-070-078 -> Task 10; metrics REQ-080-086 -> Task 8; audit REQ-090-094 -> Tasks 1/7/10/11.
- **No campaign DOL:** scanner state may remain monitorable, but Tasks 6/10 create no order and never fabricate fixed `2R`.
- **Biases:** ZEC is winner-selected exploratory evidence; current-universe membership is survivorship-biased when historical membership is unavailable; nonreconstructable CMC is forward-shadow only.
- **Parity label:** `live_parity` is allowed only when scanner/runtime commits and configuration hashes match and every live deterministic behavior is represented by the shared pipeline. Otherwise report `research_execution_model` or `invalid`.
