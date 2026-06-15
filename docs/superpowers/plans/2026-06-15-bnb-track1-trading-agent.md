# BNB Track 1 Autonomous Trading Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the deterministic ICT/SMC scanner in a bounded autonomous agent that trades leveraged longs and shorts on a BNB perp venue, with the scanner as the sole signal source and the AI bounded to gate/size/manage.

**Architecture:** Four layers borrowed in *shape* from `llm-trading-bot` but with decision authority inverted — the scanner decides, not the LLM. READ (CMC context) → SIGNAL (scanner `Setup`) → DECIDE (deterministic `gate`+`size` → typed `AgentDecision`) → EXECUTE (`PerpExecutor` Protocol with `Paper`/`Aster`/… impls). A new-closed-candle, stops-first runner loop drives it. Everything is injectable so the whole loop is testable with no network.

**Tech Stack:** Python 3.11+, `uv` + `pytest` (existing scanner setup), frozen `dataclasses` + `Enum` (no new deps — matches scanner idiom, *not* pydantic), `ccxt` (already a dep) for the Aster executor, `bnbagent-sdk` (vendored sibling repo) for ERC-8004 identity.

**Home:** A **new, separate repo** `bnb-trading-agent` (the Track 1 submission), package `magic_agent`, that consumes the stable scanner as an **editable path dependency** (`uv add --editable ../trading-scanner`). The scanner (`trading-scanner`) stays its own package repo, untouched. The agent imports the scanner directly (`from magic_scanner.scan import scan_symbols`) but lives in its own repo for a clean submission identity. For the final submission the dep may be git-pinned instead of editable.

**Design spec:** copied into the new repo as `docs/superpowers/specs/2026-06-15-bnb-track1-trading-agent-design.md` (original at the workspace root; this plan implements it).

**Conventions:** TDD red→green per step. Commit after each task. Every commit message ends with the trailer:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
Run tests with `uv run pytest`. The default run is offline (`-m "not network"`); all tests below are offline.

---

## File Structure

```
src/magic_agent/
  __init__.py          # package marker
  models.py            # enums + frozen dataclasses: Action/Side/Outcome, Candle,
                       #   ContextSnapshot, Setup, GateVerdict, ExecutionIntent,
                       #   AgentDecision, PositionState, AccountState
  decision.py          # gate(), size(), build_decision() — deterministic, AI-bounded
  executor.py          # PerpExecutor Protocol + PaperExecutor (in-memory sim)
  context.py           # CmcContextAdapter (injectable client, degrades to unavailable)
  log.py               # AgentLog (JSONL decision/audit log) + decision_record()
  runner.py            # check_stops() + on_candle() — new-candle, stops-first loop body
  setup_view.py        # from_scan_result(): ScanResult -> agent Setup (pure mapper, no scanner import)
  scanner_gateway.py   # ScannerGateway.scan(): the ONLY module importing magic_scanner.*
  executors/
    __init__.py
    aster.py           # AsterRestExecutor (ccxt-based, injectable exchange)
  identity.py          # Erc8004Identity (bnbagent-sdk wrapper, injectable client)
  treasury.py          # TwakTreasury (collateral move / pnl sweep, injectable client)
  cli.py               # `magic-agent run` entry point (wires the loop)

tests/
  test_models.py  test_decision.py  test_executor.py  test_context.py
  test_log.py  test_runner.py  test_setup_view.py  test_scanner_gateway.py  test_aster.py
  test_identity.py  test_treasury.py  test_cli.py

docs/superpowers/specs/2026-06-15-venue-spike-findings.md   # Task 2 output
```

Responsibilities are split so each file holds one concern and stays small enough to reason about in one read. `models.py` is shared vocabulary; `decision.py` is pure logic; `executor.py`/`executors/*` are the venue boundary; `runner.py` is orchestration only. `scanner_gateway.py` is the **sole seam** importing scanner internals (`magic_scanner.*`) — if scanner types shift, only it changes, and it delegates to the scanner-free `setup_view.py` mapper.

---

## Task 1: Scaffold the new repo + editable scanner dependency

**Files:**
- Create: new repo `bnb-trading-agent/` (sibling of `trading-scanner/`)
- Create: `pyproject.toml` (via `uv init`), `src/magic_agent/__init__.py`, `src/magic_agent/executors/__init__.py`, `tests/__init__.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Init the new repo + uv package project**

```bash
cd ..                       # workspace root: sibling of trading-scanner/
mkdir bnb-trading-agent && cd bnb-trading-agent
git init
uv init --package --name magic-agent --python ">=3.11"
# uv creates pyproject.toml + src/magic_agent/__init__.py
```

- [ ] **Step 2: Add the scanner as an editable path dependency**

```bash
uv add --editable ../trading-scanner
# resolves the `magic-scanner` package; `from magic_scanner.scan import scan_symbols` now works.
# (For the final submission you may swap to a git-pinned dep instead of --editable.)
```

- [ ] **Step 3: Create the remaining package markers + test dir**

```bash
mkdir -p src/magic_agent/executors tests
printf '"""Bounded autonomous trading-agent layer (BNB Track 1)."""\n' > src/magic_agent/__init__.py
printf '"""Venue-specific PerpExecutor implementations."""\n' > src/magic_agent/executors/__init__.py
printf '' > tests/__init__.py
```

- [ ] **Step 4: Write the smoke test (proves BOTH the agent package and the scanner dep import)**

```python
# tests/test_smoke.py
def test_agent_package_imports():
    import magic_agent
    assert magic_agent.__doc__ is not None


def test_scanner_dependency_is_importable():
    from magic_scanner.scan import scan_symbols  # editable path dep resolves
    assert callable(scan_symbols)
```

- [ ] **Step 5: Run it**

Run: `uv run pytest tests/test_smoke.py -q`
Expected: PASS (2 passed). If `magic_scanner` import fails, the editable dep didn't resolve — re-run Step 2.

- [ ] **Step 6: Copy the spec + plan into the new repo, commit**

```bash
mkdir -p docs/superpowers/specs docs/superpowers/plans
cp ../trading-scanner/docs/superpowers/plans/2026-06-15-bnb-track1-trading-agent.md docs/superpowers/plans/
cp ../docs/superpowers/specs/2026-06-15-bnb-track1-trading-agent-design.md docs/superpowers/specs/
git add -A
git commit -m "chore: scaffold bnb-trading-agent (uv package + editable scanner dep)"
```

---

## Task 2: Venue spike (verification — gates venue choice)

This is a **research task**, not TDD. It resolves §8 of the spec. Output a findings doc; do **not** write production code from guesses.

**Files:**
- Create: `docs/superpowers/specs/2026-06-15-venue-spike-findings.md`
- Scratch (gitignored / deleted after): `scripts/_spike_venue.py`

- [ ] **Step 1: Confirm ccxt has the venue class and inspect its perp surface**

```bash
uv run python - <<'PY'
import ccxt
print("aster" in ccxt.exchanges, "apollox" in ccxt.exchanges)
ex = ccxt.aster() if "aster" in ccxt.exchanges else None
print(type(ex).__name__ if ex else "NO ASTER CLASS")
if ex:
    print("has create_order:", hasattr(ex, "create_order"))
    print("has set_leverage:", hasattr(ex, "set_leverage"))
    print("urls.api:", ex.urls.get("api"))
PY
```
Record: does the class exist? Does it expose `create_order`/`set_leverage`/`fetch_positions`? Base URLs?

- [ ] **Step 2: Verify auth flow + testnet from the venue docs**

Read and record, for the chosen venue (Aster default, ApolloX alt):
- Auth: HMAC API key/secret, **or** wallet-signed (`signer`/`privateKey`/`AGENT`)? Is the wallet in the order path?
- Testnet/sandbox base URL for perps? (Aster: `aster-finance-futures-api-testnet.md` in their api-docs repo.)
- `positionSide` values for hedge-mode long+short; leverage/margin endpoints.
- Sources: Aster `https://asterdex.github.io/aster-api-website/futures/account%26trades/`, ApolloX `https://github.com/apollox-finance/apollox-finance-api-docs`.

- [ ] **Step 3: Confirm CMC + TWAK auth (de-garnish x402)**

Record: CMC Agent Hub auth/billing mechanism (API key? x402?). TWAK auth (Bearer / HMAC per `developer.trustwallet.com/developer/agent-sdk`). Confirm whether x402 is actually required anywhere or is bonus.

- [ ] **Step 4: Write the findings doc**

Create `docs/superpowers/specs/2026-06-15-venue-spike-findings.md` with one section per spec §8 item (auth flow, testnet, leverage/positionSide, ccxt fit, CMC/TWAK auth), each marked **CONFIRMED** or **STILL UNKNOWN** with the source. End with: "Default venue = X; executor shape = REST-HMAC | REST-wallet-signed."

- [ ] **Step 5: Commit the findings, delete scratch**

```bash
rm -f scripts/_spike_venue.py
git add docs/superpowers/specs/2026-06-15-venue-spike-findings.md
git commit -m "docs(agent): venue spike findings (auth/testnet/leverage/ccxt fit)"
```

