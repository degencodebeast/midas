# MIDAS Track 1 Spot Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate MIDAS from its perp-shaped Aster loop to a fail-closed, spot-long Track 1 runtime using CMC rank/veto context, scanner-owned Direct-QML authorization, mandatory RiskPolicy, and receipt-reconciled TWAK execution.

**Architecture:** Build one deterministic `DecisionPipeline` shared by live, paper, and replay. Side effects sit behind injected scanner, clock, quote, wallet, persistence, and execution ports. TWAK is the only signer for swaps and x402; `bnbagent-sdk` is used only for ERC-8004 identity.

**Tech Stack:** Python 3.11+, frozen dataclasses, `Decimal`, `pytest`, `uv`, `magic-scanner` (commit-pinned), TWAK CLI, BSC JSON-RPC, CMC x402.

---

## Authority And Execution Order

Read before every task: `docs/superpowers/specs/2026-06-21-track1-spot-agent-design.md`, `docs/superpowers/specs/2026-06-21-recon-findings.md`, workspace `docs/agent-trading-rules.md`, and the completed scanner plan. Implement in task order. Do not execute the superseded `2026-06-21-track1-spot-agent-implementation.md`.

## File Structure

| File | Responsibility |
|---|---|
| `src/magic_agent/spot_models.py` | Spot-only domain contracts shared by runtime and replay |
| `src/magic_agent/identity_registry.py` | Contract-first gold identities and scannability |
| `src/magic_agent/cmc_selector.py` | Immutable rank/veto/clamp snapshots; never setup authority |
| `src/magic_agent/scanner_gateway.py` | Convert scanner authorization into `AuthorizedSetup` |
| `src/magic_agent/risk_policy.py` | Mandatory sizing, portfolio caps, and action-aware fail-safe |
| `src/magic_agent/decision_pipeline.py` | Pure entry/exit decisions; no network or persistence |
| `src/magic_agent/state_journal.py` | Atomic integrity-protected runtime state |
| `src/magic_agent/execution_journal.py` | Durable execution state transitions and idempotency |
| `src/magic_agent/twak.py` | Strict TWAK subprocess boundary and redaction |
| `src/magic_agent/executability.py` | Fresh trade-size buy/sell route validation |
| `src/magic_agent/reconcile.py` | Receipt, confirmations, nonce, and balance-delta reconciliation |
| `src/magic_agent/position_manager.py` | Halt-safe stop/DOL exits and failed-exit retention |
| `src/magic_agent/x402.py` | Budgeted TWAK x402 CMC requests |
| `src/magic_agent/registration.py` | Separate Track 1 and ERC-8004 registration |
| `src/magic_agent/runner.py`, `live.py`, `cli.py` | Spot orchestration only; paper default |

### Task 1: Pin And Verify The Scanner Contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/test_scanner_contract.py`

- [ ] **Step 1: Write the failing contract test**

```python
import inspect
from dataclasses import fields

from magic_scanner.scan import ScanResult, scan_pair


def test_pinned_scanner_exposes_track1_contract():
    assert {f.name for f in fields(ScanResult)} >= {"entry", "levels", "authorization"}
    signature = inspect.signature(scan_pair)
    assert signature.parameters["execution_mode"].default == "research_confirmation"
    assert "allowed_side" in signature.parameters
```

- [ ] **Step 2: Run it and confirm RED against the stale pin**

Run: `uv run pytest tests/test_scanner_contract.py -q`
Expected: FAIL importing `magic_scanner.authorization` while `pyproject.toml` remains pinned to `4ccd1a9`.

- [ ] **Step 3: Pin the reviewed scanner commit**

After scanner-plan Task 7 is reviewed and committed, require a clean scanner worktree and replace
only the 40-character source revision:

```bash
test -z "$(git -C ../trading-scanner status --porcelain)"
SCANNER_SHA="$(git -C ../trading-scanner rev-parse HEAD)"
test "${#SCANNER_SHA}" -eq 40
perl -0pi -e 's#(magic-scanner = \{ git = "https://github.com/degencodebeast/trading-scanner", rev = ")[0-9a-f]{40}(" \})#$1'"$SCANNER_SHA"'$2#' pyproject.toml
uv lock --upgrade-package magic-scanner
```

Run `rg -n "magic-scanner =" pyproject.toml` and verify the printed revision equals
`git -C ../trading-scanner rev-parse HEAD`. Do not use a branch or `HEAD` in the file.

- [ ] **Step 4: Run the contract and MIDAS baseline**

Run: `uv run pytest tests/test_scanner_contract.py tests/test_scanner_gateway.py tests/test_setup_view.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/test_scanner_contract.py
git commit -m "build: pin aggressive scanner contract"
```

### Task 2: Define Spot-Only Domain Contracts

**Files:**
- Create: `src/magic_agent/spot_models.py`
- Create: `tests/test_spot_models.py`

- [ ] **Step 1: Write the failing model tests**

```python
from decimal import Decimal

import pytest

from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent


def test_spot_intent_rejects_short_and_leverage():
    setup = AuthorizedSetup.example()
    with pytest.raises(ValueError, match="spot-long"):
        SpotIntent("i-1", setup, Decimal("1"), "sell", ActionPurpose.STRATEGY)


def test_authorized_setup_requires_campaign_dol():
    with pytest.raises(ValueError, match="campaign DOL"):
        AuthorizedSetup.example(campaign_dol=None)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_spot_models.py -q`
Expected: FAIL because `magic_agent.spot_models` does not exist.

- [ ] **Step 3: Implement immutable contracts**

```python
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum


class ActionPurpose(str, Enum):
    STRATEGY = "strategy"
    RISK_EXIT = "risk_exit"
    COMPLIANCE = "compliance"
    X402 = "x402"


@dataclass(frozen=True)
class AuthorizedSetup:
    setup_id: str
    identity_key: str
    symbol: str
    grade: str
    entry: Decimal
    structural_stop: Decimal
    stop_source: str
    stop_anchor: Decimal
    stop_anchor_bar: int
    campaign_dol: Decimal
    campaign_dol_source: str
    checklist_dol: bool
    scanner_commit: str
    observed_at: str

    def __post_init__(self) -> None:
        if self.campaign_dol is None:
            raise ValueError("campaign DOL is required")
        if not self.structural_stop < self.entry < self.campaign_dol:
            raise ValueError("spot-long geometry must be stop < entry < campaign DOL")

    @classmethod
    def example(cls, **changes: object) -> "AuthorizedSetup":
        base = cls(
            "setup-1", "zec-bsc", "ZEC/USDT", "B", Decimal("100"),
            Decimal("90"), "h12_pivot", Decimal("91"), 12,
            Decimal("120"), "prior_week", False, "scanner-sha", "2026-06-21T00:00:00Z",
        )
        return replace(base, **changes)


@dataclass(frozen=True)
class SpotIntent:
    intent_id: str
    setup: AuthorizedSetup
    quantity: Decimal
    side: str
    purpose: ActionPurpose

    def __post_init__(self) -> None:
        expected = "buy" if self.purpose is ActionPurpose.STRATEGY else "sell"
        if self.side != expected or self.quantity <= 0:
            raise ValueError("spot-long intent has invalid side or quantity")
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_spot_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/spot_models.py tests/test_spot_models.py
git commit -m "feat(agent): add spot-only domain contracts"
```