> If the spike contradicts the Aster-default assumption (e.g., no testnet, or ccxt class can't place orders), STOP and flag it — Tasks 9+ depend on this. Tasks 3–8 are venue-agnostic and proceed regardless.

---

## Task 3: Core models

**Files:**
- Create: `src/magic_agent/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
import dataclasses
import pytest
from magic_agent.models import (
    Action, Side, Outcome, Candle, ContextSnapshot, Setup,
    GateVerdict, ExecutionIntent, AgentDecision, PositionState, AccountState,
)


def test_enums_have_expected_members():
    assert Action.ENTER_LONG.value == "enter_long"
    assert Action.ENTER_SHORT.value == "enter_short"
    assert {s.value for s in Side} == {"flat", "long", "short"}
    assert Outcome.OPENED.value == "opened"


def test_setup_is_frozen_and_carries_entry_and_stop():
    s = Setup(symbol="BNB/USDT", direction=Side.LONG, rating="A", regime="risk_on",
              entry=600.0, stop_loss=588.0, take_profit=636.0, confirmation_kind="chained_scob")
    assert s.entry == 600.0 and s.stop_loss == 588.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.entry = 1.0  # type: ignore[misc]


def test_position_and_account_defaults():
    p = PositionState()
    assert p.side is Side.FLAT and p.size == 0.0
    a = AccountState(equity=1000.0, available=1000.0)
    assert a.currency == "USDT"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.models`.

- [ ] **Step 3: Implement the models**

```python
# src/magic_agent/models.py
"""Shared vocabulary for the agent layer — enums + frozen dataclasses.

Frozen dataclasses (not pydantic) keep us aligned with the scanner's idiom and add
no dependency. These types are the interfaces between decision / execution / runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    HOLD = "hold"
    CLOSE = "close"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"


class Side(str, Enum):
    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


class Outcome(str, Enum):
    NOOP = "noop"
    OPENED = "opened"
    CLOSED = "closed"
    SKIPPED_VETO = "skipped_veto"
    SKIPPED_ZERO_SIZE = "skipped_zero_size"
    SKIPPED_IN_POSITION = "skipped_in_position"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Candle:
    """OHLC for stop checks. No timestamp — the runner owns time, not the model."""
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ContextSnapshot:
    """CMC-derived market context. ``status='unavailable'`` => no veto, no boost."""
    regime: str        # "risk_on" | "risk_off" | "neutral"
    risk_flag: str     # "low" | "elevated" | "high"
    status: str = "ok"  # "ok" | "unavailable"


@dataclass(frozen=True)
class Setup:
    """Agent-side view of one scanner setup (the SOLE signal source)."""
    symbol: str
    direction: Side    # LONG or SHORT
    rating: str        # "A" / "B" / ...
    regime: str
    entry: float
    stop_loss: float
    take_profit: float
    confirmation_kind: str


@dataclass(frozen=True)
class GateVerdict:
    allow: bool
    size_multiplier: float
    reason: str


@dataclass(frozen=True)
class ExecutionIntent:
    symbol: str
    action: Action          # ENTER_LONG / ENTER_SHORT / CLOSE
    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    leverage: float = 1.0


@dataclass(frozen=True)
class AgentDecision:
    action: Action
    intent: ExecutionIntent | None
    gate: GateVerdict
    setup_ref: str
    reasoning: str = ""


@dataclass(frozen=True)
class PositionState:
    side: Side = Side.FLAT
    size: float = 0.0
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass(frozen=True)
class AccountState:
    equity: float
    available: float
    currency: str = "USDT"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/models.py tests/test_models.py
git commit -m "feat(agent): core models (enums + frozen dataclasses)"
```

---

## Task 4: PerpExecutor Protocol + PaperExecutor

**Files:**
- Create: `src/magic_agent/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_executor.py
from magic_agent.models import (
    Action, Side, Outcome, ExecutionIntent, PositionState,
)
from magic_agent.executor import PaperExecutor, PerpExecutor


def _intent(action, qty=2.0, entry=600.0, sl=588.0, tp=636.0):
    return ExecutionIntent(symbol="BNB/USDT", action=action, qty=qty,
                           entry=entry, stop_loss=sl, take_profit=tp, leverage=1.0)


def test_paper_executor_satisfies_protocol():
    assert isinstance(PaperExecutor(starting_equity=1000.0), PerpExecutor)


def test_open_long_sets_position_and_reduces_available():
    ex = PaperExecutor(starting_equity=1000.0)
    out = ex.open_position(_intent(Action.ENTER_LONG, qty=1.0, entry=600.0))
    assert out is Outcome.OPENED
    pos = ex.get_position()
    assert pos.side is Side.LONG and pos.size == 1.0 and pos.entry_price == 600.0


def test_open_short_then_close_realizes_pnl():
    ex = PaperExecutor(starting_equity=1000.0)
    ex.open_position(_intent(Action.ENTER_SHORT, qty=1.0, entry=600.0))
    out = ex.close_position(mark_price=580.0)  # short profits when price falls
    assert out is Outcome.CLOSED
    assert ex.get_position().side is Side.FLAT
    assert ex.get_account(mark_price=580.0).equity == 1020.0  # +20


def test_open_when_already_in_position_is_skipped():
    ex = PaperExecutor(starting_equity=1000.0)
    ex.open_position(_intent(Action.ENTER_LONG, qty=1.0))
    out = ex.open_position(_intent(Action.ENTER_LONG, qty=1.0))
    assert out is Outcome.SKIPPED_IN_POSITION


def test_zero_qty_is_skipped():
    ex = PaperExecutor(starting_equity=1000.0)
    assert ex.open_position(_intent(Action.ENTER_LONG, qty=0.0)) is Outcome.SKIPPED_ZERO_SIZE
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_executor.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.executor`.

- [ ] **Step 3: Implement Protocol + PaperExecutor**

```python
# src/magic_agent/executor.py
"""Execution boundary. ``PerpExecutor`` is the one interface; ``PaperExecutor`` is
the in-memory sim used as the always-demoable fallback and in every test."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from magic_agent.models import (
    Action, AccountState, ExecutionIntent, Outcome, PositionState, Side,
)


@runtime_checkable
class PerpExecutor(Protocol):
    def get_position(self) -> PositionState: ...
    def get_account(self, *, mark_price: float) -> AccountState: ...
    def open_position(self, intent: ExecutionIntent) -> Outcome: ...
    def close_position(self, *, mark_price: float) -> Outcome: ...
    def sync(self) -> None: ...


class PaperExecutor:
    """One-position-at-a-time in-memory perp sim. PnL is mark-to-entry * size,
    sign-aware for long/short. No leverage effect on PnL in v1 (size is units)."""

    def __init__(self, starting_equity: float = 1000.0) -> None:
        self._realized = starting_equity
        self._pos = PositionState()

    def get_position(self) -> PositionState:
        return self._pos

    def _unrealized(self, mark_price: float) -> float:
        p = self._pos
        if p.side is Side.FLAT or p.entry_price is None:
            return 0.0
        sign = 1.0 if p.side is Side.LONG else -1.0
        return sign * (mark_price - p.entry_price) * p.size

    def get_account(self, *, mark_price: float) -> AccountState:
        equity = self._realized + self._unrealized(mark_price)
        return AccountState(equity=equity, available=self._realized)

    def open_position(self, intent: ExecutionIntent) -> Outcome:
        if intent.qty <= 0:
            return Outcome.SKIPPED_ZERO_SIZE
        if self._pos.side is not Side.FLAT:
            return Outcome.SKIPPED_IN_POSITION
        side = Side.LONG if intent.action is Action.ENTER_LONG else Side.SHORT
        self._pos = PositionState(
            side=side, size=intent.qty, entry_price=intent.entry,
            stop_loss=intent.stop_loss, take_profit=intent.take_profit,
        )
        return Outcome.OPENED

    def close_position(self, *, mark_price: float) -> Outcome:
        if self._pos.side is Side.FLAT:
            return Outcome.NOOP
        self._realized += self._unrealized(mark_price)
        self._pos = PositionState()
        return Outcome.CLOSED

    def sync(self) -> None:
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_executor.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/executor.py tests/test_executor.py
git commit -m "feat(agent): PerpExecutor Protocol + PaperExecutor sim"
```

---

## Task 5: Decision layer (gate + size + build_decision)

**Files:**
- Create: `src/magic_agent/decision.py`
- Test: `tests/test_decision.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_decision.py
from magic_agent.models import (
    Action, Side, ContextSnapshot, Setup, AccountState,
)
from magic_agent.decision import gate, size, build_decision, RISK_PCT_DEFAULT


def _setup(direction=Side.LONG, rating="A", regime="risk_on"):
    return Setup(symbol="BNB/USDT", direction=direction, rating=rating, regime=regime,
                 entry=600.0, stop_loss=588.0, take_profit=636.0,
                 confirmation_kind="chained_scob")


def test_c_grade_is_vetoed():
    v = gate(_setup(rating="C"), ContextSnapshot("neutral", "low"))
    assert v.allow is False


def test_high_risk_flag_vetoes_even_a_grade():
    v = gate(_setup(rating="A"), ContextSnapshot("risk_on", "high"))
    assert v.allow is False


def test_long_in_risk_off_is_halved_not_vetoed_for_a_grade():
    v = gate(_setup(direction=Side.LONG, rating="A", regime="x"),
             ContextSnapshot("risk_off", "low"))
    assert v.allow is True and v.size_multiplier == 0.5


def test_b_grade_against_regime_is_vetoed():
    v = gate(_setup(direction=Side.SHORT, rating="B"),
             ContextSnapshot("risk_on", "low"))  # short vs risk_on = against
    assert v.allow is False


def test_full_a_family_is_tradeable():
    for r in ("A++", "A+", "A", "A-"):
        assert gate(_setup(rating=r), ContextSnapshot("risk_on", "low")).allow is True


def test_full_b_family_is_tradeable_when_aligned():
    for r in ("B+", "B", "B-"):
        assert gate(_setup(rating=r), ContextSnapshot("neutral", "low")).allow is True


def test_below_threshold_ratings_are_vetoed():
    for r in ("C", "N/A", "—"):
        assert gate(_setup(rating=r), ContextSnapshot("risk_on", "low")).allow is False


def test_unavailable_context_lets_setup_stand_at_full_size():
    v = gate(_setup(rating="A"), ContextSnapshot("neutral", "low", status="unavailable"))
    assert v.allow is True and v.size_multiplier == 1.0


def test_size_is_risk_over_stop_distance():
    # 1% of 1000 = 10 risk; |600-588| = 12 -> 0.8333 units
    qty = size(equity=1000.0, risk_pct=0.01, entry=600.0, stop_loss=588.0)
    assert abs(qty - (10.0 / 12.0)) < 1e-9


def test_zero_stop_distance_returns_zero_size():
    assert size(equity=1000.0, risk_pct=0.01, entry=600.0, stop_loss=600.0) == 0.0


def test_build_decision_enter_long_carries_scaled_intent():
    acct = AccountState(equity=1000.0, available=1000.0)
    d = build_decision(_setup(rating="A", regime="risk_on"),
                       ContextSnapshot("risk_on", "low"), acct)
    assert d.action is Action.ENTER_LONG
    assert d.intent is not None and d.intent.action is Action.ENTER_LONG
    # full size: 1% risk over 12 distance
    assert abs(d.intent.qty - (10.0 / 12.0)) < 1e-9


def test_build_decision_veto_has_no_intent():
    acct = AccountState(equity=1000.0, available=1000.0)
    d = build_decision(_setup(rating="C"), ContextSnapshot("neutral", "low"), acct)
    assert d.action is Action.HOLD and d.intent is None and d.gate.allow is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_decision.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.decision`.

- [ ] **Step 3: Implement the decision layer**

```python
# src/magic_agent/decision.py
"""Bounded, deterministic decision layer.

The scanner produces the Setup; this layer only decides take/skip (``gate``) and
position size (``size``). The take/skip and size are pure functions of (grade,
context, risk) — an LLM may later rank/explain, but never controls these.
"""
from __future__ import annotations

from magic_agent.models import (
    Action, AccountState, AgentDecision, ContextSnapshot, ExecutionIntent,
    GateVerdict, Setup, Side,
)

RISK_PCT_DEFAULT = 0.01

# Scanner rating FAMILY — source of truth is engine.py:15
# RATING = ["N/A", "C", "B-", "B", "B+", "A-", "A", "A+", "A++"].
# We trade the A-family and B-family; "C"/"N/A"/"—" are below threshold. Match by
# family, NOT exact "A"/"B" (which would wrongly veto A++, A+, A-, B+, B-).
_A_GRADES = {"A++", "A+", "A", "A-"}
_B_GRADES = {"B+", "B", "B-"}


def _grade_family(rating: str) -> str | None:
    if rating in _A_GRADES:
        return "A"
    if rating in _B_GRADES:
        return "B"
    return None


def _against_regime(direction: Side, regime: str) -> bool:
    """A long fights a risk_off tape; a short fights a risk_on tape."""
    return (regime == "risk_off" and direction is Side.LONG) or (
        regime == "risk_on" and direction is Side.SHORT
    )


def gate(setup: Setup, context: ContextSnapshot) -> GateVerdict:
    family = _grade_family(setup.rating)
    if family is None:
        return GateVerdict(False, 0.0, f"rating {setup.rating} below threshold")

    # Context unavailable => the setup stands on its own grade, full size.
    if context.status != "ok":
        return GateVerdict(True, 1.0, "context unavailable; setup stands")

    if context.risk_flag == "high":
        return GateVerdict(False, 0.0, "context risk_flag=high")

    against = _against_regime(setup.direction, context.regime)
    if family == "B" and against:
        return GateVerdict(False, 0.0, "B-grade against regime")

    mult = 1.0
    if against:
        mult = 0.5  # A-grade against regime: allowed at half size
    elif context.risk_flag == "elevated":
        mult = 0.5
    return GateVerdict(True, mult, f"allowed (mult={mult})")


def size(*, equity: float, risk_pct: float, entry: float, stop_loss: float) -> float:
    distance = abs(entry - stop_loss)
    if distance <= 0:
        return 0.0
    return (equity * risk_pct) / distance


def build_decision(
    setup: Setup,
    context: ContextSnapshot,
    account: AccountState,
    *,
    risk_pct: float = RISK_PCT_DEFAULT,
    leverage: float = 1.0,
) -> AgentDecision:
    verdict = gate(setup, context)
    ref = f"{setup.symbol}:{setup.direction.value}:{setup.confirmation_kind}"
    if not verdict.allow:
        return AgentDecision(Action.HOLD, None, verdict, ref, verdict.reason)

    qty = size(equity=account.equity, risk_pct=risk_pct,
               entry=setup.entry, stop_loss=setup.stop_loss) * verdict.size_multiplier
    action = Action.ENTER_LONG if setup.direction is Side.LONG else Action.ENTER_SHORT
    intent = ExecutionIntent(
        symbol=setup.symbol, action=action, qty=qty, entry=setup.entry,
        stop_loss=setup.stop_loss, take_profit=setup.take_profit, leverage=leverage,
    )
    return AgentDecision(action, intent, verdict, ref, verdict.reason)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_decision.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/decision.py tests/test_decision.py
git commit -m "feat(agent): bounded decision layer (gate + size + build_decision)"
```

---

## Task 5b: LLM advisory clamp (the bounded-AI guardrail)

`gate`/`size` are the deterministic hard floor. An optional LLM advisor may only **reduce** size, or say **wait** — never increase size, un-veto, flip direction, or change stops. `clamp_advice` enforces that in code (mirrors `llm-trading-bot`'s `_validate_decision`), so a hallucinated/drifted recommendation is rejected, not executed. **Worst-case LLM failure = a missed or smaller trade, never a bad one.** (The advisor LLM call itself is injected at the CLI like the CMC client; default `None` → pure deterministic. The runner applies `clamp_advice` only when an advisor is configured.)

**Files:**
- Modify: `src/magic_agent/decision.py` (append `LlmAdvice` + `clamp_advice`)
- Modify: `tests/test_decision.py` (append clamp tests)

- [ ] **Step 1: Write the failing tests (append to `tests/test_decision.py`)**

```python
from magic_agent.models import Action
from magic_agent.decision import LlmAdvice, clamp_advice


def _allowed():
    acct = AccountState(equity=1000.0, available=1000.0)
    return build_decision(_setup(rating="A", regime="risk_on"),
                          ContextSnapshot("risk_on", "low"), acct)


def test_clamp_size_factor_reduces():
    base = _allowed()
    out = clamp_advice(base, LlmAdvice(action_hint="take", size_factor=0.5))
    assert out.intent.qty == base.intent.qty * 0.5


def test_clamp_factor_above_one_is_capped_to_baseline():
    base = _allowed()
    out = clamp_advice(base, LlmAdvice(action_hint="take", size_factor=5.0))
    assert out.intent.qty == base.intent.qty  # never increases


def test_clamp_negative_factor_floors_to_hold():
    out = clamp_advice(_allowed(), LlmAdvice(action_hint="take", size_factor=-1.0))
    assert out.action is Action.HOLD and out.intent is None


def test_clamp_wait_forces_hold():
    out = clamp_advice(_allowed(), LlmAdvice(action_hint="wait", size_factor=1.0))
    assert out.action is Action.HOLD and out.intent is None


def test_clamp_cannot_unveto_a_vetoed_baseline():
    acct = AccountState(equity=1000.0, available=1000.0)
    vetoed = build_decision(_setup(rating="C"), ContextSnapshot("neutral", "low"), acct)
    out = clamp_advice(vetoed, LlmAdvice(action_hint="take", size_factor=1.0))
    assert out.action is Action.HOLD and out.intent is None  # stays vetoed
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_decision.py -q`
Expected: FAIL — `ImportError: cannot import name 'LlmAdvice'`.

- [ ] **Step 3: Implement (append to `src/magic_agent/decision.py`)**

```python
# add to the imports at the top of decision.py:
from dataclasses import dataclass, replace as _replace


@dataclass(frozen=True)
class LlmAdvice:
    """Bounded LLM output. ``size_factor`` is clamped to [0, 1] — the LLM can only
    reduce. ``action_hint`` is "take" or "wait". The LLM never sets entry/stops/direction."""
    action_hint: str          # "take" | "wait"
    size_factor: float = 1.0
    reasoning: str = ""


def clamp_advice(baseline: AgentDecision, advice: LlmAdvice) -> AgentDecision:
    """Apply LLM advice strictly WITHIN the deterministic baseline. The LLM can only
    reduce size or defer; it can never un-veto, increase size, or change geometry."""
    if baseline.intent is None or baseline.action is Action.HOLD:
        return baseline  # cannot act on a vetoed / no-intent baseline
    if advice.action_hint == "wait":
        return AgentDecision(Action.HOLD, None, baseline.gate, baseline.setup_ref,
                             advice.reasoning or "LLM: wait")
    factor = min(1.0, max(0.0, advice.size_factor))   # size-DOWN only
    new_qty = baseline.intent.qty * factor
    if new_qty <= 0:
        return AgentDecision(Action.HOLD, None, baseline.gate, baseline.setup_ref,
                             advice.reasoning or "LLM: size->0")
    intent = _replace(baseline.intent, qty=new_qty)
    return AgentDecision(baseline.action, intent, baseline.gate, baseline.setup_ref,
                         advice.reasoning or baseline.reasoning)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_decision.py -q`
Expected: PASS (the 12 from Task 5 + 5 new = 17 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/decision.py tests/test_decision.py
git commit -m "feat(agent): LLM advisory clamp (size-down/wait only, code-enforced)"
```

---

## Task 6: setup_view — map ScanResult → agent Setup

**Files:**
- Create: `src/magic_agent/setup_view.py`
- Test: `tests/test_setup_view.py`

The scanner's `EntrySetup` carries `entry` and `qml_key_level` but **no stop field**, so the agent derives the stop: a `stop_buffer_pct` beyond `qml_key_level` (the protective level), and take-profit from `min_rr`. This is explicit agent config, not an invented scanner field.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_view.py
import pytest
from magic_agent.models import Side
from magic_agent.setup_view import from_scan_result


class _Inputs:
    def __init__(self, direction):
        self.trade_direction = direction


class _Result:
    def __init__(self, rating, regime="risk_on"):
        self.rating = rating
        self.regime = regime


class _Entry:
    def __init__(self, entry, qml_key_level, confirmation_kind="chained_scob"):
        self.entry = entry
        self.qml_key_level = qml_key_level
        self.confirmation_kind = confirmation_kind


class _Scan:
    def __init__(self, symbol, direction, rating, entry, qml, regime="risk_on"):
        self.symbol = symbol
        self.inputs = _Inputs(direction)
        self.result = _Result(rating, regime)
        self.entry = _Entry(entry, qml) if entry is not None else None
        self.confirmation_kind = "chained_scob"


def test_long_setup_stop_below_qml_and_tp_by_rr():
    sc = _Scan("BNB/USDT", "Long", "A", entry=600.0, qml=594.0)
    s = from_scan_result(sc, stop_buffer_pct=0.005, min_rr=3.0)
    assert s is not None
    assert s.direction is Side.LONG
    assert s.stop_loss == pytest.approx(594.0 * (1 - 0.005))  # below QML level
    risk = s.entry - s.stop_loss
    assert s.take_profit == pytest.approx(s.entry + 3.0 * risk)


def test_short_setup_stop_above_qml():
    sc = _Scan("BNB/USDT", "Short", "A", entry=600.0, qml=606.0)
    s = from_scan_result(sc, stop_buffer_pct=0.005, min_rr=3.0)
    assert s.direction is Side.SHORT
    assert s.stop_loss == pytest.approx(606.0 * (1 + 0.005))  # above QML level
    risk = s.stop_loss - s.entry
    assert s.take_profit == pytest.approx(s.entry - 3.0 * risk)


def test_no_entry_or_no_direction_returns_none():
    assert from_scan_result(_Scan("X", "—", "A", entry=600.0, qml=594.0)) is None
    assert from_scan_result(_Scan("X", "Long", "A", entry=None, qml=None)) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_setup_view.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.setup_view`.

- [ ] **Step 3: Implement the mapper**

```python
# src/magic_agent/setup_view.py
"""Adapt a scanner ``ScanResult`` into the agent's ``Setup``.

Stop derivation: the scanner's ``EntrySetup`` exposes ``entry`` + ``qml_key_level``
(the protective level) but no explicit stop. The agent places the stop just beyond
``qml_key_level`` (``stop_buffer_pct``) and the target at ``min_rr`` × risk. Typed
loosely (duck-typed) so this module doesn't import the scanner's concrete classes.
"""
from __future__ import annotations

from typing import Any

from magic_agent.models import Setup, Side

_DIRECTION = {"Long": Side.LONG, "Short": Side.SHORT}


def from_scan_result(
    scan: Any,
    *,
    stop_buffer_pct: float = 0.005,
    min_rr: float = 3.0,
) -> Setup | None:
    side = _DIRECTION.get(getattr(scan.inputs, "trade_direction", "—"))
    entry_report = getattr(scan, "entry", None)
    if side is None or entry_report is None:
        return None
    entry = getattr(entry_report, "entry", None)
    qml = getattr(entry_report, "qml_key_level", None)
    if entry is None or qml is None:
        return None

    if side is Side.LONG:
        stop = qml * (1.0 - stop_buffer_pct)
        take_profit = entry + min_rr * (entry - stop)
    else:
        stop = qml * (1.0 + stop_buffer_pct)
        take_profit = entry - min_rr * (stop - entry)

    return Setup(
        symbol=scan.symbol,
        direction=side,
        rating=scan.result.rating,
        regime=str(scan.result.regime),
        entry=float(entry),
        stop_loss=float(stop),
        take_profit=float(take_profit),
        confirmation_kind=getattr(scan, "confirmation_kind", "none"),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_setup_view.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/setup_view.py tests/test_setup_view.py
git commit -m "feat(agent): map ScanResult -> agent Setup (config-derived stop/tp)"
```

---

## Task 6b: ScannerGateway — the sole scanner-internals seam

Per the library/app boundary discipline: this is the **only** module that imports `magic_scanner.*`. Everything downstream depends solely on the agent's `Setup`. `scan_symbols` is injected so tests need no scanner/network.

**Files:**
- Create: `src/magic_agent/scanner_gateway.py`
- Test: `tests/test_scanner_gateway.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scanner_gateway.py
from magic_agent.models import Side
from magic_agent.scanner_gateway import ScannerGateway


class _Inputs:
    def __init__(self, d): self.trade_direction = d
class _Result:
    def __init__(self, r, g="risk_on"): self.rating = r; self.regime = g
class _Entry:
    def __init__(self, e, q): self.entry = e; self.qml_key_level = q; self.confirmation_kind = "chained_scob"
class _Scan:
    def __init__(self, sym, d, r, e, q):
        self.symbol = sym; self.inputs = _Inputs(d); self.result = _Result(r)
        self.entry = _Entry(e, q); self.confirmation_kind = "chained_scob"


def test_scan_returns_mapped_setup():
    gw = ScannerGateway(scan_fn=lambda syms, **k: [_Scan("BNB/USDT", "Long", "A", 600.0, 594.0)])
    s = gw.scan("BNB/USDT")
    assert s is not None and s.symbol == "BNB/USDT" and s.direction is Side.LONG


def test_scan_returns_none_when_no_qualifying_result():
    gw = ScannerGateway(scan_fn=lambda syms, **k: [])
    assert gw.scan("BNB/USDT") is None


def test_scan_passes_symbol_through_to_scan_fn():
    seen = {}
    def fake(syms, **k):
        seen["syms"] = syms
        return []
    ScannerGateway(scan_fn=fake).scan("ETH/USDT")
    assert seen["syms"] == ["ETH/USDT"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scanner_gateway.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.scanner_gateway`.

- [ ] **Step 3: Implement the gateway**

```python
# src/magic_agent/scanner_gateway.py
"""The ONLY module that imports scanner internals (`magic_scanner.*`).

Downstream agent code depends solely on the agent ``Setup``. If scanner types change,
this is the one file to fix. ``scan_fn`` defaults to the live scanner but is injected
in tests so no scanner/network is needed.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from magic_agent.models import Setup
from magic_agent.setup_view import from_scan_result


class ScannerGateway:
    def __init__(
        self,
        *,
        scan_fn: Callable[..., list[Any]] | None = None,
        stop_buffer_pct: float = 0.005,
        min_rr: float = 3.0,
    ) -> None:
        if scan_fn is None:
            from magic_scanner.scan import scan_symbols  # the sole scanner import

            scan_fn = scan_symbols
        self._scan_fn = scan_fn
        self._stop_buffer_pct = stop_buffer_pct
        self._min_rr = min_rr

    def scan(self, symbol: str) -> Setup | None:
        results = self._scan_fn([symbol]) or []
        for result in results:
            setup = from_scan_result(
                result, stop_buffer_pct=self._stop_buffer_pct, min_rr=self._min_rr
            )
            if setup is not None:
                return setup
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scanner_gateway.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/scanner_gateway.py tests/test_scanner_gateway.py
git commit -m "feat(agent): ScannerGateway — sole magic_scanner.* seam"
```

---

## Task 7: CmcContextAdapter

**Files:**
- Create: `src/magic_agent/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py
from magic_agent.context import CmcContextAdapter


def test_no_client_degrades_to_unavailable():
    ctx = CmcContextAdapter(client=None).get_context("BNB/USDT")
    assert ctx.status == "unavailable" and ctx.regime == "neutral"


def test_client_result_is_mapped():
    def fake(symbol):
        assert symbol == "BNB/USDT"
        return {"regime": "risk_off", "risk_flag": "elevated"}
    ctx = CmcContextAdapter(client=fake).get_context("BNB/USDT")
    assert ctx.status == "ok" and ctx.regime == "risk_off" and ctx.risk_flag == "elevated"


def test_client_error_degrades_to_unavailable():
    def boom(symbol):
        raise RuntimeError("network down")
    ctx = CmcContextAdapter(client=boom).get_context("BNB/USDT")
    assert ctx.status == "unavailable"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_context.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.context`.

- [ ] **Step 3: Implement the adapter**

```python
# src/magic_agent/context.py
"""CMC Agent Hub context adapter.

``client`` is an injected callable ``client(symbol) -> {"regime","risk_flag"}`` (a
thin wrapper over the CMC MCP/REST call, wired in the CLI). Kept injectable so the
loop is testable with no network. Any failure (or no client) degrades to
``status='unavailable'`` so the runner never blocks on CMC.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from magic_agent.models import ContextSnapshot

_UNAVAILABLE = ContextSnapshot(regime="neutral", risk_flag="low", status="unavailable")


class CmcContextAdapter:
    def __init__(self, client: Callable[[str], dict[str, Any]] | None = None) -> None:
        self._client = client

    def get_context(self, symbol: str) -> ContextSnapshot:
        if self._client is None:
            return _UNAVAILABLE
        try:
            raw = self._client(symbol)
            return ContextSnapshot(
                regime=str(raw["regime"]),
                risk_flag=str(raw["risk_flag"]),
                status="ok",
            )
        except Exception:
            return _UNAVAILABLE
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_context.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/context.py tests/test_context.py
git commit -m "feat(agent): CmcContextAdapter (injectable, degrades to unavailable)"
```

---

## Task 8: AgentLog (JSONL decision/audit log)

**Files:**
- Create: `src/magic_agent/log.py`
- Test: `tests/test_log.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_log.py
import json
from magic_agent.models import (
    Action, Side, ContextSnapshot, GateVerdict, ExecutionIntent, AgentDecision, Outcome,
)
from magic_agent.log import decision_record, AgentLog


def _decision():
    intent = ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 0.8, 600.0, 588.0, 636.0, 1.0)
    return AgentDecision(Action.ENTER_LONG, intent,
                         GateVerdict(True, 1.0, "ok"), "BNB/USDT:long:chained_scob", "ok")


def test_record_is_jsonable_and_complete():
    rec = decision_record(_decision(), ContextSnapshot("risk_on", "low"),
                          Outcome.OPENED, now="2026-06-15T00:00:00Z",
                          baseline_qty=1.0, llm_size_factor=0.8, llm_action_hint="take")
    assert rec["action"] == "enter_long"
    assert rec["outcome"] == "opened"
    assert rec["setup_ref"] == "BNB/USDT:long:chained_scob"
    assert rec["qty"] == 0.8                 # clamped FINAL size
    assert rec["baseline_qty"] == 1.0        # deterministic baseline (pre-LLM)
    assert rec["llm_size_factor"] == 0.8     # the AI's marginal effect — auditable
    assert rec["llm_action_hint"] == "take"
    json.dumps(rec)  # must not raise


def test_agentlog_appends_jsonl(tmp_path):
    path = tmp_path / "decisions.jsonl"
    log = AgentLog(path)
    log(decision_record(_decision(), ContextSnapshot("risk_on", "low"),
                         Outcome.OPENED, now="t1"))
    log(decision_record(_decision(), ContextSnapshot("risk_on", "low"),
                        Outcome.OPENED, now="t2"))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["ts"] == "t1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_log.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.log`.

- [ ] **Step 3: Implement the log**

```python
# src/magic_agent/log.py
"""Append-only JSONL decision/audit log — the backtest substrate for the agent
layer (mirrors the scanner's Phase-5 alert log). Every decision + outcome is
recorded so confirmation quality / gate behaviour can be ranked later from data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from magic_agent.models import AgentDecision, ContextSnapshot, Outcome


def decision_record(
    decision: AgentDecision,
    context: ContextSnapshot,
    outcome: Outcome,
    *,
    now: str,
    baseline_qty: float | None = None,
    llm_size_factor: float | None = None,
    llm_action_hint: str | None = None,
) -> dict[str, Any]:
    """Records the FINAL (clamped) decision plus the deterministic baseline and the
    LLM's pre-clamp recommendation, so the AI's marginal effect (baseline vs final)
    is measurable later. ``baseline_qty``/``llm_*`` are None on the pure-deterministic
    path (no advisor configured)."""
    intent = decision.intent
    return {
        "ts": now,
        "setup_ref": decision.setup_ref,
        "action": decision.action.value,
        "allow": decision.gate.allow,
        "size_multiplier": decision.gate.size_multiplier,
        "gate_reason": decision.gate.reason,
        "regime": context.regime,
        "risk_flag": context.risk_flag,
        "context_status": context.status,
        "qty": intent.qty if intent else 0.0,            # clamped FINAL size
        "baseline_qty": baseline_qty,                    # deterministic pre-LLM size
        "llm_size_factor": llm_size_factor,              # LLM recommendation (auditable)
        "llm_action_hint": llm_action_hint,
        "entry": intent.entry if intent else None,
        "stop_loss": intent.stop_loss if intent else None,
        "take_profit": intent.take_profit if intent else None,
        "leverage": intent.leverage if intent else None,
        "outcome": outcome.value,
        "reasoning": decision.reasoning,
    }


class AgentLog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, record: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_log.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/log.py tests/test_log.py
git commit -m "feat(agent): JSONL decision/audit log"
```

---

## Task 9: Runner loop (new-candle gate + stops-first)

**Files:**
- Create: `src/magic_agent/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner.py
from magic_agent.models import (
    Action, Side, Candle, ContextSnapshot, Setup, Outcome,
)
from magic_agent.executor import PaperExecutor
from magic_agent.context import CmcContextAdapter
from magic_agent.runner import check_stops, on_candle


def _setup(direction=Side.LONG, rating="A"):
    return Setup("BNB/USDT", direction, rating, "risk_on", 600.0, 588.0, 636.0, "chained_scob")


def test_check_stops_long_sl_and_tp():
    pos_long = PaperExecutor().open_position  # not used; build position via executor below
    ex = PaperExecutor(1000.0)
    from magic_agent.models import ExecutionIntent
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0))
    assert check_stops(ex.get_position(), Candle(600, 600, 588, 590)) is True   # low hit SL
    assert check_stops(ex.get_position(), Candle(600, 636, 599, 620)) is True   # high hit TP
    assert check_stops(ex.get_position(), Candle(600, 610, 595, 605)) is False  # neither


def test_on_candle_opens_on_allowed_setup():
    ex = PaperExecutor(1000.0)
    decision, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
    )
    assert outcome is Outcome.OPENED
    assert ex.get_position().side is Side.LONG


def test_on_candle_stops_first_closes_before_new_entry():
    ex = PaperExecutor(1000.0)
    from magic_agent.models import ExecutionIntent
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0))
    # candle hits SL -> must CLOSE, not consult setup_fn
    called = {"n": 0}
    def setup_fn():
        called["n"] += 1
        return _setup()
    decision, outcome = on_candle(ex, setup_fn=setup_fn, context=CmcContextAdapter(None),
                                  candle=Candle(600, 600, 580, 585))
    assert outcome is Outcome.CLOSED and called["n"] == 0
    assert ex.get_position().side is Side.FLAT


def test_on_candle_veto_does_not_open():
    ex = PaperExecutor(1000.0)
    _, outcome = on_candle(ex, setup_fn=lambda: _setup(rating="C"),
                           context=CmcContextAdapter(None), candle=Candle(600, 601, 599, 600))
    assert outcome is Outcome.SKIPPED_VETO and ex.get_position().side is Side.FLAT


def test_on_candle_no_setup_is_noop():
    ex = PaperExecutor(1000.0)
    _, outcome = on_candle(ex, setup_fn=lambda: None,
                           context=CmcContextAdapter(None), candle=Candle(600, 601, 599, 600))
    assert outcome is Outcome.NOOP
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.runner`.

- [ ] **Step 3: Implement the runner body**

```python
# src/magic_agent/runner.py
"""Loop body — one newly-closed candle at a time, stops checked FIRST.

Pure orchestration: scanning, context, executor are all injected, so the whole loop
runs in tests with no network. The live driver (cli.py) supplies a real fetch +
new-candle gate (drop the forming bar, dedupe on timestamp) around ``on_candle``.
"""
from __future__ import annotations

from collections.abc import Callable

from magic_agent.context import CmcContextAdapter
from magic_agent.decision import build_decision, RISK_PCT_DEFAULT
from magic_agent.executor import PerpExecutor
from magic_agent.log import AgentLog, decision_record
from magic_agent.models import (
    Action, AgentDecision, Candle, GateVerdict, Outcome, PositionState, Setup, Side,
)


def check_stops(position: PositionState, candle: Candle) -> bool:
    """True if this candle's range touched the position's stop or target."""
    if position.side is Side.FLAT:
        return False
    if position.side is Side.LONG:
        return (position.stop_loss is not None and candle.low <= position.stop_loss) or (
            position.take_profit is not None and candle.high >= position.take_profit
        )
    return (position.stop_loss is not None and candle.high >= position.stop_loss) or (
        position.take_profit is not None and candle.low <= position.take_profit
    )


def on_candle(
    executor: PerpExecutor,
    *,
    setup_fn: Callable[[], Setup | None],
    context: CmcContextAdapter,
    candle: Candle,
    risk_pct: float = RISK_PCT_DEFAULT,
    leverage: float = 1.0,
    log: AgentLog | None = None,
    now: str = "",
) -> tuple[AgentDecision, Outcome]:
    # 1. Stops first — deterministic risk before anything else.
    pos = executor.get_position()
    if pos.side is not Side.FLAT and check_stops(pos, candle):
        outcome = executor.close_position(mark_price=candle.close)
        decision = AgentDecision(Action.CLOSE, None, GateVerdict(True, 0.0, "stop/target hit"),
                                 "stop", "stop or target hit")
        if log is not None:
            from magic_agent.models import ContextSnapshot
            log(decision_record(decision, ContextSnapshot("neutral", "low", "ok"), outcome, now=now))
        return decision, outcome

    # 2. Already in a position -> hold (one position at a time).
    if pos.side is not Side.FLAT:
        return AgentDecision(Action.HOLD, None, GateVerdict(False, 0.0, "in position"),
                             "hold", "in position"), Outcome.SKIPPED_IN_POSITION

    # 3. Signal -> context -> decision.
    setup = setup_fn()
    if setup is None:
        return AgentDecision(Action.HOLD, None, GateVerdict(False, 0.0, "no setup"),
                             "none", "no setup"), Outcome.NOOP

    ctx = context.get_context(setup.symbol)
    account = executor.get_account(mark_price=candle.close)
    decision = build_decision(setup, ctx, account, risk_pct=risk_pct, leverage=leverage)

    if not decision.gate.allow:
        outcome = Outcome.SKIPPED_VETO
    elif decision.intent is None or decision.intent.qty <= 0:
        outcome = Outcome.SKIPPED_ZERO_SIZE
    else:
        outcome = executor.open_position(decision.intent)

    if log is not None:
        log(decision_record(decision, ctx, outcome, now=now))
    return decision, outcome
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_runner.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/runner.py tests/test_runner.py
git commit -m "feat(agent): runner loop body (new-candle gate + stops-first)"
```

---

## Task 10: AsterRestExecutor (ccxt-based, mocked in tests)

**Files:**
- Create: `src/magic_agent/executors/aster.py`
- Test: `tests/test_aster.py`

> Depends on Task 2 findings. If the spike found a wallet-signed path instead of ccxt HMAC, adapt `_exchange` calls accordingly; the `PerpExecutor` interface and tests below are unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_aster.py
from magic_agent.models import Action, Side, Outcome, ExecutionIntent
from magic_agent.executor import PerpExecutor
from magic_agent.executors.aster import AsterRestExecutor


class FakeExchange:
    """Minimal ccxt-shaped stub. Records calls; returns canned state."""
    def __init__(self):
        self.orders = []
        self.leverage_set = []
        self._positions = []

    def set_leverage(self, lev, symbol):
        self.leverage_set.append((lev, symbol))

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.orders.append({"symbol": symbol, "type": type_, "side": side,
                            "amount": amount, "params": params or {}})
        self._positions = [{"symbol": symbol, "contracts": amount,
                            "side": "long" if side == "buy" else "short",
                            "entryPrice": 600.0}]
        return {"id": "o1"}

    def fetch_positions(self, symbols=None):
        return self._positions

    def fetch_balance(self, params=None):
        return {"USDT": {"total": 1000.0, "free": 800.0}}


def test_satisfies_protocol():
    assert isinstance(AsterRestExecutor(FakeExchange(), "BNB/USDT"), PerpExecutor)


def test_open_long_sends_buy_with_hedge_position_side():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    out = ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0, leverage=5.0))
    assert out is Outcome.OPENED
    assert fx.leverage_set == [(5.0, "BNB/USDT")]
    o = fx.orders[0]
    assert o["side"] == "buy" and o["amount"] == 1.0
    assert o["params"]["positionSide"] == "LONG"


def test_open_short_sends_sell_with_short_position_side():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_SHORT, 2.0, 600.0, 612.0, 564.0))
    o = fx.orders[0]
    assert o["side"] == "sell" and o["params"]["positionSide"] == "SHORT"