### Task 3: Build The Contract-First Gold Identity Registry

**Files:**
- Create: `src/magic_agent/identity_registry.py`
- Create: `data/track1_identities.json`
- Create: `tests/test_identity_registry.py`

- [ ] **Step 1: Write RED tests for case and address authority**

```python
from magic_agent.identity_registry import IdentityRegistry


def test_case_distinct_symbols_and_contract_lookup(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text('[{"competition_symbol":"USDf","cmc_id":1,"chain_id":56,"contract_address":"0x01","decimals":18,"onchain_symbol":"USDf","market_data_source":"gateio","market_data_symbol":"USDF_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]},{"competition_symbol":"USDF","cmc_id":2,"chain_id":56,"contract_address":"0x02","decimals":18,"onchain_symbol":"USDF","market_data_source":"gateio","market_data_symbol":"USDF2_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]}]')
    registry = IdentityRegistry.load(path)
    assert registry.by_symbol("USDf").contract_address == "0x01"
    assert registry.by_symbol("USDF").contract_address == "0x02"
    assert registry.by_contract("0x02").cmc_id == 2
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_identity_registry.py -q`
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement exact-key loading**

```python
from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class IdentityRecord:
    competition_symbol: str
    cmc_id: int
    chain_id: int
    contract_address: str
    decimals: int
    onchain_symbol: str
    market_data_source: str
    market_data_symbol: str
    coverage_status: str
    verification_status: str
    verified_at: str
    sources: tuple[str, ...]


class IdentityRegistry:
    def __init__(self, records: list[IdentityRecord]) -> None:
        self._symbols = {r.competition_symbol: r for r in records}
        self._contracts = {r.contract_address.lower(): r for r in records}
        self._identity_keys = {f"{r.competition_symbol.lower()}-bsc": r for r in records}
        if len(self._symbols) != len(records) or len(self._contracts) != len(records):
            raise ValueError("duplicate symbol or contract identity")

    @classmethod
    def load(cls, path: str | Path) -> "IdentityRegistry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([IdentityRecord(**{**row, "sources": tuple(row["sources"])}) for row in raw])

    def by_symbol(self, symbol: str) -> IdentityRecord:
        return self._symbols[symbol]

    def by_contract(self, address: str) -> IdentityRecord:
        return self._contracts[address.lower()]

    def by_contract_key(self, identity_key: str) -> IdentityRecord:
        return self._identity_keys[identity_key]

    def scannable(self) -> tuple[IdentityRecord, ...]:
        return tuple(r for r in self._symbols.values() if r.coverage_status == "scannable" and r.verification_status == "gold")
```

Create the initial file with case-preserved, contract-bound records. Only APE starts `gold`; the
other three remain `registry_resolved` and are excluded by `scannable()` until route/CMC-ID evidence
is added and reviewed:

```json
[
  {"competition_symbol":"APE","cmc_id":18876,"chain_id":56,"contract_address":"0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79","decimals":18,"onchain_symbol":"APE","market_data_source":"gateio","market_data_symbol":"APE_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc_bsc_platform","apechain_official_oft","pancakeswap_quote"]},
  {"competition_symbol":"ZEC","cmc_id":1437,"chain_id":56,"contract_address":"0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb","decimals":18,"onchain_symbol":"ZEC","market_data_source":"gateio","market_data_symbol":"ZEC_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]},
  {"competition_symbol":"DEXE","cmc_id":7326,"chain_id":56,"contract_address":"0x6E88056E8376Ae7709496Ba64d37fa2f8015ce3e","decimals":18,"onchain_symbol":"DEXE","market_data_source":"gateio","market_data_symbol":"DEXE_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]},
  {"competition_symbol":"TRX","cmc_id":1958,"chain_id":56,"contract_address":"0xCE7de646e7208a4Ef112cb6ed5038FA6cC6b12e3","decimals":18,"onchain_symbol":"TRX","market_data_source":"gateio","market_data_symbol":"TRX_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]}
]
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_identity_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/identity_registry.py data/track1_identities.json tests/test_identity_registry.py
git commit -m "feat(identity): add contract-first Track 1 registry"
```

### Task 4: Persist CMC Rank/Veto/Clamp Snapshots

**Files:**
- Create: `src/magic_agent/cmc_selector.py`
- Create: `tests/test_cmc_selector.py`

- [ ] **Step 1: Write rank-only and stale-data tests**

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from magic_agent.cmc_selector import CandidateSnapshot, select_candidates


def test_cmc_cannot_authorize_or_increase_size():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    rows = [CandidateSnapshot("zec-bsc", now, now + timedelta(minutes=15), Decimal("12"), Decimal("40"), False, (), Decimal("1"))]
    selected = select_candidates(rows, now=now)
    assert selected[0].identity_key == "zec-bsc"
    assert selected[0].macro_clamp <= Decimal("1")
    assert not hasattr(selected[0], "authorized")


def test_stale_snapshot_yields_no_new_candidates():
    now = datetime(2026, 6, 21, 1, tzinfo=timezone.utc)
    expired = CandidateSnapshot("zec-bsc", now, now - timedelta(seconds=1), Decimal("12"), Decimal("40"), False, (), Decimal("1"))
    assert select_candidates([expired], now=now) == ()
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_cmc_selector.py -q`
Expected: FAIL because `cmc_selector` does not exist.

- [ ] **Step 3: Implement the immutable selection surface**

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class CandidateSnapshot:
    identity_key: str
    observed_at: datetime
    expires_at: datetime
    momentum_7d: Decimal
    momentum_30d: Decimal
    vetoed: bool
    veto_reasons: tuple[str, ...]
    macro_clamp: Decimal

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.macro_clamp <= Decimal("1"):
            raise ValueError("macro clamp must only preserve or reduce size")


def select_candidates(rows: list[CandidateSnapshot], *, now: datetime) -> tuple[CandidateSnapshot, ...]:
    valid = [r for r in rows if r.expires_at >= now and not r.vetoed]
    return tuple(sorted(valid, key=lambda r: (r.momentum_30d, r.momentum_7d, r.identity_key), reverse=True))
```

Persist raw response hash, normalized fields, TTL, schema/config version, rank/veto/clamp, and x402 payment ID through `state_journal.py` in Task 7. No `TAKE` field exists.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_cmc_selector.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/cmc_selector.py tests/test_cmc_selector.py
git commit -m "feat(selection): add immutable CMC rank-veto snapshots"
```

### Task 5: Consume Scanner-Owned Authorization

**Files:**
- Modify: `src/magic_agent/scanner_gateway.py`
- Replace: `src/magic_agent/setup_view.py`
- Modify: `tests/test_scanner_gateway.py`
- Modify: `tests/test_setup_view.py`

- [ ] **Step 1: Write the canonical mapping tests**

```python
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from magic_agent.scanner_gateway import authorized_setup_from_scan


def _scan_result(state: str = "authorized", *, campaign_dol: bool = True):
    entry = SimpleNamespace(
        entry=97.0, qml_reclaim_time=pd.Timestamp("2026-06-21T00:00:00Z"),
    )
    levels = SimpleNamespace(
        stop=SimpleNamespace(level=89.5, source="h12_pivot", anchor=90.0, anchor_bar=8),
        campaign_dol=(SimpleNamespace(level=120.0, source="prior_week") if campaign_dol else None),
    )
    return SimpleNamespace(
        authorization=SimpleNamespace(
            state=state, authorized_direction="Long" if state == "authorized" else None,
        ),
        levels=levels, entry=entry, symbol="ZEC/USDT",
        result=SimpleNamespace(rating="B"),
        inputs=SimpleNamespace(draw_on_liquidity=False),
    )


def test_only_authorized_scan_maps_to_setup():
    result = _scan_result()
    setup = authorized_setup_from_scan(result, identity_key="zec-bsc", scanner_commit="abc")
    assert setup is not None
    assert setup.entry == Decimal("97.0")
    assert setup.structural_stop == Decimal("89.5")
    assert setup.campaign_dol == Decimal("120.0")


def test_monitor_only_and_unresolved_dol_create_no_setup():
    assert authorized_setup_from_scan(_scan_result("monitor_only"), identity_key="zec-bsc", scanner_commit="abc") is None
    assert authorized_setup_from_scan(_scan_result(campaign_dol=False), identity_key="zec-bsc", scanner_commit="abc") is None
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_scanner_gateway.py tests/test_setup_view.py -q`
Expected: FAIL because current `setup_view.py` fabricates a QML percentage stop.

- [ ] **Step 3: Replace fabrication with a strict mapper**

```python
from decimal import Decimal

from magic_agent.spot_models import AuthorizedSetup


def authorized_setup_from_scan(result, *, identity_key: str, scanner_commit: str) -> AuthorizedSetup | None:
    auth = result.authorization
    levels = result.levels
    entry = result.entry
    if auth is None or auth.state != "authorized" or auth.authorized_direction != "Long":
        return None
    if entry is None or entry.entry is None or levels is None or levels.stop is None or levels.campaign_dol is None:
        return None
    return AuthorizedSetup(
        setup_id=f"{identity_key}:{entry.qml_reclaim_time.isoformat()}",
        identity_key=identity_key,
        symbol=result.symbol,
        grade=result.result.rating,
        entry=Decimal(str(entry.entry)),
        structural_stop=Decimal(str(levels.stop.level)),
        stop_source=levels.stop.source,
        stop_anchor=Decimal(str(levels.stop.anchor)),
        stop_anchor_bar=levels.stop.anchor_bar,
        campaign_dol=Decimal(str(levels.campaign_dol.level)),
        campaign_dol_source=levels.campaign_dol.source,
        checklist_dol=result.inputs.draw_on_liquidity,
        scanner_commit=scanner_commit,
        observed_at=entry.qml_reclaim_time.isoformat(),
    )
```

`ScannerGateway.scan` calls `scan_pair(symbol, frames,
execution_mode="track1_aggressive", allowed_side="Long")` and returns this mapper's result. Delete
every fixed percentage stop/target path from `setup_view.py`.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_scanner_gateway.py tests/test_setup_view.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/scanner_gateway.py src/magic_agent/setup_view.py tests/test_scanner_gateway.py tests/test_setup_view.py
git commit -m "feat(agent): consume scanner-owned authorization and levels"
```

### Task 6: Implement Mandatory Action-Aware RiskPolicy

**Files:**
- Create: `src/magic_agent/risk_policy.py`
- Create: `tests/test_risk_policy.py`

- [ ] **Step 1: Write fail-safe and sizing tests**

```python
from decimal import Decimal

from magic_agent.risk_policy import PortfolioRiskState, QuantityCaps, RiskConfig, evaluate_risk
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


def test_missing_config_denies_entry_but_allows_reconciled_exit():
    state = PortfolioRiskState.example(reconciled_position=True)
    caps = QuantityCaps.unbounded()
    assert not evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.STRATEGY, caps, Decimal("1")).approved
    assert evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.RISK_EXIT, caps, Decimal("1")).approved


def test_wider_stop_reduces_quantity_and_caps_never_increase_it():
    state = PortfolioRiskState.example(equity_usd=Decimal("1000"))
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    narrow = evaluate_risk(AuthorizedSetup.example(structural_stop=Decimal("95")), state, config, ActionPurpose.STRATEGY, caps, Decimal("1"))
    wide = evaluate_risk(AuthorizedSetup.example(structural_stop=Decimal("80")), state, config, ActionPurpose.STRATEGY, caps, Decimal("1"))
    assert narrow.final_qty > wide.final_qty
    assert narrow.final_qty <= narrow.base_qty
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_risk_policy.py -q`
Expected: FAIL because `risk_policy` does not exist.

- [ ] **Step 3: Implement fixed-fractional min-of-caps sizing**

```python
from dataclasses import dataclass, replace
from decimal import Decimal

from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


@dataclass(frozen=True)
class RiskConfig:
    risk_fraction: Decimal
    max_open_risk: Decimal
    max_token_fraction: Decimal
    stable_reserve_fraction: Decimal
    daily_loss_fraction: Decimal

    @classmethod
    def defaults(cls) -> "RiskConfig":
        return cls(Decimal("0.0025"), Decimal("0.01"), Decimal("0.25"), Decimal("0.30"), Decimal("0.015"))


@dataclass(frozen=True)
class PortfolioRiskState:
    equity_usd: Decimal
    cash_usd: Decimal
    peak_equity_usd: Decimal
    daily_anchor_usd: Decimal
    open_stressed_loss_usd: Decimal
    reconciled_position: bool = False

    @classmethod
    def example(cls, **changes: object) -> "PortfolioRiskState":
        return replace(cls(Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("0")), **changes)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    denied_by: str | None
    reasons: tuple[str, ...]
    risk_budget_usd: Decimal
    base_qty: Decimal
    final_qty: Decimal


@dataclass(frozen=True)
class QuantityCaps:
    liquidity_qty: Decimal
    pool_share_qty: Decimal
    concentration_qty: Decimal
    gap_stress_qty: Decimal
    minimum_notional_usd: Decimal

    @classmethod
    def unbounded(cls) -> "QuantityCaps":
        maximum = Decimal("Infinity")
        return cls(maximum, maximum, maximum, maximum, Decimal("0"))


def apply_quantity_caps(*, base_qty: Decimal, entry: Decimal, caps: QuantityCaps) -> tuple[Decimal, str | None]:
    final = min(base_qty, caps.liquidity_qty, caps.pool_share_qty,
                caps.concentration_qty, caps.gap_stress_qty)
    if final * entry < caps.minimum_notional_usd:
        return (Decimal("0"), "below_minimum_notional")
    return (final, None)


def evaluate_risk(setup: AuthorizedSetup, state: PortfolioRiskState,
                  config: RiskConfig | None, purpose: ActionPurpose,
                  caps: QuantityCaps, size_clamp: Decimal) -> RiskDecision:
    if not Decimal("0") <= size_clamp <= Decimal("1"):
        raise ValueError("size clamp must only preserve or reduce quantity")
    if config is None:
        allowed_exit = purpose is ActionPurpose.RISK_EXIT and state.reconciled_position
        return RiskDecision(allowed_exit, None if allowed_exit else "missing_policy", ("fail_safe",), Decimal("0"), Decimal("0"), Decimal("0"))
    if purpose is ActionPurpose.RISK_EXIT:
        return RiskDecision(state.reconciled_position, None if state.reconciled_position else "unreconciled", (), Decimal("0"), Decimal("0"), Decimal("0"))
    loss_per_unit = setup.entry - setup.structural_stop
    if loss_per_unit <= 0:
        return RiskDecision(False, "geometry", ("nonpositive_loss",), Decimal("0"), Decimal("0"), Decimal("0"))
    budget = state.equity_usd * config.risk_fraction
    base = budget / loss_per_unit
    cash_cap = max(Decimal("0"), state.cash_usd - state.equity_usd * config.stable_reserve_fraction) / setup.entry
    token_cap = state.equity_usd * config.max_token_fraction / setup.entry
    capped, cap_reason = apply_quantity_caps(
        base_qty=min(base, cash_cap, token_cap), entry=setup.entry, caps=caps,
    )
    final = capped * size_clamp
    projected = state.open_stressed_loss_usd + final * loss_per_unit
    daily_loss = max(Decimal("0"), state.daily_anchor_usd - state.equity_usd)
    within_daily = daily_loss + projected <= state.daily_anchor_usd * config.daily_loss_fraction
    approved = final > 0 and projected <= state.equity_usd * config.max_open_risk and within_daily
    denied = cap_reason or (None if approved else "portfolio_or_daily_cap")
    return RiskDecision(approved, denied, (), budget, base, final if approved else Decimal("0"))


class RiskPolicy:
    def __init__(self, config: RiskConfig | None) -> None:
        self.config = config

    def evaluate(self, setup: AuthorizedSetup, state: PortfolioRiskState,
                 purpose: ActionPurpose, caps: QuantityCaps,
                 size_clamp: Decimal = Decimal("1")) -> RiskDecision:
        return evaluate_risk(setup, state, self.config, purpose, caps, size_clamp)
```