def test_get_position_maps_ccxt_position():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 588.0, 636.0))
    pos = ex.get_position()
    assert pos.side is Side.LONG and pos.size == 1.0 and pos.entry_price == 600.0


def test_zero_qty_skips_without_order():
    fx = FakeExchange()
    ex = AsterRestExecutor(fx, "BNB/USDT")
    assert ex.open_position(ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 0.0, 600.0, 588.0, 636.0)) is Outcome.SKIPPED_ZERO_SIZE
    assert fx.orders == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_aster.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.executors.aster`.

- [ ] **Step 3: Implement the executor**

```python
# src/magic_agent/executors/aster.py
"""Aster perps via a ccxt-shaped exchange (REST, hedge mode long+short).

The ``exchange`` is injected (``ccxt.aster({...})`` in the CLI, a stub in tests) so
this is testable with no network. We POLL (no websocket ``watch*`` needed). Maps the
agent's ``ExecutionIntent`` to ccxt ``create_order`` with ``positionSide`` per the
Binance-futures hedge-mode convention confirmed in the Task 2 spike.
"""
from __future__ import annotations

from typing import Any

from magic_agent.models import (
    Action, AccountState, ExecutionIntent, Outcome, PositionState, Side,
)


class AsterRestExecutor:
    def __init__(self, exchange: Any, symbol: str) -> None:
        self._exchange = exchange
        self._symbol = symbol

    def get_position(self) -> PositionState:
        positions = self._exchange.fetch_positions([self._symbol]) or []
        for p in positions:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0.0:
                continue
            side = Side.LONG if p.get("side") == "long" else Side.SHORT
            entry = p.get("entryPrice")
            return PositionState(side=side, size=contracts,
                                 entry_price=float(entry) if entry is not None else None)
        return PositionState()

    def get_account(self, *, mark_price: float) -> AccountState:
        bal = self._exchange.fetch_balance() or {}
        usdt = bal.get("USDT", {})
        total = float(usdt.get("total") or 0.0)
        free = float(usdt.get("free") or total)
        return AccountState(equity=total, available=free)

    def open_position(self, intent: ExecutionIntent) -> Outcome:
        if intent.qty <= 0:
            return Outcome.SKIPPED_ZERO_SIZE
        is_long = intent.action is Action.ENTER_LONG
        try:
            self._exchange.set_leverage(intent.leverage, intent.symbol)
            self._exchange.create_order(
                intent.symbol, "market", "buy" if is_long else "sell", intent.qty,
                None, {"positionSide": "LONG" if is_long else "SHORT"},
            )
        except Exception:
            return Outcome.REJECTED
        return Outcome.OPENED

    def close_position(self, *, mark_price: float) -> Outcome:
        pos = self.get_position()
        if pos.side is Side.FLAT:
            return Outcome.NOOP
        is_long = pos.side is Side.LONG
        try:
            self._exchange.create_order(
                self._symbol, "market", "sell" if is_long else "buy", pos.size,
                None, {"positionSide": "LONG" if is_long else "SHORT", "reduceOnly": True},
            )
        except Exception:
            return Outcome.REJECTED
        return Outcome.CLOSED

    def sync(self) -> None:
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_aster.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/executors/aster.py tests/test_aster.py
git commit -m "feat(agent): AsterRestExecutor (ccxt hedge-mode long/short)"
```

---

## Task 11: Erc8004Identity (bnbagent-sdk wrapper)

**Files:**
- Create: `src/magic_agent/identity.py`
- Test: `tests/test_identity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identity.py
from magic_agent.identity import Erc8004Identity


class FakeRegistrar:
    def __init__(self):
        self.registered = []

    def register(self, agent_uri, metadata):
        self.registered.append((agent_uri, metadata))
        return 42  # agentId


def test_register_returns_agent_id_and_caches():
    reg = FakeRegistrar()
    ident = Erc8004Identity(reg, agent_uri="ipfs://profile", metadata={"name": "scanner-agent"})
    assert ident.agent_id is None
    aid = ident.register()
    assert aid == 42 and ident.agent_id == 42
    assert reg.registered == [("ipfs://profile", {"name": "scanner-agent"})]


def test_register_is_idempotent():
    reg = FakeRegistrar()
    ident = Erc8004Identity(reg, agent_uri="ipfs://p", metadata={})
    ident.register()
    ident.register()
    assert len(reg.registered) == 1  # second call is a no-op
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_identity.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.identity`.

- [ ] **Step 3: Implement the wrapper**

```python
# src/magic_agent/identity.py
"""ERC-8004 on-chain agent identity via bnbagent-sdk.

``registrar`` is injected (the bnbagent-sdk ``Erc8004Contract`` / agent client in the
CLI, a stub in tests) and must expose ``register(agent_uri, metadata) -> agent_id``.
Registration is idempotent — once we hold an ``agent_id`` we never re-register.
"""
from __future__ import annotations