No cap or macro/AI clamp may increase `base_qty`.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_risk_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/risk_policy.py tests/test_risk_policy.py
git commit -m "feat(risk): add mandatory spot RiskPolicy"
```

### Task 7: Extract The Shared DecisionPipeline

**Files:**
- Create: `src/magic_agent/decision_pipeline.py`
- Create: `tests/test_decision_pipeline.py`
- Modify: `src/magic_agent/runner.py`

- [ ] **Step 1: Write parity-oriented decision tests**

```python
from decimal import Decimal

from magic_agent.decision_pipeline import DecisionInputs, DecisionPipeline
from magic_agent.risk_policy import RiskDecision
from magic_agent.spot_models import AuthorizedSetup


def _inputs(*, position_open: bool = False, stop_touched: bool = False,
            entries_halted: bool = False) -> DecisionInputs:
    setup = AuthorizedSetup.example()
    risk = RiskDecision(True, None, (), Decimal("2.5"), Decimal("0.25"), Decimal("0.25"))
    return DecisionInputs(
        setup=setup, risk=risk,
        position=object() if position_open else None,
        stop_touched=stop_touched, target_touched=False,
        entries_halted=entries_halted,
    )


def test_pipeline_creates_entry_only_from_scanner_authorization():
    pipeline = DecisionPipeline()
    inputs = _inputs()
    decision = pipeline.decide(inputs)
    assert decision.action == "enter"
    assert decision.intent is not None
    assert decision.intent.setup.campaign_dol == inputs.setup.campaign_dol


def test_entry_halt_does_not_block_stop_exit():
    pipeline = DecisionPipeline()
    decision = pipeline.decide(_inputs(position_open=True, stop_touched=True, entries_halted=True))
    assert decision.action == "risk_exit"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_decision_pipeline.py -q`
Expected: FAIL because no shared pipeline exists.

- [ ] **Step 3: Implement a pure action selector**

```python
from dataclasses import dataclass

from magic_agent.risk_policy import RiskDecision
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent


@dataclass(frozen=True)
class DecisionInputs:
    setup: AuthorizedSetup | None
    risk: RiskDecision | None
    position: object | None
    stop_touched: bool
    target_touched: bool
    entries_halted: bool


@dataclass(frozen=True)
class PipelineDecision:
    action: str
    intent: SpotIntent | None
    reason: str


class DecisionPipeline:
    def decide(self, data: DecisionInputs) -> PipelineDecision:
        if data.position is not None and (data.stop_touched or data.target_touched):
            return PipelineDecision("risk_exit", None, "stop" if data.stop_touched else "campaign_dol")
        if data.position is not None:
            return PipelineDecision("hold", None, "position_open")
        if data.entries_halted:
            return PipelineDecision("hold", None, "entries_halted")
        if data.setup is None:
            return PipelineDecision("hold", None, "no_scanner_authorization")
        if data.risk is None or not data.risk.approved or data.risk.final_qty <= 0:
            return PipelineDecision("hold", None, "risk_denied")
        intent = SpotIntent(
            f"intent:{data.setup.setup_id}", data.setup,
            data.risk.final_qty, "buy", ActionPurpose.STRATEGY,
        )
        return PipelineDecision("enter", intent, "authorized")
```

Move all pure entry and exit selection from `runner.on_candle` into this class. Runner performs I/O
only after receiving `PipelineDecision`.

- [ ] **Step 4: Run GREEN and existing decision tests**

Run: `uv run pytest tests/test_decision_pipeline.py tests/test_decision.py tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/decision_pipeline.py src/magic_agent/runner.py tests/test_decision_pipeline.py
git commit -m "refactor(agent): extract shared deterministic DecisionPipeline"
```

### Task 8: Add Atomic Runtime State And Execution Journals

**Files:**
- Create: `src/magic_agent/state_journal.py`
- Create: `src/magic_agent/execution_journal.py`
- Create: `tests/test_state_journal.py`
- Create: `tests/test_execution_journal.py`

- [ ] **Step 1: Write corruption and transition tests**

```python
import pytest

from magic_agent.execution_journal import ExecutionJournal, ExecutionState
from magic_agent.state_journal import IntegrityError, StateJournal


def test_corrupt_state_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"payload":{},"sha256":"wrong"}')
    with pytest.raises(IntegrityError):
        StateJournal(path).load()


def test_execution_transition_is_monotonic_and_idempotent(tmp_path):
    journal = ExecutionJournal(tmp_path / "exec.json")
    journal.create("i-1", "key-1", {"quote": "q"})
    journal.transition("i-1", ExecutionState.EXECUTING)
    journal.transition("i-1", ExecutionState.SUBMITTED, tx_hash="0xabc")
    journal.transition("i-1", ExecutionState.SUBMITTED, tx_hash="0xabc")
    assert journal.get("i-1").state is ExecutionState.SUBMITTED
    with pytest.raises(ValueError):
        journal.transition("i-1", ExecutionState.INTENT_PERSISTED)
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_state_journal.py tests/test_execution_journal.py -q`
Expected: FAIL because the journal modules do not exist.

- [ ] **Step 3: Implement atomic hash-wrapped JSON**

```python
import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path


class IntegrityError(RuntimeError):
    pass


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


class StateJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
        wrapper = {"payload": payload, "sha256": hashlib.sha256(body.encode()).hexdigest()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(wrapper, sort_keys=True, default=_json_default), encoding="utf-8")
        os.replace(temp, self.path)

    def load(self) -> dict:
        wrapper = json.loads(self.path.read_text(encoding="utf-8"))
        body = json.dumps(wrapper["payload"], sort_keys=True, separators=(",", ":"), default=_json_default)
        if hashlib.sha256(body.encode()).hexdigest() != wrapper["sha256"]:
            raise IntegrityError("state integrity check failed")
        return wrapper["payload"]
```

```python
# execution_journal.py
from dataclasses import asdict, dataclass, field
from enum import Enum

from magic_agent.state_journal import StateJournal


class ExecutionState(str, Enum):
    INTENT_PERSISTED = "INTENT_PERSISTED"
    EXECUTING = "EXECUTING"
    SUBMITTED = "SUBMITTED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    REVERTED = "REVERTED"
    BROADCAST_UNKNOWN = "BROADCAST_UNKNOWN"
    RECONCILED = "RECONCILED"


_NEXT = {
    ExecutionState.INTENT_PERSISTED: {ExecutionState.EXECUTING},
    ExecutionState.EXECUTING: {ExecutionState.SUBMITTED, ExecutionState.BROADCAST_UNKNOWN},
    ExecutionState.SUBMITTED: {ExecutionState.MINED, ExecutionState.REVERTED, ExecutionState.BROADCAST_UNKNOWN},
    ExecutionState.MINED: {ExecutionState.CONFIRMED, ExecutionState.REVERTED},
    ExecutionState.CONFIRMED: {ExecutionState.RECONCILED, ExecutionState.BROADCAST_UNKNOWN},
    ExecutionState.REVERTED: set(),
    ExecutionState.BROADCAST_UNKNOWN: {ExecutionState.MINED, ExecutionState.REVERTED, ExecutionState.RECONCILED},
    ExecutionState.RECONCILED: set(),
}


@dataclass
class ExecutionRecord:
    intent_id: str
    idempotency_key: str
    state: ExecutionState
    evidence: dict = field(default_factory=dict)


class ExecutionJournal:
    def __init__(self, path) -> None:
        self.store = StateJournal(path)
        self.records: dict[str, ExecutionRecord] = {}
        if self.store.path.exists():
            raw = self.store.load()
            self.records = {
                key: ExecutionRecord(
                    value["intent_id"], value["idempotency_key"],
                    ExecutionState(value["state"]), value["evidence"],
                )
                for key, value in raw.items()
            }

    def _save(self) -> None:
        self.store.save({key: {**asdict(record), "state": record.state.value}
                         for key, record in self.records.items()})

    def create(self, intent_id: str, idempotency_key: str, evidence: dict) -> None:
        if intent_id in self.records:
            raise ValueError("duplicate intent")
        self.records[intent_id] = ExecutionRecord(
            intent_id, idempotency_key, ExecutionState.INTENT_PERSISTED, dict(evidence),
        )
        self._save()

    def transition(self, intent_id: str, state: ExecutionState, **evidence) -> None:
        record = self.records[intent_id]
        if record.state is state:
            if evidence and any(record.evidence.get(key) != value for key, value in evidence.items()):
                raise ValueError("conflicting idempotent transition")
            return
        if state not in _NEXT[record.state]:
            raise ValueError(f"invalid transition {record.state.value}->{state.value}")
        record.state = state
        record.evidence.update(evidence)
        self._save()

    def get(self, intent_id: str) -> ExecutionRecord:
        return self.records[intent_id]
```

The evidence supplied to `create` contains intent, quote, policy, pre-balances, and pre-nonce.
Later transitions add transaction hash, receipt, post-balances, actual fill, and errors.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_state_journal.py tests/test_execution_journal.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/state_journal.py src/magic_agent/execution_journal.py tests/test_state_journal.py tests/test_execution_journal.py
git commit -m "feat(state): add atomic runtime and execution journals"
```

### Task 9: Build A Strict TWAK Boundary And Executability Probe

**Files:**
- Create: `src/magic_agent/twak.py`
- Create: `src/magic_agent/executability.py`
- Create: `tests/test_twak.py`
- Create: `tests/test_executability.py`

- [ ] **Step 1: Capture credentialed canary schemas outside source control**

Run manually with the version pinned in the runbook:

```bash
twak --version
twak swap 1 USDC 0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79 --chain bsc --quote-only --json
twak wallet balance --json
twak x402 quote 'https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?id=1' --json
```

Expected: exit zero and JSON. Save redacted fixtures as `tests/fixtures/twak_quote.json`, `twak_balance.json`, and `twak_x402_quote.json`; never store credentials, passwords, addresses not intended for test publication, or signatures.

- [ ] **Step 2: Write strict parsing tests from the captured fixtures**

```python
from dataclasses import dataclass
from decimal import Decimal

from magic_agent.executability import validate_round_trip
from magic_agent.twak import TwakError, TwakRunner


@dataclass
class _Process:
    returncode: int
    stdout: str


def _run(returncode: int, stdout: str):
    def invoke(command, **kwargs):
        return _Process(returncode, stdout)
    return invoke


def test_nonzero_or_malformed_output_never_succeeds():
    runner = TwakRunner(run=_run(1, '{"success":true}'))
    try:
        runner.json(["wallet", "balance", "--json"])
        assert False, "nonzero exit must raise"
    except TwakError:
        pass


def test_password_is_redacted():
    runner = TwakRunner(run=_run(0, '{"success":true,"data":{}}'))
    runner.json(["wallet", "balance", "--password", "secret", "--json"])
    assert "secret" not in runner.last_redacted_command


def test_missing_impact_or_sell_route_fails_closed():
    buy = {"output_qty": "10", "provider": "rango", "minimum_output": "9.8",
           "slippage_bps": "20", "expires_at": "2026-06-21T00:01:00Z"}
    result = validate_round_trip(buy, None, now="2026-06-21T00:00:00Z")
    assert result.approved is False
    assert "missing_sell_quote" in result.reasons
```

- [ ] **Step 3: Implement strict subprocess execution**

```python
import json
import subprocess
from collections.abc import Callable


class TwakError(RuntimeError):
    pass


class TwakRunner:
    def __init__(self, run: Callable = subprocess.run) -> None:
        self._run = run
        self.last_redacted_command = ""

    def json(self, args: list[str], *, timeout: float = 60) -> dict:
        command = ["twak", *args]
        redacted = command.copy()
        if "--password" in redacted:
            redacted[redacted.index("--password") + 1] = "[REDACTED]"
        self.last_redacted_command = " ".join(redacted)
        process = self._run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if process.returncode != 0:
            raise TwakError(f"twak exit={process.returncode}")
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise TwakError("twak returned malformed JSON") from exc
        if payload.get("success") is False or payload.get("error"):
            raise TwakError("twak reported failure")
        return payload
```

```python
# executability.py
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from magic_agent.risk_policy import QuantityCaps


@dataclass(frozen=True)
class ExecutabilityDecision:
    approved: bool
    reasons: tuple[str, ...]
    quantity_caps: QuantityCaps


def validate_round_trip(buy: dict | None, sell: dict | None, *, now: str) -> ExecutabilityDecision:
    reasons: list[str] = []
    if buy is None:
        reasons.append("missing_buy_quote")
    if sell is None:
        reasons.append("missing_sell_quote")
    required = {"output_qty", "provider", "minimum_output", "impact_bps", "slippage_bps", "expires_at"}
    for name, quote in (("buy", buy), ("sell", sell)):
        if quote is None:
            continue
        missing = required - set(quote)
        if missing:
            reasons.append(f"{name}_missing_{'_'.join(sorted(missing))}")
        elif Decimal(str(quote["output_qty"])) <= 0 or Decimal(str(quote["minimum_output"])) <= 0:
            reasons.append(f"{name}_nonpositive_output")
        elif datetime.fromisoformat(str(quote["expires_at"]).replace("Z", "+00:00")) <= datetime.fromisoformat(now.replace("Z", "+00:00")):
            reasons.append(f"{name}_quote_expired")
    maximum = Decimal("Infinity")
    caps = QuantityCaps(maximum, maximum, maximum, maximum, Decimal("0"))
    return ExecutabilityDecision(not reasons, tuple(reasons), caps)
```

The coordinator obtains fresh buy and sell quote payloads from `TwakRunner` at intended size and
passes them here. Quote-calibrated liquidity, pool-share, gap-stress, and minimum-notional values
replace the unbounded caps before RiskPolicy. Missing fields deny; they never become zero impact.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_twak.py tests/test_executability.py -q`
Expected: PASS using fixtures only.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/twak.py src/magic_agent/executability.py tests/test_twak.py tests/test_executability.py tests/fixtures/twak_*.json
git commit -m "feat(exec): add strict TWAK boundary and quote probe"
```

### Task 10: Reconcile Receipts And Balances Before Booking Positions

**Files:**
- Create: `src/magic_agent/reconcile.py`
- Create: `src/magic_agent/execution_coordinator.py`
- Modify: `src/magic_agent/execution_journal.py`
- Create: `tests/test_reconcile.py`
- Create: `tests/test_execution_coordinator.py`

- [ ] **Step 1: Write unknown-broadcast and no-delta tests**

```python
from magic_agent.reconcile import ReconcileResult, reconcile_buy


def test_receipt_success_without_balance_delta_is_not_position():
    result = reconcile_buy(receipt={"status": "0x1", "blockNumber": "0x10"}, confirmations=2, required_confirmations=2, stable_before=100, stable_after=100, token_before=0, token_after=0)
    assert result.state == "CONFIRMED_NOT_RECONCILED"
    assert result.position_qty == 0


def test_timeout_after_possible_broadcast_is_unknown():
    result = ReconcileResult.broadcast_unknown("wallet nonce changed")
    assert result.state == "BROADCAST_UNKNOWN"
    assert result.blocks_new_exposure is True
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_reconcile.py -q`
Expected: FAIL because `reconcile` does not exist.

- [ ] **Step 3: Implement the invariant**

```python
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReconcileResult:
    state: str
    position_qty: Decimal
    blocks_new_exposure: bool
    reason: str

    @classmethod
    def broadcast_unknown(cls, reason: str) -> "ReconcileResult":
        return cls("BROADCAST_UNKNOWN", Decimal("0"), True, reason)


def reconcile_buy(*, receipt: dict, confirmations: int, required_confirmations: int, stable_before, stable_after, token_before, token_after) -> ReconcileResult:
    if int(str(receipt.get("status", "0")), 0) != 1:
        return ReconcileResult("REVERTED", Decimal("0"), False, "receipt reverted")
    if confirmations < required_confirmations:
        return ReconcileResult("MINED", Decimal("0"), True, "awaiting confirmations")
    stable_delta = Decimal(str(stable_after)) - Decimal(str(stable_before))
    token_delta = Decimal(str(token_after)) - Decimal(str(token_before))
    if stable_delta >= 0 or token_delta <= 0:
        return ReconcileResult("CONFIRMED_NOT_RECONCILED", Decimal("0"), True, "balance delta mismatch")
    return ReconcileResult("RECONCILED", token_delta, False, "receipt and balances agree")
```

```python
# execution_coordinator.py
from dataclasses import asdict

from magic_agent.execution_journal import ExecutionState
from magic_agent.twak import TwakError


class ExecutionCoordinator:
    def __init__(self, *, twak, rpc, balances, journal, positions, registry,
                 required_confirmations: int = 2) -> None:
        self.twak = twak
        self.rpc = rpc
        self.balances = balances
        self.journal = journal
        self.positions = positions
        self.registry = registry
        self.required_confirmations = required_confirmations

    def submit(self, intent, *, quote: dict, policy) -> str:
        return self.submit_buy(
            intent, quote=quote, policy=policy,
            pre_nonce=self.rpc.wallet_nonce(),
        )

    def submit_buy(self, intent, *, quote: dict, policy, pre_nonce: int) -> str:
        pre = self.balances.snapshot(intent.setup.identity_key)
        self.journal.create(intent.intent_id, intent.intent_id, {
            "intent": asdict(intent), "quote": quote, "policy": asdict(policy),
            "pre_balances": pre, "pre_nonce": pre_nonce,
        })
        self.journal.transition(intent.intent_id, ExecutionState.EXECUTING)
        try:
            contract = self.registry.by_contract_key(intent.setup.identity_key).contract_address
            payload = self.twak.json([
                "swap", str(intent.quantity), "USDC", contract,
                "--chain", "bsc", "--json",
            ])
            tx_hash = payload.get("data", {}).get("tx_hash") or payload.get("tx_hash")
            if not tx_hash:
                raise TwakError("swap response missing transaction hash")
        except Exception as exc:
            self.journal.transition(
                intent.intent_id, ExecutionState.BROADCAST_UNKNOWN, error=str(exc),
            )
            return "BROADCAST_UNKNOWN"
        self.journal.transition(intent.intent_id, ExecutionState.SUBMITTED, tx_hash=tx_hash)
        receipt = self.rpc.wait_receipt(tx_hash)
        self.journal.transition(intent.intent_id, ExecutionState.MINED, receipt=receipt)
        confirmations = self.rpc.confirmations(receipt)
        post = self.balances.snapshot(intent.setup.identity_key)
        result = reconcile_buy(
            receipt=receipt, confirmations=confirmations,
            required_confirmations=self.required_confirmations,
            stable_before=pre["stable"], stable_after=post["stable"],
            token_before=pre["token"], token_after=post["token"],
        )
        if result.state != "RECONCILED":
            return result.state
        self.journal.transition(intent.intent_id, ExecutionState.CONFIRMED)
        self.journal.transition(intent.intent_id, ExecutionState.RECONCILED,
                                post_balances=post, actual_qty=str(result.position_qty))
        self.positions.open_from_reconciliation(intent, result.position_qty)
        return "RECONCILED"
```

Add `IdentityRegistry.by_contract_key(identity_key)` as an exact lookup over a dedicated immutable
identity-key index; tickers are prohibited. A timeout, malformed output, or missing hash after
invocation becomes `BROADCAST_UNKNOWN`, never automatic retry. Restart scans unfinished
journal records and reconciles nonce, recent receipts, and balances before permitting exposure.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_reconcile.py tests/test_execution_journal.py tests/test_execution_coordinator.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/reconcile.py src/magic_agent/execution_coordinator.py src/magic_agent/execution_journal.py tests/test_reconcile.py tests/test_execution_coordinator.py
git commit -m "feat(exec): reconcile TWAK receipts and wallet balances"
```

### Task 11: Add Halt-Safe Position Management

**Files:**
- Create: `src/magic_agent/position_manager.py`
- Create: `tests/test_position_manager.py`

- [ ] **Step 1: Write exit-priority tests**

```python
from dataclasses import dataclass