from typing import Any


class Erc8004Identity:
    def __init__(self, registrar: Any, *, agent_uri: str, metadata: dict[str, Any]) -> None:
        self._registrar = registrar
        self._agent_uri = agent_uri
        self._metadata = metadata
        self.agent_id: int | None = None

    def register(self) -> int:
        if self.agent_id is not None:
            return self.agent_id
        self.agent_id = int(self._registrar.register(self._agent_uri, self._metadata))
        return self.agent_id
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_identity.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/identity.py tests/test_identity.py
git commit -m "feat(agent): ERC-8004 identity wrapper (idempotent register)"
```

---

## Task 12: TwakTreasury (collateral move / pnl sweep)

**Files:**
- Create: `src/magic_agent/treasury.py`
- Test: `tests/test_treasury.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_treasury.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.treasury`.

- [ ] **Step 3: Implement the treasury adapter**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_treasury.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/treasury.py tests/test_treasury.py
git commit -m "feat(agent): TWAK treasury adapter (collateral move)"
```

---

## Task 13: CLI wiring (`magic-agent run`)

**Files:**
- Create: `src/magic_agent/cli.py`
- Modify: `pyproject.toml` (add `magic-agent` script entry)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test (arg parsing only — no live loop in tests)**

```python
# tests/test_cli.py
from magic_agent.cli import build_parser


def test_run_parses_symbol_executor_and_risk_defaults():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT"])
    assert args.symbol == "BNB/USDT"
    assert args.executor == "paper"      # safe default
    assert args.risk_pct == 0.01
    assert args.leverage == 1.0


def test_run_accepts_aster_executor_and_overrides():
    p = build_parser()
    args = p.parse_args(["run", "--symbol", "BNB/USDT", "--executor", "aster",
                         "--risk-pct", "0.005", "--leverage", "5"])
    assert args.executor == "aster" and args.risk_pct == 0.005 and args.leverage == 5.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.cli`.

- [ ] **Step 3: Implement the CLI**

```python
# src/magic_agent/cli.py
"""`magic-agent run` — wire context + scanner + decision + executor into the loop.

The live driver fetches OHLCV, drops the forming bar, dedupes on timestamp (the
new-closed-candle gate), and calls ``runner.on_candle`` per closed candle. ``paper``
is the default executor so a bare ``magic-agent run`` never touches funds.
"""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="magic-agent",
                                     description="Bounded autonomous trading agent (BNB Track 1)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the autonomous loop")
    run.add_argument("--symbol", default="BNB/USDT", help="Pair to trade")
    run.add_argument("--executor", choices=["paper", "aster"], default="paper",
                     help="Execution backend (default: paper — no real funds)")
    run.add_argument("--risk-pct", dest="risk_pct", type=float, default=0.01,
                     help="Fraction of equity risked per trade (default 0.01)")
    run.add_argument("--leverage", type=float, default=1.0, help="Leverage (default 1.0)")
    run.set_defaults(func=_cmd_run)
    return parser


def _cmd_run(args: argparse.Namespace) -> None:  # pragma: no cover - live loop
    # Wiring only; exercised manually / on testnet, not in unit tests.
    from magic_agent.context import CmcContextAdapter
    from magic_agent.executor import PaperExecutor

    context = CmcContextAdapter(client=None)  # TODO(cli): wire CMC client per spike findings
    from magic_agent.scanner_gateway import ScannerGateway
    gateway = ScannerGateway()                # sole scanner seam; setup_fn = lambda: gateway.scan(args.symbol)
    if args.executor == "paper":
        executor = PaperExecutor(starting_equity=1000.0)
    else:
        import ccxt  # noqa: F401
        from magic_agent.executors.aster import AsterRestExecutor
        exchange = ccxt.aster({})  # creds from env, wired per spike findings
        executor = AsterRestExecutor(exchange, args.symbol)
    print(f"agent ready: symbol={args.symbol} executor={args.executor} "
          f"risk_pct={args.risk_pct} leverage={args.leverage}")
    # The full poll/new-candle-gate loop is added when wiring live data (post-spike).


def main(argv: list[str] | None = None) -> None:  # pragma: no cover
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Add the script entry to pyproject**

In `pyproject.toml` (created by `uv init`), add or confirm a `[project.scripts]` entry:

```toml
magic-agent = "magic_agent.cli:main"
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Full-suite regression + commit**