from magic_agent.position_manager import PositionManager


@dataclass(frozen=True)
class _Position:
    stop: float = 90.0
    campaign_dol: float = 120.0


@dataclass(frozen=True)
class _Quote:
    approved: bool


def test_entry_halts_do_not_block_stop_exit():
    manager = PositionManager(sell_probe=lambda position: _Quote(True), risk_policy=object())
    position = _Position()
    action = manager.evaluate(position, low=89, high=101, entries_halted=True)
    assert action.reason == "stop"
    assert action.purpose.value == "risk_exit"


def test_failed_sell_keeps_position_managed():
    manager = PositionManager(sell_probe=lambda position: _Quote(False), risk_policy=object())
    position = _Position()
    result = manager.execute_exit(position, reason="stop")
    assert result.position_retained is True
    assert result.urgent_alert is True
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_position_manager.py -q`
Expected: FAIL because `position_manager` does not exist.

- [ ] **Step 3: Implement stop/DOL priority**

```python
from dataclasses import dataclass

from magic_agent.spot_models import ActionPurpose


@dataclass(frozen=True)
class ExitAction:
    reason: str
    purpose: ActionPurpose


@dataclass(frozen=True)
class ExitPreparation:
    position_retained: bool
    urgent_alert: bool
    quote: object | None = None


class PositionManager:
    def __init__(self, *, sell_probe, risk_policy) -> None:
        self.sell_probe = sell_probe
        self.risk_policy = risk_policy

    def evaluate(self, position, *, low, high, entries_halted: bool) -> ExitAction | None:
        if low <= position.stop:
            return ExitAction("stop", ActionPurpose.RISK_EXIT)
        if high >= position.campaign_dol:
            return ExitAction("campaign_dol", ActionPurpose.RISK_EXIT)
        return None

    def execute_exit(self, position, *, reason: str):
        quote = self.sell_probe(position)
        if not quote.approved:
            return ExitPreparation(True, True)
        return ExitPreparation(True, False, quote)
```

The coordinator sends approved exits through the same persisted intent, TWAK, receipt, and reconciliation path as buys. The position is removed only after a reconciled token decrease and stable increase.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_position_manager.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/position_manager.py tests/test_position_manager.py
git commit -m "feat(position): add halt-safe structural exit manager"
```

### Task 12: Add TWAK-Only x402 CMC Payments

**Files:**
- Create: `src/magic_agent/x402.py`
- Create: `tests/test_x402.py`

- [ ] **Step 1: Write allowlist and budget tests**

```python
class _Twak:
    def __init__(self) -> None:
        self.calls = []

    def json(self, args):
        self.calls.append(args)
        return {"success": True, "data": {}}


from decimal import Decimal

import pytest

from magic_agent.x402 import X402Budget, X402Client, X402Denied


def test_non_cmc_destination_and_over_budget_are_denied():
    client = X402Client(_Twak(), X402Budget(Decimal("0.01"), Decimal("0.10")))
    with pytest.raises(X402Denied):
        client.get("https://evil.example/data")
    with pytest.raises(X402Denied):
        client.get("https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?id=1", quoted_usd=Decimal("0.02"))
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_x402.py -q`
Expected: FAIL because `x402` does not exist.

- [ ] **Step 3: Implement the locked TWAK command**

```python
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse


class X402Denied(RuntimeError):
    pass


@dataclass(frozen=True)
class X402Budget:
    max_request_usd: Decimal
    max_daily_usd: Decimal


class X402Client:
    def __init__(self, twak, budget: X402Budget) -> None:
        self.twak = twak
        self.budget = budget
        self.spent_today = Decimal("0")

    def get(self, url: str, *, quoted_usd: Decimal = Decimal("0.01")) -> dict:
        if urlparse(url).hostname != "pro-api.coinmarketcap.com":
            raise X402Denied("destination not allowlisted")
        if quoted_usd > self.budget.max_request_usd or self.spent_today + quoted_usd > self.budget.max_daily_usd:
            raise X402Denied("x402 budget exceeded")
        atomic = int(quoted_usd * Decimal("1000000"))
        payload = self.twak.json([
            "x402", "request", url, "--max-payment", str(atomic),
            "--prefer-network", "base", "--prefer-method", "eip3009",
            "--prefer-asset", "USDC", "--yes", "--json",
        ])
        self.spent_today += quoted_usd
        return payload
```

Persist budget approval and idempotency before TWAK. Persist payment/result hashes after return. x402 failure halts new CMC-dependent entries but never position monitoring, reconciliation, or protective exits.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_x402.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/x402.py tests/test_x402.py
git commit -m "feat(x402): add budgeted TWAK CMC payments"
```

### Task 13: Separate Competition And ERC-8004 Registration

**Files:**
- Create: `src/magic_agent/registration.py`
- Modify: `src/magic_agent/identity.py`
- Create: `tests/test_registration.py`

- [ ] **Step 1: Write non-conflation tests**

```python
from magic_agent.registration import CompetitionRegistrar, Erc8004Registrar


class _Twak:
    def __init__(self) -> None:
        self.calls = []

    def json(self, args):
        self.calls.append(args)
        return {"success": True}


class _SdkAgent:
    def __init__(self) -> None:
        self.calls = []

    def register_agent(self, *, agent_uri):
        self.calls.append((agent_uri,))
        return {"agentId": 7, "transactionHash": "0xabc"}


def test_competition_registration_uses_twak_only():
    twak = _Twak()
    CompetitionRegistrar(twak).register()
    assert twak.calls[-1][:3] == ["compete", "register", "--json"]


def test_erc8004_uses_sdk_only():
    sdk_agent = _SdkAgent()
    result = Erc8004Registrar(sdk_agent).register("ipfs://agent")
    assert result.agent_id == 7
    assert sdk_agent.calls == [("ipfs://agent",)]
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_registration.py -q`
Expected: FAIL because `registration` does not exist.

- [ ] **Step 3: Implement separate adapters**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RegistrationResult:
    agent_id: int | None
    transaction_hash: str


class CompetitionRegistrar:
    def __init__(self, twak) -> None:
        self.twak = twak

    def register(self) -> dict:
        return self.twak.json(["compete", "register", "--json"])

    def status(self) -> dict:
        return self.twak.json(["compete", "status", "--json"])


class Erc8004Registrar:
    def __init__(self, sdk_agent) -> None:
        self.sdk_agent = sdk_agent

    def register(self, agent_uri: str) -> RegistrationResult:
        result = self.sdk_agent.register_agent(agent_uri=agent_uri)
        return RegistrationResult(int(result["agentId"]), str(result["transactionHash"]))
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_registration.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/registration.py src/magic_agent/identity.py tests/test_registration.py
git commit -m "feat(agent): separate Track 1 and ERC-8004 registration"
```

### Task 14: Wire The Spot Runtime, Paper Adapter, And Compliance Ledger

**Files:**
- Modify: `src/magic_agent/models.py`, `runner.py`, `live.py`, `cli.py`, `status.py`, `treasury.py`
- Create: `src/magic_agent/compliance.py`
- Create: `tests/test_spot_runtime.py`
- Modify: `tests/test_cli.py`, `tests/test_live.py`, `tests/test_status.py`