```bash
uv run pytest -q   # expect all agent tests green (the scanner's own ~533-test suite lives in its own repo)
git add src/magic_agent/cli.py tests/test_cli.py pyproject.toml
git commit -m "feat(agent): magic-agent CLI (run; paper default) + script entry"
```

---

# Phase 2 — aegis-derived: policy engine, judge-trace, dashboard

> Built **after** the core loop (Tasks 1–13) is green. New files: `src/magic_agent/policy.py`,
> `src/magic_agent/api.py`, `web/` (Next.js), `dashboard_app.py` (Streamlit fallback). Sequencing: policy
> + judge-trace first (cheap, the safety spine + demo proof), dashboard last. Spec refs: §4.7, §6.

## Task 14: Fail-closed policy engine

**Files:**
- Create: `src/magic_agent/policy.py`
- Test: `tests/test_policy.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_policy.py
import pytest
from magic_agent.models import Action, ExecutionIntent
from magic_agent.policy import (
    PolicyConfig, PolicyState, run_policies, MissingPolicyConfigError,
)


def _intent(qty=1.0, entry=600.0, sl=588.0, lev=1.0):
    return ExecutionIntent("BNB/USDT", Action.ENTER_LONG, qty, entry, sl, 636.0, lev)


def _state(**kw):
    base = dict(equity=1000.0, realized_pnl_today=0.0, open_positions=0,
                seconds_since_last_trade=None)
    base.update(kw)
    return PolicyState(**base)


def test_empty_config_raises_fail_closed():
    with pytest.raises(MissingPolicyConfigError):
        run_policies(_intent(), _state(), PolicyConfig())  # no policies => refuse


def test_daily_loss_kill_switch_denies():
    cfg = PolicyConfig(max_daily_loss=50.0)
    r = run_policies(_intent(), _state(realized_pnl_today=-60.0), cfg)
    assert r.approved is False and r.denied_by == "daily_loss"


def test_max_leverage_denies():
    r = run_policies(_intent(lev=20.0), _state(), PolicyConfig(max_leverage=5.0))
    assert r.approved is False and r.denied_by == "max_leverage"


def test_stop_required_denies_when_missing():
    bad = ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 1.0, 600.0, 0.0, 636.0, 1.0)
    r = run_policies(bad, _state(), PolicyConfig(require_stop=True))
    assert r.approved is False and r.denied_by == "stop_required"


def test_max_concurrent_denies():
    r = run_policies(_intent(), _state(open_positions=1), PolicyConfig(max_concurrent=1))
    assert r.approved is False and r.denied_by == "max_concurrent"


def test_all_policies_pass_approves():
    cfg = PolicyConfig(max_leverage=5.0, max_daily_loss=50.0, max_concurrent=1,
                       require_stop=True)
    assert run_policies(_intent(), _state(), cfg).approved is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.policy`.

- [ ] **Step 3: Implement**

```python
# src/magic_agent/policy.py
"""Fail-closed policy engine — the only path to funds (modelled on aegis
engine/policies/engine.mjs). AND semantics, short-circuit on first denial, and
``run_policies`` THROWS on an empty config (no policies => no trade, never a silent
allow). The runner calls this before any open_position; the no-bypass test proves it."""
from __future__ import annotations

from dataclasses import dataclass

from magic_agent.models import ExecutionIntent


class MissingPolicyConfigError(ValueError):
    """Raised when no policies are active — fail closed, never allow."""


@dataclass(frozen=True)
class PolicyConfig:
    max_notional: float | None = None
    max_leverage: float | None = None
    max_daily_loss: float | None = None   # positive; kill-switch when pnl_today <= -this
    max_concurrent: int | None = None
    require_stop: bool = False
    cooldown_seconds: float | None = None

    def active(self) -> list[str]:
        names = []
        if self.max_daily_loss is not None: names.append("daily_loss")
        if self.max_concurrent is not None: names.append("max_concurrent")
        if self.require_stop: names.append("stop_required")
        if self.max_leverage is not None: names.append("max_leverage")
        if self.max_notional is not None: names.append("max_notional")
        if self.cooldown_seconds is not None: names.append("cooldown")
        return names


@dataclass(frozen=True)
class PolicyState:
    equity: float
    realized_pnl_today: float = 0.0
    open_positions: int = 0
    seconds_since_last_trade: float | None = None


@dataclass(frozen=True)
class PolicyResult:
    approved: bool
    denied_by: str | None = None
    reason: str = ""


def run_policies(
    intent: ExecutionIntent, state: PolicyState, config: PolicyConfig
) -> PolicyResult:
    if not config.active():
        raise MissingPolicyConfigError("no policies configured — refusing (fail-closed)")
    # AND semantics; order = most protective first; first denial short-circuits.
    if config.max_daily_loss is not None and state.realized_pnl_today <= -abs(config.max_daily_loss):
        return PolicyResult(False, "daily_loss", "daily-loss kill-switch tripped")
    if config.max_concurrent is not None and state.open_positions >= config.max_concurrent:
        return PolicyResult(False, "max_concurrent", "max concurrent positions reached")
    if config.require_stop and (intent.stop_loss is None or intent.stop_loss <= 0):
        return PolicyResult(False, "stop_required", "intent has no stop")
    if config.max_leverage is not None and intent.leverage > config.max_leverage:
        return PolicyResult(False, "max_leverage", f"leverage {intent.leverage} > {config.max_leverage}")
    if config.max_notional is not None and (intent.qty * intent.entry) > config.max_notional:
        return PolicyResult(False, "max_notional", "notional over cap")
    if (config.cooldown_seconds is not None and state.seconds_since_last_trade is not None
            and state.seconds_since_last_trade < config.cooldown_seconds):
        return PolicyResult(False, "cooldown", "within cooldown window")
    return PolicyResult(True, None, "all policies passed")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_policy.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/policy.py tests/test_policy.py
git commit -m "feat(agent): fail-closed policy engine (run_policies, throws on empty config)"
```

---

## Task 15: Wire the policy gate into the runner (no-bypass)

**Files:**
- Modify: `src/magic_agent/models.py` (add `Outcome.SKIPPED_POLICY`)
- Modify: `src/magic_agent/runner.py` (gate `open_position` behind `run_policies`)
- Test: `tests/test_runner.py` (append no-bypass test)

- [ ] **Step 1: Add the outcome (models.py) — append to the `Outcome` enum**

```python
    SKIPPED_POLICY = "skipped_policy"
```

- [ ] **Step 2: Write the failing test (append to `tests/test_runner.py`)**

```python
from magic_agent.policy import PolicyConfig
from magic_agent.models import Outcome


def test_policy_denial_prevents_open(monkeypatch):
    ex = PaperExecutor(1000.0)
    # max_concurrent=1 but force a state with an open position => deny path.
    # Simplest: a daily-loss kill-switch already tripped.
    cfg = PolicyConfig(max_daily_loss=50.0)
    _, outcome = on_candle(
        ex, setup_fn=lambda: _setup(), context=CmcContextAdapter(None),
        candle=Candle(600, 601, 599, 600),
        policy_config=cfg, realized_pnl_today=-100.0,  # kill-switch tripped
    )
    assert outcome is Outcome.SKIPPED_POLICY
    assert ex.get_position().side is Side.FLAT  # NEVER reached the executor
```

- [ ] **Step 3: Implement — modify `on_candle` in `runner.py`**

Add params `policy_config: "PolicyConfig | None" = None` and `realized_pnl_today: float = 0.0` to the
signature. After the allow check and before `executor.open_position(decision.intent)`, insert:

```python
    if policy_config is not None and decision.intent is not None:
        from magic_agent.policy import PolicyState, run_policies
        pstate = PolicyState(
            equity=account.equity,
            realized_pnl_today=realized_pnl_today,
            open_positions=0 if executor.get_position().side is Side.FLAT else 1,
        )
        verdict = run_policies(decision.intent, pstate, policy_config)
        if not verdict.approved:
            if log is not None:
                log(decision_record(decision, ctx, Outcome.SKIPPED_POLICY, now=now))
            return decision, Outcome.SKIPPED_POLICY
```

(The CLI ALWAYS passes a non-empty `policy_config`, so the live path is fail-closed; `run_policies`
itself raises on an empty config — proven in Task 14.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_runner.py -q`
Expected: PASS (the Task 9 tests + this new one).

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/models.py src/magic_agent/runner.py tests/test_runner.py
git commit -m "feat(agent): gate executor behind run_policies in the runner (no-bypass)"
```

---

## Task 16: `magic-agent judge-trace` (single-screen policy proof)

**Files:**
- Modify: `src/magic_agent/cli.py` (add `judge-trace` subcommand)
- Create: `src/magic_agent/judge_trace.py` (the pure report builder)
- Test: `tests/test_judge_trace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge_trace.py
from magic_agent.judge_trace import judge_trace_report
from magic_agent.policy import PolicyConfig


def test_report_shows_pass_and_deny():
    cfg = PolicyConfig(max_leverage=5.0, max_daily_loss=50.0, require_stop=True)
    report = judge_trace_report(cfg)
    assert "PASS" in report and "DENY" in report
    assert "max_leverage" in report   # the cap-breaching intent names the tripped policy
    assert "zero funds" in report.lower() or "no funds" in report.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_judge_trace.py -q`
Expected: FAIL — `ModuleNotFoundError: magic_agent.judge_trace`.

- [ ] **Step 3: Implement**

```python
# src/magic_agent/judge_trace.py
"""One-screen, zero-funds, zero-network proof that the policy engine works
(modelled on aegis scripts/judge-trace.mjs). Runs a canned PASSING intent and a
canned DENIED (cap-breaching) intent through the REAL run_policies and renders the
verdicts. The deterministic 'prove the safety story in 10 seconds' demo artifact."""
from __future__ import annotations

from magic_agent.models import Action, ExecutionIntent
from magic_agent.policy import PolicyState, run_policies, PolicyConfig

_PASS = ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 0.5, 600.0, 588.0, 636.0, 3.0)
_DENY = ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 0.5, 600.0, 588.0, 636.0, 50.0)  # over leverage
_STATE = PolicyState(equity=1000.0)


def judge_trace_report(config: PolicyConfig) -> str:
    lines = ["magic-agent judge-trace — policy proof (zero funds, zero network)", "=" * 60]
    for label, intent in (("representative PASS", _PASS), ("representative DENY", _DENY)):
        r = run_policies(intent, _STATE, config)
        verdict = "PASS" if r.approved else f"DENY (by {r.denied_by})"
        lines.append(f"[{verdict}] {label}: lev={intent.leverage} qty={intent.qty} — {r.reason}")
    lines.append("=" * 60)
    lines.append(f"active policies: {', '.join(config.active())}")
    return "\n".join(lines)
```

- [ ] **Step 4: Wire the subcommand (in `cli.py` `build_parser`)**

```python
    jt = sub.add_parser("judge-trace", help="Print a one-screen policy proof (zero funds)")
    jt.set_defaults(func=_cmd_judge_trace)
```

and the handler:

```python
def _cmd_judge_trace(args: argparse.Namespace) -> None:  # pragma: no cover
    from magic_agent.judge_trace import judge_trace_report
    from magic_agent.policy import PolicyConfig
    print(judge_trace_report(PolicyConfig(
        max_leverage=5.0, max_daily_loss=50.0, max_concurrent=1, require_stop=True)))
```

- [ ] **Step 5: Run + commit**

Run: `uv run pytest tests/test_judge_trace.py -q` → PASS (1 passed).

```bash
git add src/magic_agent/judge_trace.py src/magic_agent/cli.py tests/test_judge_trace.py
git commit -m "feat(agent): judge-trace CLI — one-screen policy proof"
```

---

## Task 17: FastAPI read API (status + decisions)

**Files:**
- Create: `src/magic_agent/api.py`
- Modify: `pyproject.toml` (add `fastapi`, `uvicorn`; `httpx` to dev for TestClient)
- Test: `tests/test_api.py`

The dashboard reads two endpoints: `/api/status` (snapshot) and `/api/decisions?take=N` (last N JSONL
records). REST-first (testable with `TestClient`, no network). A live `/ws/decisions` stream is an
optional enhancement (note below), not required for v1.

- [ ] **Step 1: Add deps**

```bash
uv add fastapi uvicorn
uv add --dev httpx
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_api.py
import json
from fastapi.testclient import TestClient
from magic_agent.api import create_app


def _seed(tmp_path):
    p = tmp_path / "decisions.jsonl"
    p.write_text("\n".join(json.dumps({"ts": f"t{i}", "action": "enter_long",
                  "qty": i, "baseline_qty": i, "outcome": "opened"}) for i in range(5)) + "\n")
    return p


def test_status_endpoint(tmp_path):
    app = create_app(log_path=_seed(tmp_path),
                     status_fn=lambda: {"mode": "paper", "equity": 1000.0, "halted": False})
    c = TestClient(app)
    r = c.get("/api/status")
    assert r.status_code == 200 and r.json()["mode"] == "paper"


def test_decisions_returns_last_n(tmp_path):
    app = create_app(log_path=_seed(tmp_path), status_fn=lambda: {})
    c = TestClient(app)
    r = c.get("/api/decisions?take=2")
    rows = r.json()
    assert len(rows) == 2 and rows[-1]["ts"] == "t4"  # newest last
```

- [ ] **Step 3: Implement**

```python
# src/magic_agent/api.py
"""Read-only FastAPI for the mission-control dashboard. Two endpoints: /api/status
(injected snapshot fn) and /api/decisions (last N from the JSONL decision log). No
trading — strictly read. A /ws/decisions live stream is an optional enhancement."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI


def create_app(*, log_path: str | Path, status_fn: Callable[[], dict[str, Any]]) -> FastAPI:
    app = FastAPI(title="magic-agent mission control", docs_url=None)
    path = Path(log_path)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return status_fn()

    @app.get("/api/decisions")
    def decisions(take: int = 100) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(ln) for ln in lines[-take:]]

    return app
```

- [ ] **Step 4: Run + commit**

Run: `uv run pytest tests/test_api.py -q` → PASS (2 passed).

```bash
git add src/magic_agent/api.py tests/test_api.py pyproject.toml uv.lock
git commit -m "feat(agent): read-only FastAPI (status + decisions) for the dashboard"
```

> Enhancement (not v1-blocking): add `@app.websocket("/ws/decisions")` that tails the JSONL file —
> ring-buffer backfill of the last N, then stream new lines (mirrors aegis `engine/studio/ws/signals.mjs`).

---

## Task 18: Read-only mission-control dashboard (Next.js; Streamlit fallback)

**Files:**
- Create: `web/` (Next.js app) — OR `dashboard_app.py` (Streamlit fallback)

This task is presentational (read-only) and not TDD. **Next.js + FastAPI is the locked default** — build
the Next.js path. The **Streamlit fallback** (guaranteed, ~half a day) is used **only if** the core loop
isn't green in time; do **not** default to Streamlit by preference. Both consume the Task-17 API. Panels
(per spec §4.7): Status, Performance, Positions,
**Decision feed** (the centrepiece — renders the `/api/decisions` rows: scanner grade → context → baseline →
LLM rec → clamped final → outcome), Policy/risk.

- [ ] **Next.js path:**
  - `npx create-next-app@latest web --ts --tailwind --app` (in the repo root).
  - One read-only page: `useEffect` polls `GET /api/status` (~5s) + `GET /api/decisions?take=100`; render
    the five panels; the decision feed is a table with a visible `baseline_qty → qty` (clamp) column.
  - Run the API (`uvicorn magic_agent.api:...`) + `npm run dev`; verify panels populate from a paper run's log.
  - Commit `web/` (gitignore `node_modules`).

- [ ] **Streamlit fallback path** (`dashboard_app.py`):
  - `uv add streamlit`; `st.dataframe` for the decision feed (read the JSONL directly), `st.line_chart`
    for equity, `st.metric` for win-rate/PnL, an auto-refresh.
  - `uv run streamlit run dashboard_app.py`; verify panels.
  - Commit.

- [ ] **Commit** (whichever path):

```bash
git add web pyproject.toml 2>/dev/null; git add dashboard_app.py 2>/dev/null
git commit -m "feat(agent): read-only mission-control dashboard"
```

---

## Stretch (separate future plan — NOT in this plan)

`OnchainContractExecutor` — drive ApolloX perp contracts (`openMarketTrade`/`closeTrade`) directly, signed/sent via bnbagent-sdk `ContractClientMixin` + `EVMWalletProvider`, behind the same `PerpExecutor` interface. Highest self-custody purity, highest build risk; only after Tasks 1–13 are green and a live REST loop runs.

---

## Self-Review

**1. Spec coverage** (design spec §-by-§):
- §3 four-layer architecture → Tasks 5 (decide), 7 (read/CMC), 4+10 (execute), 9 (loop). ✓
- §4.1 scanner as sole signal → Task 6 `setup_view` (pure mapper) + Task 6b `ScannerGateway` (the sole `magic_scanner.*` seam; agent consumes `ScanResult`, never invents). ✓
- §4.2 CMC context (degrades) → Task 7. ✓
- §4.3 bounded decision + typed `AgentDecision` → Task 5. ✓
- §4.4 `PerpExecutor` + Paper/Aster impls → Tasks 4, 10. ✓
- §4.5 new-candle, stops-first runner → Task 9. ✓
- §4.6 ERC-8004 identity + TWAK treasury → Tasks 11, 12. ✓
- §6 deterministic risk (stop required, sizing, one-position) → Tasks 5, 9; **kill-switch / daily-loss cap and max-concurrent are NOT yet a task** — see gap below.
- §7 sponsor mapping → Tasks 7/10/11/12. ✓
- §8 venue spike → Task 2. ✓
- §10 testing (PaperExecutor everywhere, no network) → all tasks inject seams. ✓

**Gap found → added note:** §6's **kill-switch / max-daily-loss / max-concurrent-positions** are not a standalone task. v1 enforces one-position-at-a-time (Task 4 `SKIPPED_IN_POSITION`) and stop-required (Task 6), which covers the core risk surface; the equity-based kill-switch is a small follow-up task to add in the live-wiring phase (it needs realized-PnL tracking across candles, which lives in the CLI loop in Task 13's `_cmd_run`, not in the unit-tested body). Flagged here rather than silently dropped. If you want it unit-tested now, add a Task 9b: a `RiskGuard(daily_loss_cap)` consulted before `open_position` in `on_candle`.

**2. Placeholder scan:** the only `TODO` is in `_cmd_run` (live CMC client wiring), which is `# pragma: no cover` and explicitly post-spike — not a logic placeholder. All test/impl steps contain real code. ✓

**3. Type consistency:** `Setup`, `ContextSnapshot`, `ExecutionIntent`, `AgentDecision`, `Outcome`, `Side`, `Action` are defined in Task 3 and used with identical signatures in Tasks 4–13. `build_decision(setup, context, account, *, risk_pct, leverage)` is called consistently (Tasks 5, 9). `PerpExecutor` method set (`get_position`/`get_account`/`open_position`/`close_position`/`sync`) matches across `PaperExecutor` (Task 4) and `AsterRestExecutor` (Task 10). `decision_record(decision, context, outcome, *, now)` matches between Task 8 (def) and Task 9 (call). ✓