- [ ] **Step 1: Write the end-to-end paper and no-bypass tests**

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.cmc_selector import CandidateSnapshot
from magic_agent.decision_pipeline import DecisionInputs, DecisionPipeline
from magic_agent.risk_policy import PortfolioRiskState, QuantityCaps, RiskConfig, RiskPolicy
from magic_agent.runner import run_cycle
from magic_agent.spot_models import AuthorizedSetup


def _app(*, authorized: bool):
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    candidate = CandidateSnapshot(
        "zec-bsc", now, now + timedelta(minutes=15),
        Decimal("0.1"), Decimal("0.4"), False, (), Decimal("1"),
    )
    setup = AuthorizedSetup.example() if authorized else None
    executions = []
    alerts = []
    state = SimpleNamespace(
        blocks_new_exposure=False,
        risk_state=lambda: PortfolioRiskState.example(),
        as_dict=lambda: {},
    )
    app = SimpleNamespace(
        reconcile_unfinished=lambda: None,
        position_manager=SimpleNamespace(process_exits=lambda observed_at: None),
        state=state,
        cmc_source=SimpleNamespace(snapshot=lambda observed_at: [candidate]),
        scanner_gateway=SimpleNamespace(scan=lambda selected: setup),
        executability=SimpleNamespace(quote_round_trip=lambda selected: SimpleNamespace(
            approved=True, quantity_caps=QuantityCaps.unbounded(), quote={"id": "q"},
        )),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
        pipeline=DecisionPipeline(),
        inputs=lambda selected, risk: DecisionInputs(selected, risk, None, False, False, False),
        execution_coordinator=SimpleNamespace(submit=lambda intent, **evidence: executions.append(SimpleNamespace(state="RECONCILED", intent=intent))),
        compliance=SimpleNamespace(observe=lambda records, observed_at: alerts.append(SimpleNamespace(code="daily_qualification_at_risk")) if not records else None),
        execution_journal=SimpleNamespace(confirmed_records=lambda: executions),
        state_journal=SimpleNamespace(save=lambda payload: None),
    )
    return app, executions, alerts, now


def test_paper_spot_cycle_uses_shared_pipeline_and_reconciles():
    app, executions, alerts, now = _app(authorized=True)
    run_cycle(app, now)
    assert executions[-1].state == "RECONCILED"
    assert executions[-1].intent.setup.structural_stop == AuthorizedSetup.example().structural_stop


def test_compliance_ledger_cannot_authorize_trade():
    app, executions, alerts, now = _app(authorized=False)
    run_cycle(app, now)
    assert executions == []
    assert alerts[-1].code == "daily_qualification_at_risk"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_spot_runtime.py tests/test_cli.py tests/test_live.py -q`
Expected: FAIL while active CLI choices and models remain perp-shaped.

- [ ] **Step 3: Complete the spot migration**

Make `paper` the default and `twak` the only live executor choice. Remove active CLI leverage/short/Aster configuration. The runtime order is fixed:

```python
from magic_agent.cmc_selector import select_candidates
from magic_agent.spot_models import ActionPurpose


def run_cycle(app, now) -> None:
    app.reconcile_unfinished()
    app.position_manager.process_exits(now)
    if app.state.blocks_new_exposure:
        return
    snapshots = app.cmc_source.snapshot(now)
    for candidate in select_candidates(snapshots, now=now):
        setup = app.scanner_gateway.scan(candidate)
        if setup is None:
            continue
        quote = app.executability.quote_round_trip(setup)
        if not quote.approved:
            continue
        risk = app.risk_policy.evaluate(
            setup, app.state.risk_state(), ActionPurpose.STRATEGY,
            quote.quantity_caps, candidate.macro_clamp,
        )
        decision = app.pipeline.decide(app.inputs(setup, risk))
        if decision.intent is not None:
            app.execution_coordinator.submit(
                decision.intent, quote=quote.quote, policy=risk,
            )
            break
    app.compliance.observe(app.execution_journal.confirmed_records(), now)
    app.state_journal.save(app.state.as_dict())
```

`ComplianceLedger` counts confirmed eligible swaps by verified competition-day boundaries and emits alerts only. Its execution method does not exist. Retain Aster files only as inactive historical code until a later cleanup commit; no active import may reference them.

- [ ] **Step 4: Run the complete MIDAS suite**

Run: `uv run pytest -q`
Expected: PASS. Also run `rg -n "AsterRestExecutor|ENTER_SHORT|leverage" src/magic_agent/{cli.py,runner.py,live.py}` and expect no matches.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent tests
git commit -m "feat(agent): complete Track 1 spot runtime migration"
```

### Task 15: Operational And Safety Gate

**Files:**
- Modify: `README.md`, `.env.example`
- Create: `docs/track1-spot-runbook.md`

- [ ] **Step 1: Run deterministic gates**

Run: `uv run pytest -q`
Expected: PASS with no network.

Run: `rg -n "policy_config is not None|AsterRestExecutor|ENTER_SHORT" src/magic_agent`
Expected: no active runtime bypass/import matches.

- [ ] **Step 2: Run credentialed observations without funds**

Run: `twak auth status`, `twak compete status --json`, a quote-only buy and sell, and
`twak x402 quote 'https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?id=1' --json`.
Expected: valid redacted JSON artifacts matching Task 9 fixtures. These observations do not activate live trading.

- [ ] **Step 3: Document activation gates**

The runbook must require: scanner commit pin, gold identity, fresh CMC snapshot, mandatory policy, gas reserve, fresh two-sided quote, empty unfinished-execution set, competition registration, state backup, paper cycle, and explicit operator live activation. The first live order uses the 0.25% canary risk default and one concurrent position.

- [ ] **Step 4: Run Opus review**

Apply `general-review-protocol` to all runtime commits. Any path that books before reconciliation, bypasses RiskPolicy, signs outside TWAK, fabricates stop/DOL, or blocks a protective exit is release-blocking.

- [ ] **Step 5: Commit documentation fixes**

```bash
git add README.md .env.example docs/track1-spot-runbook.md
git commit -m "docs: add Track 1 spot activation runbook"
```

## Self-Review

- **Coverage:** identity REQ-010-015 -> Task 3; CMC REQ-020-024 -> Tasks 4/12; scanner REQ-030-039A -> Tasks 1/5; risk REQ-040-049A -> Tasks 6/7; executability REQ-050-056 -> Task 9; execution REQ-060-070 -> Tasks 8-10; positions REQ-080-084 -> Tasks 7/11/14; compliance REQ-090-095 -> Task 14; x402 REQ-110-115 -> Task 12; registration and migration -> Tasks 13-15.
- **Locked authority:** TWAK signs swaps and x402. `bnbagent-sdk` is ERC-8004 identity only. CMC never creates setup authority.
- **Accepted limits:** scanner supersession remains untouched; macro-distant H12 pivots reduce size or skip; compliance execution remains disabled; live defaults remain conservative until causal replay evidence exists.
- **Dependency:** Task 1 cannot substitute a scanner SHA until the scanner-contract plan is implemented and reviewed. All other tasks may be developed against the local editable scanner but the operational gate remains closed.
