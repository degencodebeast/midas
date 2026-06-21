# MIDAS Track 1 Spot Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate MIDAS from its perp-shaped Aster loop to a fail-closed, spot-long Track 1 runtime using CMC rank/veto context, scanner-owned Direct-QML authorization, mandatory RiskPolicy, and receipt-reconciled TWAK execution.

**Architecture:** Build one deterministic `LifecycleEvaluator -> DecisionInputs -> DecisionPipeline`
chain shared by live, paper, and replay. A fixed six-token monitoring watchlist is scanned on each
closed H1 while a four-hour discovery pass accounts for all 149 eligibility rows and may promote new
candidates; monitoring never bypasses gold identity, CMC, scanner, risk, or quote gates. Only the
evaluator produces decision inputs; side effects sit behind injected scanner, clock, quote, wallet,
persistence, and execution ports. TWAK is the only signer for swaps and x402; `bnbagent-sdk` is used
only for ERC-8004 identity.

**Tech Stack:** Python 3.11+, frozen dataclasses, `Decimal`, `pytest`, `uv`, `magic-scanner` (commit-pinned), TWAK CLI, BSC JSON-RPC, CMC x402.

---

## Authority And Execution Order

Read before every task: `docs/superpowers/specs/2026-06-21-track1-spot-agent-design.md`, `docs/superpowers/specs/2026-06-21-recon-findings.md`, workspace `docs/agent-trading-rules.md`, and the completed scanner plan. Implement in task order. Do not execute the superseded `2026-06-21-track1-spot-agent-implementation.md`.

After scanner Plan 1 lands and before this plan's Task 1 begins, run a fresh 149-row research filter
with the new scanner contract and current CMC observations. Save the immutable input/output hashes,
observation timestamp, scanner commit, all exclusions, and the six pinned names to
`data/research/latest_track1_filter.json`. The stale workspace `FILTERED_TOKENS.md` and its historical
`TAKE` labels are not runtime inputs. The fresh artifact is an auditable seed only: it does not hardcode
trade authorization, and the live candidate source continues H1 monitoring and four-hour discovery.

## File Structure

| File | Responsibility |
|---|---|
| `src/magic_agent/spot_models.py` | Spot-only domain contracts shared by runtime and replay |
| `src/magic_agent/eligibility.py` | Immutable 149-row competition ledger and exclusion accounting |
| `src/magic_agent/identity_registry.py` | Contract-first identities and execution eligibility |
| `src/magic_agent/cmc_source.py` | Normalize CMC ID/symbol observations for every eligibility row |
| `src/magic_agent/cmc_selector.py` | Immutable rank/veto/clamp snapshots; never setup authority |
| `src/magic_agent/watchlist.py` | Pinned H1 monitoring plus four-hour discovery promotion/demotion |
| `src/magic_agent/candidate_source.py` | Enumerate every eligibility row into monitorable candidates or reasoned exclusions |
| `src/magic_agent/scanner_gateway.py` | Convert scanner authorization into `AuthorizedSetup` |
| `src/magic_agent/risk_policy.py` | Mandatory sizing, portfolio caps, and action-aware fail-safe |
| `src/magic_agent/lifecycle.py` | Single-source lifecycle observation and exit evaluation |
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


def test_authorized_setup_preserves_scanner_lifecycle_provenance():
    setup = AuthorizedSetup.example()
    assert setup.grade == "B"
    assert setup.raw_grade == "B"
    assert setup.grade_promotion_reason is None
    assert setup.qml_state == "active"
    assert setup.qml_id == "qml-1"
    assert setup.governing_poi_id == "h12-poi-1"
    assert setup.governing_poi_timeframe == "12h"
    assert setup.bias_alignment == "aligned"
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
from typing import Literal


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
    # Scanner-owned effective grade used by RiskPolicy.
    grade: str
    # Frozen engine grade retained for audit/replay parity.
    raw_grade: str
    grade_promotion_reason: str | None
    entry: Decimal
    structural_stop: Decimal
    stop_source: str
    stop_anchor: Decimal
    stop_anchor_bar: int
    campaign_dol: Decimal
    campaign_dol_source: str
    checklist_dol: bool
    qml_id: str
    qml_state: Literal["active", "protected", "unknown"]
    governing_poi_id: str
    governing_poi_timeframe: str
    bias_alignment: Literal["aligned", "counter_bias", "no_bias"]
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
            "setup-1", "zec-bsc", "ZEC/USDT", "B", "B", None, Decimal("100"),
            Decimal("90"), "h12_pivot", Decimal("91"), 12,
            Decimal("120"), "prior_week", False,
            "qml-1", "active", "h12-poi-1", "12h", "aligned",
            "scanner-sha", "2026-06-21T00:00:00Z",
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

### Task 3: Build The 149-Row Eligibility Ledger And Contract-First Identity Registry

**Files:**
- Create: `src/magic_agent/eligibility.py`
- Create: `src/magic_agent/identity_registry.py`
- Create: `scripts/build_track1_eligibility.py`
- Create: `data/track1_eligibility.json`
- Create: `data/track1_identities.json`
- Create: `tests/test_eligibility.py`
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


def test_monitoring_and_execution_identity_are_separate(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text('[{"competition_symbol":"APE","cmc_id":18876,"chain_id":56,"contract_address":"0x01","decimals":18,"onchain_symbol":"APE","market_data_source":"gateio","market_data_symbol":"APE_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]},{"competition_symbol":"ZEC","cmc_id":1437,"chain_id":56,"contract_address":"0x02","decimals":18,"onchain_symbol":"ZEC","market_data_source":"gateio","market_data_symbol":"ZEC_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["cmc"]}]')
    registry = IdentityRegistry.load(path)
    assert {row.competition_symbol for row in registry.monitorable()} == {"APE", "ZEC"}
    assert {row.competition_symbol for row in registry.execution_eligible()} == {"APE"}
```

Add the eligibility-ledger test in `tests/test_eligibility.py`:

```python
from magic_agent.eligibility import EligibilityLedger


def test_authoritative_ledger_preserves_all_149_rows_and_duplicate_slx():
    ledger = EligibilityLedger.load("data/track1_eligibility.json")
    assert len(ledger.records) == 149
    assert len({row.eligibility_id for row in ledger.records}) == 149
    assert [row.competition_symbol for row in ledger.records].count("SLX") == 2
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_eligibility.py tests/test_identity_registry.py -q`
Expected: FAIL because the eligibility and identity modules do not exist.

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

    def get_by_symbol(self, symbol: str) -> IdentityRecord | None:
        return self._symbols.get(symbol)

    def by_contract(self, address: str) -> IdentityRecord:
        return self._contracts[address.lower()]

    def by_contract_key(self, identity_key: str) -> IdentityRecord:
        return self._identity_keys[identity_key]

    def monitorable(self) -> tuple[IdentityRecord, ...]:
        return tuple(r for r in self._symbols.values() if r.coverage_status == "scannable")

    def execution_eligible(self) -> tuple[IdentityRecord, ...]:
        return tuple(r for r in self.monitorable() if r.verification_status == "gold")
```

Create `src/magic_agent/eligibility.py` with immutable, duplicate-safe row identity:

```python
from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class EligibilityRecord:
    eligibility_id: str
    competition_symbol: str


class EligibilityLedger:
    def __init__(self, records: tuple[EligibilityRecord, ...]) -> None:
        if len(records) != 149 or len({row.eligibility_id for row in records}) != 149:
            raise ValueError("Track 1 ledger must preserve 149 unique eligibility rows")
        self.records = records

    @classmethod
    def load(cls, path: str | Path) -> "EligibilityLedger":
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(tuple(EligibilityRecord(**row) for row in rows))
```

Create `scripts/build_track1_eligibility.py`. It copies the authority list into MIDAS once at build
time, preserving ordinal identity so the duplicate `SLX` rows do not collapse:

```python
import json
from pathlib import Path


source = Path(__file__).resolve().parents[2] / "TOKENS.MD"
text = source.read_text(encoding="utf-8")
symbols = [item.strip() for item in text.split("Eligible tokens:", 1)[1].split(" Trades outside", 1)[0].split(",")]
if len(symbols) != 149:
    raise SystemExit(f"expected 149 eligibility rows, got {len(symbols)}")
rows = [
    {"eligibility_id": f"track1-{index:03d}", "competition_symbol": symbol}
    for index, symbol in enumerate(symbols, start=1)
]
target = Path(__file__).resolve().parents[1] / "data" / "track1_eligibility.json"
target.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
```

Run: `uv run python scripts/build_track1_eligibility.py`
Expected: `data/track1_eligibility.json` contains 149 rows and two distinct `SLX` eligibility IDs.

Create the initial identity file with case-preserved, contract-bound records for the six pinned
monitoring names. APE starts `gold` from the three-source resolution already recorded; the other five
remain `registry_resolved` until exact CMC/on-chain/route evidence is reviewed. All six are monitorable,
but only `gold` records are execution eligible:

```json
[
  {"competition_symbol":"APE","cmc_id":18876,"chain_id":56,"contract_address":"0x8f86a15EC17cb3369d8b3E666dAdBC11daA82b79","decimals":18,"onchain_symbol":"APE","market_data_source":"gateio","market_data_symbol":"APE_USDT","coverage_status":"scannable","verification_status":"gold","verified_at":"2026-06-21T00:00:00Z","sources":["cmc_bsc_platform","apechain_official_oft","pancakeswap_quote"]},
  {"competition_symbol":"ZEC","cmc_id":1437,"chain_id":56,"contract_address":"0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb","decimals":18,"onchain_symbol":"ZEC","market_data_source":"gateio","market_data_symbol":"ZEC_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]},
  {"competition_symbol":"DEXE","cmc_id":7326,"chain_id":56,"contract_address":"0x6E88056E8376Ae7709496Ba64d37fa2f8015ce3e","decimals":18,"onchain_symbol":"DEXE","market_data_source":"gateio","market_data_symbol":"DEXE_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]},
  {"competition_symbol":"TRX","cmc_id":1958,"chain_id":56,"contract_address":"0xCE7de646e7208a4Ef112cb6ed5038FA6cC6b12e3","decimals":18,"onchain_symbol":"TRX","market_data_source":"gateio","market_data_symbol":"TRX_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]},
  {"competition_symbol":"LINK","cmc_id":1975,"chain_id":56,"contract_address":"0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD","decimals":18,"onchain_symbol":"LINK","market_data_source":"gateio","market_data_symbol":"LINK_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]},
  {"competition_symbol":"XRP","cmc_id":52,"chain_id":56,"contract_address":"0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE","decimals":18,"onchain_symbol":"XRP","market_data_source":"gateio","market_data_symbol":"XRP_USDT","coverage_status":"scannable","verification_status":"registry_resolved","verified_at":"2026-06-21T00:00:00Z","sources":["boomerang_cmc_registry"]}
]
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_eligibility.py tests/test_identity_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/eligibility.py src/magic_agent/identity_registry.py scripts/build_track1_eligibility.py data/track1_eligibility.json data/track1_identities.json tests/test_eligibility.py tests/test_identity_registry.py
git commit -m "feat(identity): add Track 1 eligibility and identity ledgers"
```

### Task 4: Persist CMC Rank/Veto/Clamp Snapshots

**Files:**
- Create: `src/magic_agent/cmc_source.py`
- Create: `src/magic_agent/cmc_selector.py`
- Create: `tests/test_cmc_source.py`
- Create: `tests/test_cmc_selector.py`

- [ ] **Step 1: Write rank-only and stale-data tests**

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from magic_agent.cmc_selector import CandidateSnapshot, select_candidates


def test_cmc_cannot_authorize_or_increase_size():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    rows = [CandidateSnapshot("zec-bsc", now, now + timedelta(minutes=15), Decimal("12"), Decimal("40"), Decimal("0.10"), Decimal("0.20"), False, (), Decimal("1"))]
    selected = select_candidates(rows, now=now)
    assert selected[0].identity_key == "zec-bsc"
    assert selected[0].macro_clamp <= Decimal("1")
    assert not hasattr(selected[0], "authorized")


def test_stale_snapshot_yields_no_new_candidates():
    now = datetime(2026, 6, 21, 1, tzinfo=timezone.utc)
    expired = CandidateSnapshot("zec-bsc", now, now - timedelta(seconds=1), Decimal("12"), Decimal("40"), Decimal("0.10"), Decimal("0.20"), False, (), Decimal("1"))
    assert select_candidates([expired], now=now) == ()


def test_counter_bias_momentum_is_a_risk_input_not_a_universe_exclusion():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    strong = CandidateSnapshot("eth-bsc", now, now + timedelta(minutes=15), Decimal("4"), Decimal("-8"), Decimal("0.20"), Decimal("0.80"), False, (), Decimal("1"))
    weak = CandidateSnapshot("ada-bsc", now, now + timedelta(minutes=15), Decimal("-1"), Decimal("20"), Decimal("0.80"), Decimal("0.20"), False, (), Decimal("1"))
    assert select_candidates([strong, weak], now=now) == (strong, weak)
    assert strong.counter_bias_momentum_qualified
    assert not weak.counter_bias_momentum_qualified
```

Add source-boundary tests in `tests/test_cmc_source.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.cmc_source import CmcCandidateSource, RawCmcQuote


def test_contract_bound_cmc_id_wins_over_same_symbol_results():
    ledger = SimpleNamespace(records=(SimpleNamespace(eligibility_id="track1-001", competition_symbol="APE"),))
    registry = SimpleNamespace(get_by_symbol=lambda symbol: SimpleNamespace(cmc_id=18876))
    client = SimpleNamespace(fetch=lambda **kwargs: (
        RawCmcQuote(18876, "APE", "2", "10"), RawCmcQuote(999999, "APE", "200", "500"),
    ))
    result = CmcCandidateSource(ledger, registry, client).snapshot(datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert result.snapshots["APE"].identity_key == "ape-bsc"
    assert result.snapshots["APE"].momentum_7d == Decimal("2")


def test_unresolved_ambiguous_symbol_is_excluded_not_guessed():
    ledger = SimpleNamespace(records=(SimpleNamespace(eligibility_id="track1-001", competition_symbol="B"),))
    registry = SimpleNamespace(get_by_symbol=lambda symbol: None)
    client = SimpleNamespace(fetch=lambda **kwargs: (
        RawCmcQuote(1, "B", "2", "10"), RawCmcQuote(2, "B", "3", "20"),
    ))
    result = CmcCandidateSource(ledger, registry, client).snapshot(datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert result.snapshots == {}
    assert result.exclusions[0].reason_code == "cmc_symbol_ambiguous"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_cmc_source.py tests/test_cmc_selector.py -q`
Expected: FAIL because `cmc_source` and `cmc_selector` do not exist.

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
    momentum_7d_rank_pct: Decimal
    momentum_30d_rank_pct: Decimal
    vetoed: bool
    veto_reasons: tuple[str, ...]
    macro_clamp: Decimal

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.macro_clamp <= Decimal("1"):
            raise ValueError("macro clamp must only preserve or reduce size")
        if not all(
            Decimal("0") <= rank <= Decimal("1")
            for rank in (self.momentum_7d_rank_pct, self.momentum_30d_rank_pct)
        ):
            raise ValueError("momentum rank percentiles must be between zero and one")

    @property
    def counter_bias_momentum_qualified(self) -> bool:
        return self.momentum_7d > 0 and self.momentum_7d_rank_pct <= Decimal("0.25")


def select_candidates(rows: list[CandidateSnapshot], *, now: datetime) -> tuple[CandidateSnapshot, ...]:
    valid = [r for r in rows if r.expires_at >= now and not r.vetoed]
    return tuple(sorted(valid, key=lambda r: (r.momentum_7d_rank_pct, r.momentum_30d_rank_pct, r.identity_key)))
```

Create `src/magic_agent/cmc_source.py` over a normalized client port. Task 12's x402 implementation
supplies the live client; tests use deterministic fakes:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from magic_agent.cmc_selector import CandidateSnapshot


NON_DIRECTIONAL = {
    "USDT", "USDC", "DAI", "USD1", "USDE", "USDD", "TUSD", "FDUSD",
    "FRAX", "FRXUSD", "USDF", "USDF", "LISUSD", "DUSD", "XUSD", "EURI",
    "BILL", "STABLE", "XAUT", "XAUM",
}


@dataclass(frozen=True)
class RawCmcQuote:
    cmc_id: int
    symbol: str
    momentum_7d: str
    momentum_30d: str


@dataclass(frozen=True)
class CmcExclusion:
    eligibility_id: str
    symbol: str
    reason_code: str


@dataclass(frozen=True)
class CmcBatch:
    snapshots: dict[str, CandidateSnapshot]
    exclusions: tuple[CmcExclusion, ...]


class CmcCandidateSource:
    def __init__(self, ledger, registry, client) -> None:
        self.ledger, self.registry, self.client = ledger, registry, client

    def snapshot(self, now: datetime) -> CmcBatch:
        symbols = tuple(dict.fromkeys(row.competition_symbol for row in self.ledger.records))
        quotes = self.client.fetch(symbols=symbols, observed_at=now)
        by_symbol: dict[str, list[RawCmcQuote]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote.symbol, []).append(quote)
        chosen, exclusions = [], []
        for row in self.ledger.records:
            if row.competition_symbol.upper() in NON_DIRECTIONAL:
                exclusions.append(CmcExclusion(row.eligibility_id, row.competition_symbol, "non_directional_asset"))
                continue
            identity = self.registry.get_by_symbol(row.competition_symbol)
            matches = by_symbol.get(row.competition_symbol, [])
            if identity is not None and identity.cmc_id is not None:
                matches = [quote for quote in matches if quote.cmc_id == identity.cmc_id]
            if len(matches) != 1:
                reason = "cmc_missing" if not matches else "cmc_symbol_ambiguous"
                exclusions.append(CmcExclusion(row.eligibility_id, row.competition_symbol, reason))
                continue
            chosen.append((row, identity, matches[0]))
        count = Decimal(len(chosen))
        rank_7d = {
            item[0].eligibility_id: Decimal(rank) / count
            for rank, item in enumerate(
                sorted(chosen, key=lambda value: (-Decimal(value[2].momentum_7d), value[0].eligibility_id)),
                start=1,
            )
        }
        rank_30d = {
            item[0].eligibility_id: Decimal(rank) / count
            for rank, item in enumerate(
                sorted(chosen, key=lambda value: (-Decimal(value[2].momentum_30d), value[0].eligibility_id)),
                start=1,
            )
        }
        snapshots = {}
        for row, identity, quote in chosen:
            identity_key = f"{row.competition_symbol.lower()}-bsc" if identity else row.eligibility_id
            snapshots[row.competition_symbol] = CandidateSnapshot(
                identity_key, now, now + timedelta(minutes=15),
                Decimal(quote.momentum_7d), Decimal(quote.momentum_30d),
                rank_7d[row.eligibility_id], rank_30d[row.eligibility_id],
                False, (), Decimal("1"),
            )
        return CmcBatch(snapshots, tuple(exclusions))
```

Persist raw response hash, normalized fields, TTL, schema/config version, rank/veto/clamp, and x402 payment ID through `state_journal.py` in Task 7. No `TAKE` field exists.
Thirty-day momentum is persisted as context and may participate in general falling-knife vetoes, but
it is not an automatic counter-bias exclusion. A counter-bias long is risk-eligible only when its
seven-day return is positive and its cross-sectional seven-day rank is in the top quartile.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_cmc_source.py tests/test_cmc_selector.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/cmc_source.py src/magic_agent/cmc_selector.py tests/test_cmc_source.py tests/test_cmc_selector.py
git commit -m "feat(selection): add immutable CMC rank-veto snapshots"
```

### Task 4A: Build The Dynamic Watchlist And Accounted Discovery Source

**Files:**
- Create: `src/magic_agent/watchlist.py`
- Create: `src/magic_agent/candidate_source.py`
- Create: `tests/test_watchlist.py`
- Create: `tests/test_candidate_source.py`

- [ ] **Step 1: Write RED tests for pinned monitoring, discovery cadence, and 149-row accounting**

```python
from datetime import datetime, timedelta, timezone

from magic_agent.candidate_source import CandidateSource
from magic_agent.eligibility import EligibilityLedger
from magic_agent.identity_registry import IdentityRegistry
from magic_agent.watchlist import PINNED_SYMBOLS, WatchlistState


def test_requested_six_are_pinned_for_h1_monitoring():
    assert PINNED_SYMBOLS == ("ZEC", "DEXE", "TRX", "APE", "LINK", "XRP")


def test_discovery_is_due_every_four_hours_but_h1_monitoring_is_always_due():
    now = datetime(2026, 6, 21, 8, tzinfo=timezone.utc)
    state = WatchlistState.initial()
    assert state.monitoring_due(now)
    assert state.discovery_due(now)
    state = state.mark_discovery(now)
    assert not state.discovery_due(now + timedelta(hours=3, minutes=59))
    assert state.discovery_due(now + timedelta(hours=4))


def test_every_eligibility_row_becomes_a_candidate_or_reasoned_exclusion():
    now = datetime(2026, 6, 21, 8, tzinfo=timezone.utc)
    source = CandidateSource(
        EligibilityLedger.load("data/track1_eligibility.json"),
        IdentityRegistry.load("data/track1_identities.json"),
    )
    batch = source.enumerate(now=now, watchlist=WatchlistState.initial(), snapshots={})
    accounted = len(batch.monitoring) + len(batch.discovery) + len(batch.exclusions)
    assert accounted == 149
    assert {row.symbol for row in batch.monitoring} == set(PINNED_SYMBOLS)
    assert all(row.reason_code for row in batch.exclusions)
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_watchlist.py tests/test_candidate_source.py -q`
Expected: FAIL because `watchlist` and `candidate_source` do not exist.

- [ ] **Step 3: Implement the monitoring/discovery boundary**

```python
# src/magic_agent/watchlist.py
from dataclasses import dataclass, replace
from datetime import datetime, timedelta


PINNED_SYMBOLS = ("ZEC", "DEXE", "TRX", "APE", "LINK", "XRP")


@dataclass(frozen=True)
class WatchlistState:
    active_symbols: tuple[str, ...]
    last_discovery_at: datetime | None

    @classmethod
    def initial(cls) -> "WatchlistState":
        return cls(PINNED_SYMBOLS, None)

    def monitoring_due(self, now: datetime) -> bool:
        return True  # caller invokes only after a closed H1 bar

    def discovery_due(self, now: datetime) -> bool:
        return self.last_discovery_at is None or now - self.last_discovery_at >= timedelta(hours=4)

    def mark_discovery(self, now: datetime) -> "WatchlistState":
        return replace(self, last_discovery_at=now)

    def promote(self, symbol: str) -> "WatchlistState":
        return replace(self, active_symbols=tuple(dict.fromkeys((*self.active_symbols, symbol))))


class WatchlistManager:
    def __init__(self, state: WatchlistState) -> None:
        self.state = state

    def promote(self, symbol: str) -> None:
        self.state = self.state.promote(symbol)

    def mark_discovery(self, now: datetime) -> None:
        self.state = self.state.mark_discovery(now)
```

```python
# src/magic_agent/candidate_source.py
from dataclasses import dataclass
from datetime import datetime

from magic_agent.cmc_selector import CandidateSnapshot


@dataclass(frozen=True)
class MonitoringCandidate:
    eligibility_id: str
    symbol: str
    identity_key: str
    snapshot: CandidateSnapshot | None
    execution_eligible: bool
    pinned: bool


@dataclass(frozen=True)
class CandidateExclusion:
    eligibility_id: str
    symbol: str
    reason_code: str


@dataclass(frozen=True)
class CandidateBatch:
    monitoring: tuple[MonitoringCandidate, ...]
    discovery: tuple[MonitoringCandidate, ...]
    exclusions: tuple[CandidateExclusion, ...]


class CandidateSource:
    def __init__(self, ledger, registry) -> None:
        self.ledger = ledger
        self.registry = registry

    def enumerate(self, *, now: datetime, watchlist, snapshots: dict[str, CandidateSnapshot]) -> CandidateBatch:
        monitoring, discovery, exclusions = [], [], []
        discovery_due = watchlist.discovery_due(now)
        for row in self.ledger.records:
            snapshot = snapshots.get(row.competition_symbol)
            identity = self.registry.get_by_symbol(row.competition_symbol)
            if identity is None:
                reason = (
                    "identity_verification_required"
                    if discovery_due and snapshot is not None and snapshot.expires_at >= now and not snapshot.vetoed
                    else "identity_unresolved"
                )
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, reason))
                continue
            if identity.coverage_status != "scannable":
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "market_data_unscannable"))
                continue
            candidate = MonitoringCandidate(
                row.eligibility_id, row.competition_symbol,
                f"{row.competition_symbol.lower()}-bsc", snapshot,
                identity.verification_status == "gold", row.competition_symbol in watchlist.active_symbols,
            )
            if candidate.pinned:
                monitoring.append(candidate)
            elif not discovery_due:
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "discovery_not_due"))
            elif snapshot is None or snapshot.expires_at < now:
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "cmc_missing_or_stale"))
            elif snapshot.vetoed:
                exclusions.append(CandidateExclusion(row.eligibility_id, row.competition_symbol, "cmc_veto"))
            else:
                discovery.append(candidate)
        return CandidateBatch(tuple(monitoring), tuple(discovery), tuple(exclusions))
```

Pinned names remain monitored when CMC is stale or vetoed; that state blocks execution, not chart
observation. A fresh, non-vetoed CMC row without a resolved identity emits
`identity_verification_required`, which queues the same CMC-ID -> BSC contract -> on-chain metadata ->
market-data coverage review used by Task 3. Once that reviewed record is persisted, the next four-hour
pass can scan it; a discovery candidate is promoted only after the scanner reports a current governing-
POI campaign worth monitoring. Promotion is not authorization. Persist every exclusion with timestamp,
eligibility ID, stage, and reason code; never collapse the duplicate `SLX` rows.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_watchlist.py tests/test_candidate_source.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/watchlist.py src/magic_agent/candidate_source.py tests/test_watchlist.py tests/test_candidate_source.py
git commit -m "feat(selection): add dynamic Track 1 watchlist discovery"
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
import pytest

from magic_agent.scanner_gateway import authorized_setup_from_scan


def _scan_result(state: str = "authorized", *, campaign_dol: bool = True):
    entry = SimpleNamespace(
        entry=97.0, qml_reclaim_time=pd.Timestamp("2026-06-21T00:00:00Z"),
        qml_id="Long:10:97", qml_state="active",
    )
    levels = SimpleNamespace(
        stop=SimpleNamespace(level=89.5, source="h12_pivot", anchor=90.0, anchor_bar=8),
        campaign_dol=(SimpleNamespace(level=120.0, source="prior_week") if campaign_dol else None),
    )
    return SimpleNamespace(
        authorization=SimpleNamespace(
            state=state, authorized_direction="Long" if state == "authorized" else None,
            raw_grade="C", effective_grade="B-",
            grade_promotion_reason="track1_counter_bias_structural",
            governing_poi=SimpleNamespace(
                kind="Breaker", origin_bar=5, bottom=95.0, top=100.0,
            ),
        ),
        levels=levels, entry=entry, symbol="ZEC/USDT",
        result=SimpleNamespace(rating="C", regime="counter-bias"),
        inputs=SimpleNamespace(draw_on_liquidity=False),
    )


def test_only_authorized_scan_maps_to_setup():
    result = _scan_result()
    setup = authorized_setup_from_scan(result, identity_key="zec-bsc", scanner_commit="abc")
    assert setup is not None
    assert setup.entry == Decimal("97.0")
    assert setup.structural_stop == Decimal("89.5")
    assert setup.campaign_dol == Decimal("120.0")
    assert setup.grade == "B-"
    assert setup.raw_grade == "C"
    assert setup.grade_promotion_reason == "track1_counter_bias_structural"
    assert setup.qml_state == "active"
    assert setup.qml_id == "Long:10:97"
    assert setup.governing_poi_id == "12h:Breaker:5:95:100"
    assert setup.bias_alignment == "counter_bias"


def test_monitor_only_and_unresolved_dol_create_no_setup():
    assert authorized_setup_from_scan(_scan_result("monitor_only"), identity_key="zec-bsc", scanner_commit="abc") is None
    assert authorized_setup_from_scan(_scan_result(campaign_dol=False), identity_key="zec-bsc", scanner_commit="abc") is None


def test_unknown_scanner_regime_fails_closed():
    scan = _scan_result()
    scan.result.regime = "none"
    with pytest.raises(ValueError, match="unsupported scanner regime"):
        authorized_setup_from_scan(scan, identity_key="zec-bsc", scanner_commit="abc")
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_scanner_gateway.py tests/test_setup_view.py -q`
Expected: FAIL because the current gateway does not map scanner-owned authorization, structural
levels, QML lifecycle, and governing-POI provenance into `AuthorizedSetup`.

- [ ] **Step 3: Replace fabrication with a strict mapper**

```python
from decimal import Decimal

from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


_BIAS_ALIGNMENT = {
    "aligned": "aligned",
    "counter-bias": "counter_bias",
    "no-bias": "no_bias",
}


def normalize_scanner_regime(value: str) -> str:
    try:
        return _BIAS_ALIGNMENT[value]
    except KeyError as exc:
        raise ValueError(f"unsupported scanner regime: {value!r}") from exc


def authorized_setup_from_scan(result, *, identity_key: str, scanner_commit: str) -> AuthorizedSetup | None:
    auth = result.authorization
    levels = result.levels
    entry = result.entry
    if auth is None or auth.state != "authorized" or auth.authorized_direction != "Long":
        return None
    if (
        entry is None or entry.entry is None or entry.qml_id is None
        or auth.governing_poi is None or levels is None
        or levels.stop is None or levels.campaign_dol is None
        or not auth.raw_grade or not auth.effective_grade
    ):
        return None
    bias_alignment = normalize_scanner_regime(result.result.regime)
    return AuthorizedSetup(
        setup_id=f"{identity_key}:{entry.qml_reclaim_time.isoformat()}",
        identity_key=identity_key,
        symbol=result.symbol,
        grade=auth.effective_grade,
        raw_grade=auth.raw_grade,
        grade_promotion_reason=auth.grade_promotion_reason,
        entry=Decimal(str(entry.entry)),
        structural_stop=Decimal(str(levels.stop.level)),
        stop_source=levels.stop.source,
        stop_anchor=Decimal(str(levels.stop.anchor)),
        stop_anchor_bar=levels.stop.anchor_bar,
        campaign_dol=Decimal(str(levels.campaign_dol.level)),
        campaign_dol_source=levels.campaign_dol.source,
        checklist_dol=result.inputs.draw_on_liquidity,
        qml_id=entry.qml_id,
        qml_state=entry.qml_state,
        governing_poi_id=(
            f"12h:{auth.governing_poi.kind}:{auth.governing_poi.origin_bar}:"
            f"{auth.governing_poi.bottom:g}:{auth.governing_poi.top:g}"
        ),
        governing_poi_timeframe="12h",
        bias_alignment=bias_alignment,
        scanner_commit=scanner_commit,
        observed_at=entry.qml_reclaim_time.isoformat(),
    )
```

Define one gateway signature for every mode:

```python
class ScannerGateway:
    def scan(self, candidate, frames=None) -> AuthorizedSetup | None:
        identity = self.registry.by_contract_key(candidate.identity_key)
        selected_frames = frames if frames is not None else self.frame_source.closed_frames(candidate)
        result = scan_pair(
            identity.market_data_symbol, selected_frames,
            execution_mode="track1_aggressive", allowed_side="Long",
        )
        return authorized_setup_from_scan(
            result, identity_key=candidate.identity_key, scanner_commit=self.scanner_commit,
        )
```

Live/paper omit `frames` and use the injected closed-frame source; replay supplies its causal
`HistoricalFrameAdapter.as_of(...)` slice. The gateway returns the mapped setup directly in every
mode. It must
preserve the scanner's existing QML lifecycle/supersession disposition and governing-POI identity;
`AuthorizedSetup.grade` must always be the scanner authorization's `effective_grade`, while
`raw_grade` and `grade_promotion_reason` remain immutable audit provenance. Scanner regime values
must be normalized explicitly (`counter-bias` -> `counter_bias`); MIDAS must never infer regime from
`ChecklistInputs`, which has no `regime` field.
MIDAS must not recalculate or revive a rejected/superseded QML. Delete every fixed percentage
stop/target path from `setup_view.py`.

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

from magic_agent.risk_policy import (
    MarketRiskContext, PortfolioRiskState, QuantityCaps, RiskConfig,
    evaluate_risk, position_risk_reduction,
)
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


def test_missing_config_denies_entry_but_allows_reconciled_exit():
    state = PortfolioRiskState.example(reconciled_position=True)
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    assert not evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.STRATEGY, caps, market).approved
    assert evaluate_risk(AuthorizedSetup.example(), state, None, ActionPurpose.RISK_EXIT, caps, market).approved


def test_wider_stop_reduces_quantity_and_caps_never_increase_it():
    state = PortfolioRiskState.example(equity_usd=Decimal("1000"))
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    narrow = evaluate_risk(AuthorizedSetup.example(structural_stop=Decimal("95")), state, config, ActionPurpose.STRATEGY, caps, market)
    wide = evaluate_risk(AuthorizedSetup.example(structural_stop=Decimal("80")), state, config, ActionPurpose.STRATEGY, caps, market)
    assert narrow.final_qty > wide.final_qty
    assert narrow.final_qty <= narrow.base_qty


def test_grade_and_counter_bias_momentum_reduce_risk_without_tightening_stop():
    state = PortfolioRiskState.example(equity_usd=Decimal("1000"))
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    aligned_a = evaluate_risk(AuthorizedSetup.example(grade="A", bias_alignment="aligned"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.aligned())
    counter_a = evaluate_risk(AuthorizedSetup.example(grade="A", bias_alignment="counter_bias"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.counter_bias_qualified())
    counter_b = evaluate_risk(AuthorizedSetup.example(grade="B", bias_alignment="counter_bias"), state, config, ActionPurpose.STRATEGY, caps, MarketRiskContext.counter_bias_qualified())
    assert aligned_a.risk_fraction == Decimal("0.005")
    assert counter_a.risk_fraction == Decimal("0.0025")
    assert counter_b.risk_fraction == Decimal("0.00125")
    assert counter_a.final_qty == aligned_a.final_qty / 2


def test_counter_bias_requires_positive_top_quartile_seven_day_momentum():
    decision = evaluate_risk(
        AuthorizedSetup.example(bias_alignment="counter_bias"),
        PortfolioRiskState.example(), RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(),
        MarketRiskContext(Decimal("-0.01"), Decimal("0.10"), Decimal("1"), False),
    )
    assert not decision.approved
    assert decision.denied_by == "counter_bias_momentum"


def test_real_scanner_counter_bias_promotion_reaches_half_risk(monkeypatch):
    import pandas as pd

    import magic_scanner.authorization as scanner_auth
    from magic_agent.scanner_gateway import authorized_setup_from_scan
    from magic_scanner.authorization import authorize_track1_setup
    from magic_scanner.detectors.dol import DolTarget
    from magic_scanner.detectors.entry import EntrySetup
    from magic_scanner.detectors.poi import HtfPoiSourceSet, HtfPoiZone
    from magic_scanner.detectors.risk import StopPlan
    from magic_scanner.detectors.swings import DealingRange
    from magic_scanner.detectors.trade_levels import TradeLevels
    from magic_scanner.engine import evaluate_v2
    from magic_scanner.scan import ScanResult
    from magic_scanner.types import ChecklistInputs

    inputs = ChecklistInputs(
        h12_liquidity_sweep=False, orderflow="Bullish", htf_poi=True,
        draw_on_liquidity=False, htf_bias="Bearish",
        weekly_range="Discount", h12_range="Discount",
        trade_direction="Long", entry_type="Aggressive",
    )
    result = evaluate_v2(inputs)
    assert (result.rating, result.regime) == ("C", "counter-bias")

    entry = EntrySetup(
        97.0, "Aggressive", "none", False, False, False, None,
        97.0, pd.Timestamp("2026-06-21T00:00:00Z"), None, None,
        qml_id="Long:10:97", qml_state="active",
    )
    levels = TradeLevels(
        97.0, StopPlan(89.5, 90.0, "h12_pivot", 8),
        DolTarget(120.0, "prior_week"), (DolTarget(120.0, "prior_week"),),
    )
    zone = HtfPoiZone(
        "Breaker", "Bullish", 100.0, 95.0, 5, 7, True, "lux_breaker_block",
    )
    monkeypatch.setattr(
        scanner_auth, "build_htf_poi_source",
        lambda *args, **kwargs: HtfPoiSourceSet((zone,), (), (zone,)),
    )
    monkeypatch.setattr(
        scanner_auth, "dealing_range",
        lambda *args, **kwargs: DealingRange(140.0, 60.0, 100.0, "Bullish"),
    )
    authorization = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}), requested_side="Long",
        inputs=inputs, result=result, entry=entry, levels=levels, left=2, right=2,
    )
    scan = ScanResult(
        "ZEC/USDT", inputs, result, entry=entry, levels=levels,
        authorization=authorization,
    )

    setup = authorized_setup_from_scan(
        scan, identity_key="zec-bsc", scanner_commit="scanner-sha",
    )
    assert setup is not None
    assert (setup.raw_grade, setup.grade) == ("C", "B-")
    assert setup.grade_promotion_reason == "track1_counter_bias_structural"
    assert setup.bias_alignment == "counter_bias"

    decision = evaluate_risk(
        setup, PortfolioRiskState.example(equity_usd=Decimal("1000")),
        RiskConfig.defaults(), ActionPurpose.STRATEGY,
        QuantityCaps.unbounded(), MarketRiskContext.counter_bias_qualified(),
    )
    assert decision.approved
    assert decision.risk_fraction == Decimal("0.00125")


def test_drawdown_consecutive_stop_concurrency_and_stale_equity_gates():
    setup = AuthorizedSetup.example()
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    assert evaluate_risk(setup, PortfolioRiskState.example(equity_usd=Decimal("950")), config, ActionPurpose.STRATEGY, caps, market).denied_by == "drawdown_entry_halt"
    assert evaluate_risk(setup, PortfolioRiskState.example(consecutive_stops=3), config, ActionPurpose.STRATEGY, caps, market).denied_by == "consecutive_stop_halt"
    assert evaluate_risk(setup, PortfolioRiskState.example(open_strategy_positions=1), config, ActionPurpose.STRATEGY, caps, market).denied_by == "concurrency_cap"
    assert evaluate_risk(setup, PortfolioRiskState.example(equity_fresh=False), config, ActionPurpose.STRATEGY, caps, market).denied_by == "stale_equity"


def test_confirmed_thirty_percent_drawdown_is_an_explicit_hard_dq_guard():
    decision = evaluate_risk(
        AuthorizedSetup.example(), PortfolioRiskState.example(equity_usd=Decimal("700")),
        RiskConfig.defaults(), ActionPurpose.STRATEGY, QuantityCaps.unbounded(),
        MarketRiskContext.aligned(),
    )
    assert not decision.approved
    assert decision.denied_by == "hard_drawdown_dq"


def test_three_percent_drawdown_throttles_and_correlation_bucket_caps_all_longs():
    config = RiskConfig.defaults()
    caps = QuantityCaps.unbounded()
    market = MarketRiskContext.aligned()
    normal = evaluate_risk(AuthorizedSetup.example(), PortfolioRiskState.example(), config, ActionPurpose.STRATEGY, caps, market)
    throttled = evaluate_risk(AuthorizedSetup.example(), PortfolioRiskState.example(equity_usd=Decimal("970"), daily_anchor_usd=Decimal("970")), config, ActionPurpose.STRATEGY, caps, market)
    assert throttled.risk_fraction == normal.risk_fraction * Decimal("0.50")
    bucket_full = evaluate_risk(
        AuthorizedSetup.example(),
        PortfolioRiskState.example(correlation_bucket_stressed_loss_usd=Decimal("8")),
        config, ActionPurpose.STRATEGY, caps, market,
    )
    assert bucket_full.denied_by == "correlation_bucket_cap"


def test_position_risk_reduction_returns_quantity_needed_to_restore_budget():
    state = PortfolioRiskState.example(
        equity_usd=Decimal("1000"), daily_anchor_usd=Decimal("1000"),
        open_stressed_loss_usd=Decimal("15"),
    )
    position = type("Position", (), {
        "quantity": Decimal("2"), "stressed_loss_per_unit": Decimal("10"),
    })()
    decision = position_risk_reduction(position, state, RiskConfig.defaults())
    assert decision.reduction_qty == Decimal("0.5")
    assert decision.reasons == ("open_or_daily_risk_exceeds_budget",)
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_risk_policy.py -q`
Expected: FAIL because `risk_policy` does not exist.

- [ ] **Step 3: Implement fixed-fractional min-of-caps sizing**

```python
from dataclasses import dataclass, fields, replace
from decimal import Decimal
import hashlib
import json

from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


@dataclass(frozen=True)
class RiskConfig:
    canary_risk_fraction: Decimal
    a_grade_risk_fraction: Decimal
    b_grade_risk_fraction: Decimal
    counter_bias_multiplier: Decimal
    drawdown_throttle_multiplier: Decimal
    max_open_risk: Decimal
    max_correlation_bucket_risk: Decimal
    max_concurrent_positions: int
    hard_max_concurrent_positions: int
    max_token_fraction: Decimal
    stable_reserve_fraction: Decimal
    daily_loss_fraction: Decimal
    consecutive_stop_halt: int
    drawdown_throttle: Decimal
    drawdown_entry_halt: Decimal
    drawdown_review: Decimal
    hard_drawdown_dq: Decimal

    def __post_init__(self) -> None:
        if self.max_concurrent_positions > self.hard_max_concurrent_positions or self.hard_max_concurrent_positions > 2:
            raise ValueError("concurrent strategy positions exceed the Track 1 hard maximum")
        if not (self.drawdown_throttle < self.drawdown_entry_halt < self.drawdown_review < self.hard_drawdown_dq):
            raise ValueError("drawdown thresholds must be strictly ordered")

    @classmethod
    def defaults(cls) -> "RiskConfig":
        return cls(
            Decimal("0.0025"), Decimal("0.005"), Decimal("0.0025"), Decimal("0.50"),
            Decimal("0.50"), Decimal("0.01"), Decimal("0.01"), 1, 2,
            Decimal("0.25"), Decimal("0.30"), Decimal("0.015"), 3,
            Decimal("0.03"), Decimal("0.05"), Decimal("0.08"), Decimal("0.30"),
        )

    def stable_hash(self) -> str:
        payload = {field.name: str(getattr(self, field.name)) for field in fields(self)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PortfolioRiskState:
    equity_usd: Decimal
    cash_usd: Decimal
    peak_equity_usd: Decimal
    daily_anchor_usd: Decimal
    open_stressed_loss_usd: Decimal
    correlation_bucket_stressed_loss_usd: Decimal = Decimal("0")
    open_strategy_positions: int = 0
    consecutive_stops: int = 0
    equity_fresh: bool = True
    reconciled_position: bool = False

    @classmethod
    def example(cls, **changes: object) -> "PortfolioRiskState":
        return replace(cls(Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("0")), **changes)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    denied_by: str | None
    reasons: tuple[str, ...]
    risk_fraction: Decimal
    risk_budget_usd: Decimal
    base_qty: Decimal
    final_qty: Decimal


@dataclass(frozen=True)
class RiskReductionDecision:
    reduction_qty: Decimal
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MarketRiskContext:
    momentum_7d: Decimal
    momentum_7d_rank_pct: Decimal
    macro_clamp: Decimal
    canary: bool

    @classmethod
    def aligned(cls) -> "MarketRiskContext":
        return cls(Decimal("0"), Decimal("1"), Decimal("1"), False)

    @classmethod
    def counter_bias_qualified(cls) -> "MarketRiskContext":
        return cls(Decimal("0.01"), Decimal("0.25"), Decimal("1"), False)


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
                  caps: QuantityCaps, market: MarketRiskContext) -> RiskDecision:
    zero = Decimal("0")

    def deny(code: str, *reasons: str) -> RiskDecision:
        return RiskDecision(False, code, tuple(reasons or (code,)), zero, zero, zero, zero)

    if not Decimal("0") <= market.macro_clamp <= Decimal("1"):
        raise ValueError("macro clamp must only preserve or reduce quantity")
    if config is None:
        allowed_exit = purpose is ActionPurpose.RISK_EXIT and state.reconciled_position
        return RiskDecision(allowed_exit, None if allowed_exit else "missing_policy", ("fail_safe",), zero, zero, zero, zero)
    if purpose is ActionPurpose.RISK_EXIT:
        return RiskDecision(state.reconciled_position, None if state.reconciled_position else "unreconciled", (), zero, zero, zero, zero)
    if not state.equity_fresh:
        return deny("stale_equity")
    drawdown = max(zero, (state.peak_equity_usd - state.equity_usd) / state.peak_equity_usd)
    if drawdown >= config.hard_drawdown_dq:
        return deny("hard_drawdown_dq")
    if drawdown >= config.drawdown_review:
        return deny("drawdown_emergency_review")
    if drawdown >= config.drawdown_entry_halt:
        return deny("drawdown_entry_halt")
    if state.consecutive_stops >= config.consecutive_stop_halt:
        return deny("consecutive_stop_halt")
    if state.open_strategy_positions >= config.max_concurrent_positions:
        return deny("concurrency_cap")
    if setup.grade.startswith("A"):
        risk_fraction = config.a_grade_risk_fraction
    elif setup.grade.startswith("B"):
        risk_fraction = config.b_grade_risk_fraction
    else:
        return deny("unsupported_grade")
    if market.canary:
        risk_fraction = min(risk_fraction, config.canary_risk_fraction)
    if setup.bias_alignment == "counter_bias":
        if market.momentum_7d <= 0 or market.momentum_7d_rank_pct > Decimal("0.25"):
            return deny("counter_bias_momentum", "counter_bias_requires_positive_top_quartile_7d")
        risk_fraction *= config.counter_bias_multiplier
    if drawdown >= config.drawdown_throttle:
        risk_fraction *= config.drawdown_throttle_multiplier
    loss_per_unit = setup.entry - setup.structural_stop
    if loss_per_unit <= 0:
        return deny("geometry", "nonpositive_loss")
    budget = state.equity_usd * risk_fraction
    base = budget / loss_per_unit
    cash_cap = max(Decimal("0"), state.cash_usd - state.equity_usd * config.stable_reserve_fraction) / setup.entry
    token_cap = state.equity_usd * config.max_token_fraction / setup.entry
    capped, cap_reason = apply_quantity_caps(
        base_qty=min(base, cash_cap, token_cap), entry=setup.entry, caps=caps,
    )
    final = capped * market.macro_clamp
    new_stressed_loss = final * loss_per_unit
    projected = state.open_stressed_loss_usd + new_stressed_loss
    projected_bucket = state.correlation_bucket_stressed_loss_usd + new_stressed_loss
    daily_loss = max(Decimal("0"), state.daily_anchor_usd - state.equity_usd)
    within_daily = daily_loss + projected <= state.daily_anchor_usd * config.daily_loss_fraction
    if cap_reason:
        return deny(cap_reason)
    if final <= 0:
        return deny("zero_safe_quantity")
    if projected > state.equity_usd * config.max_open_risk:
        return deny("open_risk_cap")
    if projected_bucket > state.equity_usd * config.max_correlation_bucket_risk:
        return deny("correlation_bucket_cap")
    if not within_daily:
        return deny("daily_loss_cap")
    return RiskDecision(True, None, (), risk_fraction, budget, base, final)


def position_risk_reduction(position, state: PortfolioRiskState,
                            config: RiskConfig | None) -> RiskReductionDecision:
    if config is None:
        return RiskReductionDecision(position.quantity, ("missing_policy_reduce_only",))
    actual_daily_loss = max(Decimal("0"), state.daily_anchor_usd - state.equity_usd)
    daily_room = max(
        Decimal("0"), state.daily_anchor_usd * config.daily_loss_fraction - actual_daily_loss,
    )
    open_risk_room = state.equity_usd * config.max_open_risk
    allowed = min(daily_room, open_risk_room)
    excess = max(Decimal("0"), state.open_stressed_loss_usd - allowed)
    if excess == 0:
        return RiskReductionDecision(Decimal("0"), ())
    if position.stressed_loss_per_unit <= 0:
        return RiskReductionDecision(position.quantity, ("invalid_position_stress_reduce_only",))
    quantity = min(position.quantity, excess / position.stressed_loss_per_unit)
    return RiskReductionDecision(quantity, ("open_or_daily_risk_exceeds_budget",))


class RiskPolicy:
    def __init__(self, config: RiskConfig | None) -> None:
        self.config = config

    def evaluate(self, setup: AuthorizedSetup, state: PortfolioRiskState,
                 purpose: ActionPurpose, caps: QuantityCaps,
                 market: MarketRiskContext) -> RiskDecision:
        return evaluate_risk(setup, state, self.config, purpose, caps, market)

    def reduction_for(self, position, state: PortfolioRiskState) -> RiskReductionDecision:
        return position_risk_reduction(position, state, self.config)
```

The confirmed Track 1 hard-DQ boundary is 30%; it is recorded explicitly even though the internal
5% entry halt and 8% emergency-review state engage much earlier. The 3% throttle multiplies new risk
by `0.50`. Counter-bias and drawdown multipliers compose, but no cap or macro/AI clamp may increase
`base_qty`. Position monitoring evaluates positions in
descending stressed-loss order and recomputes portfolio state after every reconciled reduction;
this avoids issuing overlapping reductions against the same excess risk.
The runtime portfolio-state adapter must populate equity freshness, reconciled strategy-position
count, consecutive structural-stop exits, total stressed loss, and the shared all-crypto-long
correlation-bucket stressed loss from the journal on every evaluation; defaults are test fixtures,
not permission to omit live state.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_risk_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/risk_policy.py tests/test_risk_policy.py
git commit -m "feat(risk): add mandatory spot RiskPolicy"
```

### Task 7: Extract The Shared LifecycleEvaluator And DecisionPipeline

**Files:**
- Create: `src/magic_agent/lifecycle.py`
- Create: `src/magic_agent/decision_pipeline.py`
- Create: `tests/test_lifecycle.py`
- Create: `tests/test_decision_pipeline.py`
- Modify: `src/magic_agent/runner.py`

- [ ] **Step 1: Write parity-oriented lifecycle and decision tests**

```python
from decimal import Decimal

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.lifecycle import LifecycleEvaluator, LifecycleObservation, PositionView
from magic_agent.risk_policy import RiskDecision
from magic_agent.spot_models import AuthorizedSetup


def _risk(*, approved: bool = True, denied_by: str | None = None) -> RiskDecision:
    return RiskDecision(
        approved, denied_by, ((denied_by,) if denied_by else ()),
        Decimal("0.0025"), Decimal("2.5"), Decimal("0.25"),
        Decimal("0.25") if approved else Decimal("0"),
    )


def _observation(*, position: PositionView | None = None, low: Decimal = Decimal("96"),
                 high: Decimal = Decimal("101"), opposing_htf: bool = False,
                 reduction: Decimal = Decimal("0"), entries_halted: bool = False,
                 risk: RiskDecision | None = None) -> LifecycleObservation:
    return LifecycleObservation(
        setup=AuthorizedSetup.example(), risk=risk or _risk(), position=position,
        low=low, high=high, opposing_htf_invalidated=opposing_htf,
        risk_reduction_qty=reduction, entries_halted=entries_halted,
    )


def test_evaluator_is_the_single_source_of_decision_inputs_and_prioritizes_stop():
    position = PositionView("p-1", Decimal("2"), Decimal("90"), Decimal("120"))
    inputs = LifecycleEvaluator().evaluate(
        _observation(position=position, low=Decimal("89"), high=Decimal("121"),
                     opposing_htf=True, reduction=Decimal("1"), entries_halted=True),
    )
    assert inputs.source == "lifecycle_evaluator_v1"
    assert inputs.exit_reason == "stop"
    assert inputs.exit_quantity == Decimal("2")
    assert DecisionPipeline().decide(inputs).action == "risk_exit"


def test_evaluator_supports_campaign_opposing_htf_and_risk_reduction_exits():
    position = PositionView("p-1", Decimal("2"), Decimal("90"), Decimal("120"))
    evaluator = LifecycleEvaluator()
    assert evaluator.evaluate(_observation(position=position, high=Decimal("120"))).exit_reason == "campaign_dol"
    assert evaluator.evaluate(_observation(position=position, opposing_htf=True)).exit_reason == "opposing_htf"
    reduced = evaluator.evaluate(_observation(position=position, reduction=Decimal("0.5")))
    assert reduced.exit_reason == "risk_reduction"
    assert reduced.exit_quantity == Decimal("0.5")


def test_pipeline_preserves_specific_risk_denial_codes():
    inputs = LifecycleEvaluator().evaluate(_observation(risk=_risk(approved=False, denied_by="daily_loss_cap")))
    decision = DecisionPipeline().decide(inputs)
    assert decision.action == "hold"
    assert decision.reason_codes == ("risk_denied", "daily_loss_cap")
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_lifecycle.py tests/test_decision_pipeline.py -q`
Expected: FAIL because no shared lifecycle evaluator or decision pipeline exists.

- [ ] **Step 3: Implement the single-source lifecycle evaluator and pure selector**

```python
# lifecycle.py
from dataclasses import dataclass
from decimal import Decimal

from magic_agent.risk_policy import RiskDecision
from magic_agent.spot_models import AuthorizedSetup


@dataclass(frozen=True)
class PositionView:
    position_id: str
    quantity: Decimal
    stop: Decimal
    campaign_dol: Decimal


@dataclass(frozen=True)
class LifecycleObservation:
    setup: AuthorizedSetup | None
    risk: RiskDecision | None
    position: PositionView | None
    low: Decimal | None
    high: Decimal | None
    opposing_htf_invalidated: bool
    risk_reduction_qty: Decimal
    entries_halted: bool


@dataclass(frozen=True)
class DecisionInputs:
    setup: AuthorizedSetup | None
    risk: RiskDecision | None
    position: PositionView | None
    exit_reason: str | None
    exit_quantity: Decimal
    entries_halted: bool
    source: str


class LifecycleEvaluator:
    VERSION = "lifecycle_evaluator_v1"

    def evaluate(self, observation: LifecycleObservation) -> DecisionInputs:
        position = observation.position
        reason = None
        quantity = Decimal("0")
        if position is not None:
            if observation.low is not None and observation.low <= position.stop:
                reason, quantity = "stop", position.quantity
            elif observation.high is not None and observation.high >= position.campaign_dol:
                reason, quantity = "campaign_dol", position.quantity
            elif observation.opposing_htf_invalidated:
                reason, quantity = "opposing_htf", position.quantity
            elif observation.risk_reduction_qty > 0:
                reason = "risk_reduction"
                quantity = min(position.quantity, observation.risk_reduction_qty)
        return DecisionInputs(
            observation.setup, observation.risk, position, reason, quantity,
            observation.entries_halted, self.VERSION,
        )
```

```python
# decision_pipeline.py
from dataclasses import dataclass
from decimal import Decimal

from magic_agent.lifecycle import DecisionInputs, LifecycleEvaluator
from magic_agent.spot_models import ActionPurpose, SpotIntent


@dataclass(frozen=True)
class PipelineDecision:
    action: str
    intent: SpotIntent | None
    reason: str
    reason_codes: tuple[str, ...]
    exit_quantity: Decimal = Decimal("0")


class DecisionPipeline:
    def decide(self, data: DecisionInputs) -> PipelineDecision:
        if data.source != LifecycleEvaluator.VERSION:
            raise ValueError("DecisionInputs must come from LifecycleEvaluator")
        if data.position is not None and data.exit_reason is not None:
            return PipelineDecision(
                "risk_exit", None, data.exit_reason, (data.exit_reason,), data.exit_quantity,
            )
        if data.position is not None:
            return PipelineDecision("hold", None, "position_open", ("position_open",))
        if data.entries_halted:
            return PipelineDecision("hold", None, "entries_halted", ("entries_halted",))
        if data.setup is None:
            return PipelineDecision(
                "hold", None, "no_scanner_authorization", ("no_scanner_authorization",),
            )
        if data.risk is None or not data.risk.approved or data.risk.final_qty <= 0:
            detail = data.risk.denied_by if data.risk is not None else "risk_unavailable"
            reasons = data.risk.reasons if data.risk is not None else ()
            return PipelineDecision(
                "hold", None, "risk_denied",
                tuple(dict.fromkeys(("risk_denied", detail, *reasons))),
            )
        intent = SpotIntent(
            f"intent:{data.setup.setup_id}", data.setup,
            data.risk.final_qty, "buy", ActionPurpose.STRATEGY,
        )
        return PipelineDecision("enter", intent, "authorized", ("authorized",))
```

Only `LifecycleEvaluator.evaluate()` may produce `DecisionInputs`. Live, paper, and replay callers
construct `LifecycleObservation`, never touch/exit booleans or `DecisionInputs` directly. Move pure
entry and exit action selection from `runner.on_candle` into `DecisionPipeline`; runner performs I/O
only after receiving `PipelineDecision`.

- [ ] **Step 4: Run GREEN and existing decision tests**

Run: `uv run pytest tests/test_lifecycle.py tests/test_decision_pipeline.py tests/test_decision.py tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/lifecycle.py src/magic_agent/decision_pipeline.py src/magic_agent/runner.py tests/test_lifecycle.py tests/test_decision_pipeline.py
git commit -m "refactor(agent): share deterministic lifecycle and decision pipeline"
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

from magic_agent.executability import prepare_exact_order, validate_round_trip
from magic_agent.risk_policy import MarketRiskContext, PortfolioRiskState, QuantityCaps, RiskConfig, RiskPolicy
from magic_agent.spot_models import AuthorizedSetup
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


def test_final_quote_is_refreshed_at_risk_sized_quantity():
    requested = []

    def quote_provider(setup, quantity):
        requested.append(quantity)
        return type("Quote", (), {
            "approved": True, "reasons": (), "quantity_caps": QuantityCaps.unbounded(),
            "quote": {"quantity": None if quantity is None else str(quantity)},
        })()

    prepared = prepare_exact_order(
        setup=AuthorizedSetup.example(), market=MarketRiskContext.aligned(),
        risk_state=PortfolioRiskState.example(),
        risk_policy=RiskPolicy(RiskConfig.defaults()), quote_provider=quote_provider,
    )
    assert prepared.approved
    assert requested[0] is None  # capacity discovery only
    assert requested[-1] == prepared.risk.final_qty
    assert Decimal(prepared.quote["quantity"]) == prepared.risk.final_qty
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


@dataclass(frozen=True)
class PreparedOrder:
    approved: bool
    reasons: tuple[str, ...]
    risk: object | None
    quote: dict | None


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


def prepare_exact_order(*, setup, market, risk_state, risk_policy, quote_provider) -> PreparedOrder:
    probe = quote_provider(setup, None)
    if not probe.approved:
        return PreparedOrder(False, probe.reasons, None, None)
    risk = risk_policy.evaluate(
        setup, risk_state, ActionPurpose.STRATEGY, probe.quantity_caps, market,
    )
    if not risk.approved or risk.final_qty <= 0:
        return PreparedOrder(False, ("risk_denied", risk.denied_by or "unknown"), risk, None)
    for _ in range(2):
        exact = quote_provider(setup, risk.final_qty)
        if not exact.approved:
            return PreparedOrder(False, exact.reasons, risk, None)
        revised = risk_policy.evaluate(
            setup, risk_state, ActionPurpose.STRATEGY, exact.quantity_caps, market,
        )
        if not revised.approved or revised.final_qty <= 0:
            return PreparedOrder(False, ("risk_denied", revised.denied_by or "unknown"), revised, None)
        if revised.final_qty == risk.final_qty:
            return PreparedOrder(True, (), revised, exact.quote)
        risk = revised
    return PreparedOrder(False, ("quote_size_did_not_converge",), risk, None)


class ExecutabilityProbe:
    def __init__(self, quote_provider) -> None:
        self.quote_provider = quote_provider

    def prepare_order(self, *, setup, market, risk_state, risk_policy) -> PreparedOrder:
        return prepare_exact_order(
            setup=setup, market=market, risk_state=risk_state,
            risk_policy=risk_policy, quote_provider=self.quote_provider,
        )
```

Add `from magic_agent.spot_models import ActionPurpose` to `executability.py`. The first provider call
is a bounded capacity-discovery probe and cannot be submitted. RiskPolicy computes `final_qty`; the
provider then obtains a fresh buy/sell quote at that exact quantity. If exact-size caps reduce size,
one final exact-size refresh is allowed. Non-convergence, expiry, missing fields, or changed impact
denies the order. Only the unexpired quote whose quantity equals `risk.final_qty` may be submitted.

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

from decimal import Decimal
from types import SimpleNamespace

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.lifecycle import LifecycleEvaluator, LifecycleObservation, PositionView
from magic_agent.position_manager import PositionManager
from magic_agent.risk_policy import PortfolioRiskState


@dataclass(frozen=True)
class _Position:
    position_id: str = "p-1"
    quantity: Decimal = Decimal("2")
    stressed_loss_per_unit: Decimal = Decimal("10")
    stop: float = 90.0
    campaign_dol: float = 120.0


@dataclass(frozen=True)
class _Quote:
    approved: bool


def test_entry_halts_do_not_block_stop_exit():
    executed = []
    position = _Position()
    manager = PositionManager(
        positions=lambda: [position],
        observe=lambda p, now, reduction: LifecycleObservation(
            None, None, PositionView(p.position_id, p.quantity, Decimal("90"), Decimal("120")),
            Decimal("89"), Decimal("101"), False, reduction.reduction_qty, True,
        ),
        evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=SimpleNamespace(reduction_for=lambda p, state: SimpleNamespace(reduction_qty=Decimal("0"))),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda p, quantity: _Quote(True),
        execute=lambda p, decision, quote: executed.append((p, decision, quote)),
    )
    manager.process_exits("2026-06-21T00:00:00Z")
    assert executed[0][1].reason == "stop"
    assert executed[0][1].exit_quantity == Decimal("2")


def test_failed_sell_keeps_position_managed():
    alerts = []
    position = _Position()
    manager = PositionManager(
        positions=lambda: [position], observe=lambda p, now, reduction: LifecycleObservation(
            None, None, PositionView(p.position_id, p.quantity, Decimal("90"), Decimal("120")),
            Decimal("89"), Decimal("101"), False, reduction.reduction_qty, False,
        ), evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
        risk_policy=SimpleNamespace(reduction_for=lambda p, state: SimpleNamespace(reduction_qty=Decimal("0"))),
        risk_state=lambda: PortfolioRiskState.example(),
        sell_probe=lambda p, quantity: _Quote(False), execute=lambda *args: None,
        alert=lambda code, p: alerts.append(code),
    )
    manager.process_exits("2026-06-21T00:00:00Z")
    assert alerts == ["protective_exit_unquotable"]
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_position_manager.py -q`
Expected: FAIL because `position_manager` does not exist.

- [ ] **Step 3: Implement stop/DOL priority**

```python
class PositionManager:
    def __init__(self, *, positions, observe, evaluator, pipeline, risk_policy, risk_state,
                 sell_probe, execute,
                 alert=lambda code, position: None) -> None:
        self.positions = positions
        self.observe = observe
        self.evaluator = evaluator
        self.pipeline = pipeline
        self.risk_policy = risk_policy
        self.risk_state = risk_state
        self.sell_probe = sell_probe
        self.execute = execute
        self.alert = alert

    def process_exits(self, now) -> None:
        ordered = sorted(
            self.positions(), key=lambda p: p.quantity * p.stressed_loss_per_unit, reverse=True,
        )
        for position in ordered:
            reduction = self.risk_policy.reduction_for(position, self.risk_state())
            inputs = self.evaluator.evaluate(self.observe(position, now, reduction))
            decision = self.pipeline.decide(inputs)
            if decision.action != "risk_exit":
                continue
            quote = self.sell_probe(position, decision.exit_quantity)
            if not quote.approved:
                self.alert("protective_exit_unquotable", position)
                continue
            self.execute(position, decision, quote)
```

`observe` builds raw `LifecycleObservation` from the current closed bar, scanner HTF invalidation,
and the exact `RiskPolicy.reduction_for` result supplied by `PositionManager`. It must not build
`DecisionInputs`. The shared evaluator and pipeline
select every exit, including stop, campaign DOL, opposing H12/D1 invalidation, and partial risk
reduction. The coordinator sends approved exits through the same persisted intent, TWAK, receipt,
and reconciliation path as buys. The position is removed or reduced only after reconciled token and
stable balance deltas.

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
- Create: `src/magic_agent/cmc_x402.py`
- Create: `tests/test_x402.py`
- Create: `tests/test_cmc_x402.py`

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

Add the normalized CMC transport test in `tests/test_cmc_x402.py`:

```python
from magic_agent.cmc_x402 import CmcX402QuoteClient


def test_cmc_x402_normalizes_id_symbol_and_momentum_fields():
    payload = {"data": {"18876": {"id": 18876, "symbol": "APE", "quote": {"USD": {
        "percent_change_7d": 2.5, "percent_change_30d": -5.0,
    }}}}}
    transport = type("Transport", (), {"get": lambda self, url: payload})()
    rows = CmcX402QuoteClient(transport).fetch(symbols=("APE",), observed_at=None)
    assert rows[0].cmc_id == 18876
    assert rows[0].symbol == "APE"
    assert rows[0].momentum_7d == "2.5"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_x402.py -q`
Expected: FAIL because `x402` and `cmc_x402` do not exist.

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

Create `src/magic_agent/cmc_x402.py`. The current CMC x402 endpoint accepts comma-separated IDs or
symbols and exposes 7d/30d changes under the USD quote. Chunking at 50 is our URL/budget bound, not a
claim about CMC's maximum:

```python
from urllib.parse import urlencode

from magic_agent.cmc_source import RawCmcQuote


class CmcX402QuoteClient:
    def __init__(self, transport, *, chunk_size: int = 50) -> None:
        self.transport = transport
        self.chunk_size = chunk_size

    def fetch(self, *, symbols: tuple[str, ...], observed_at) -> tuple[RawCmcQuote, ...]:
        rows = []
        for start in range(0, len(symbols), self.chunk_size):
            chunk = symbols[start:start + self.chunk_size]
            query = urlencode({"symbol": ",".join(chunk), "convert": "USD"})
            payload = self.transport.get(
                f"https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?{query}",
            )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("CMC response missing data object")
            for key, item in data.items():
                quote = item.get("quote", {}).get("USD", {})
                required = (item.get("symbol"), quote.get("percent_change_7d"), quote.get("percent_change_30d"))
                if any(value is None for value in required):
                    raise ValueError(f"CMC quote {key} missing momentum fields")
                rows.append(RawCmcQuote(
                    int(item.get("id", key)), str(item["symbol"]),
                    str(quote["percent_change_7d"]), str(quote["percent_change_30d"]),
                ))
        return tuple(rows)
```

Persist budget approval and idempotency before TWAK. Persist payment/result hashes after return. x402 failure halts new CMC-dependent entries but never position monitoring, reconciliation, or protective exits.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_x402.py tests/test_cmc_x402.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/magic_agent/x402.py src/magic_agent/cmc_x402.py tests/test_x402.py tests/test_cmc_x402.py
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
import pytest

from magic_agent.decision_pipeline import DecisionPipeline
from magic_agent.executability import PreparedOrder
from magic_agent.lifecycle import LifecycleEvaluator, LifecycleObservation
from magic_agent.position_manager import PositionManager
from magic_agent.risk_policy import MarketRiskContext, PortfolioRiskState, QuantityCaps, RiskConfig, RiskPolicy
from magic_agent.runner import run_cycle
from magic_agent.spot_models import ActionPurpose, AuthorizedSetup


def _app(*, authorized: bool, policy_present: bool = True, execution_eligible: bool = True):
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    candidate = CandidateSnapshot(
        "zec-bsc", now, now + timedelta(minutes=15),
        Decimal("0.1"), Decimal("0.4"), Decimal("0.10"), Decimal("0.20"),
        False, (), Decimal("1"),
    )
    envelope = SimpleNamespace(
        identity_key="zec-bsc", symbol="ZEC", snapshot=candidate,
        execution_eligible=execution_eligible, pinned=True,
    )
    setup = AuthorizedSetup.example() if authorized else None
    executions = []
    alerts = []
    state = SimpleNamespace(
        blocks_new_exposure=False,
        canary_mode=False,
        risk_state=lambda: PortfolioRiskState.example(),
        as_dict=lambda: {},
    )
    app = SimpleNamespace(
        reconcile_unfinished=lambda: None,
        position_manager=PositionManager(
            positions=lambda: [], observe=lambda position, observed_at, reduction: None,
            evaluator=LifecycleEvaluator(), pipeline=DecisionPipeline(),
            risk_policy=RiskPolicy(RiskConfig.defaults() if policy_present else None),
            risk_state=lambda: PortfolioRiskState.example(),
            sell_probe=lambda position, quantity: None, execute=lambda *args: None,
        ),
        state=state,
        cmc_source=SimpleNamespace(snapshot=lambda observed_at: SimpleNamespace(
            snapshots={"ZEC": candidate}, exclusions=(),
        )),
        candidate_source=SimpleNamespace(enumerate=lambda **kwargs: SimpleNamespace(
            monitoring=(envelope,), discovery=(), exclusions=(),
        )),
        watchlist=SimpleNamespace(
            state=SimpleNamespace(discovery_due=lambda observed_at: False),
            promote=lambda symbol: None, mark_discovery=lambda observed_at: None,
        ),
        exclusion_journal=SimpleNamespace(
            append_many=lambda rows, observed_at: None,
            append_code=lambda symbol, reason, observed_at: None,
        ),
        scanner_gateway=SimpleNamespace(scan=lambda selected: setup),
        executability=SimpleNamespace(prepare_order=lambda **kwargs: PreparedOrder(
            approved=True, reasons=(),
            risk=kwargs["risk_policy"].evaluate(
                kwargs["setup"], kwargs["risk_state"], ActionPurpose.STRATEGY,
                QuantityCaps.unbounded(), kwargs["market"],
            ),
            quote={"id": "q", "quantity": "0.25"},
        )),
        risk_policy=RiskPolicy(RiskConfig.defaults()) if policy_present else None,
        pipeline=DecisionPipeline(),
        lifecycle_evaluator=LifecycleEvaluator(),
        observe_entry=lambda selected, risk, observed_at: LifecycleObservation(
            selected, risk, None, None, None, False, Decimal("0"), False,
        ),
        execution_coordinator=SimpleNamespace(submit=lambda intent, **evidence: executions.append(SimpleNamespace(state="RECONCILED", intent=intent))),
        decision_journal=SimpleNamespace(append=lambda decision, observed_at: None),
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


def test_registry_resolved_watchlist_name_is_monitored_but_not_executed():
    app, executions, alerts, now = _app(authorized=True, execution_eligible=False)
    run_cycle(app, now)
    assert executions == []


def test_missing_policy_processes_protective_exits_but_blocks_entry_path():
    app, executions, alerts, now = _app(authorized=True, policy_present=False)
    with pytest.raises(RuntimeError, match="mandatory RiskPolicy"):
        run_cycle(app, now)
    assert executions == []
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_spot_runtime.py tests/test_cli.py tests/test_live.py -q`
Expected: FAIL while active CLI choices and models remain perp-shaped.

- [ ] **Step 3: Complete the spot migration**

Make `paper` the default and `twak` the only live executor choice. Remove active CLI leverage/short/Aster configuration. The runtime order is fixed:

```python
from magic_agent.cmc_selector import select_candidates
from magic_agent.risk_policy import MarketRiskContext
from magic_agent.spot_models import ActionPurpose


def run_cycle(app, now) -> None:
    app.reconcile_unfinished()
    app.position_manager.process_exits(now)
    if app.state.blocks_new_exposure:
        return
    if app.risk_policy is None:
        raise RuntimeError("mandatory RiskPolicy is missing")
    cmc_batch = app.cmc_source.snapshot(now)
    snapshots = cmc_batch.snapshots
    app.exclusion_journal.append_many(cmc_batch.exclusions, now)
    batch = app.candidate_source.enumerate(
        now=now, watchlist=app.watchlist.state, snapshots=snapshots,
    )
    app.exclusion_journal.append_many(batch.exclusions, now)
    fresh = {row.identity_key: row for row in select_candidates(list(snapshots.values()), now=now)}
    for candidate in (*batch.monitoring, *batch.discovery):
        setup = app.scanner_gateway.scan(candidate)
        if setup is None:
            continue
        if candidate in batch.discovery:
            app.watchlist.promote(candidate.symbol)
        snapshot = candidate.snapshot
        if not candidate.execution_eligible:
            app.exclusion_journal.append_code(candidate.symbol, "identity_not_gold", now)
            continue
        if snapshot is None or fresh.get(snapshot.identity_key) is None:
            app.exclusion_journal.append_code(candidate.symbol, "cmc_stale_or_vetoed", now)
            continue
        market = MarketRiskContext(
            snapshot.momentum_7d, snapshot.momentum_7d_rank_pct,
            snapshot.macro_clamp, app.state.canary_mode,
        )
        prepared = app.executability.prepare_order(
            setup=setup, market=market,
            risk_state=app.state.risk_state(), risk_policy=app.risk_policy,
        )
        if not prepared.approved:
            continue
        inputs = app.lifecycle_evaluator.evaluate(
            app.observe_entry(setup, prepared.risk, now),
        )
        decision = app.pipeline.decide(inputs)
        app.decision_journal.append(decision, now)
        if decision.intent is not None:
            app.execution_coordinator.submit(
                decision.intent, quote=prepared.quote, policy=prepared.risk,
            )
            break
    if app.watchlist.state.discovery_due(now):
        app.watchlist.mark_discovery(now)
    app.compliance.observe(app.execution_journal.confirmed_records(), now)
    app.state_journal.save(app.state.as_dict())
```

`ComplianceLedger` counts confirmed eligible swaps by verified competition-day boundaries and emits alerts only. Its execution method does not exist. Retain Aster files only as inactive historical code until a later cleanup commit; no active import may reference them.

`app.executability.prepare_order` delegates to Task 9's `prepare_exact_order`; its returned quote is
fresh, unexpired, and bound to `prepared.risk.final_qty`. Delete the legacy
`if policy_config is not None` branch from `runner.py` rather than wrapping the new policy behind it.
There is no entry code path without a constructed `RiskPolicy`. Missing policy is checked only after
protective exits have been processed, so fail-closed entry behavior cannot strand an open position.

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

The runbook must require: scanner commit pin; the fresh post-Plan-1 filter artifact; 149-row eligibility
accounting; the pinned `ZEC/DEXE/TRX/APE/LINK/XRP` monitoring list; gold identity before execution;
fresh CMC snapshot; mandatory policy; gas reserve; fresh two-sided final-size quote; empty unfinished-
execution set; competition registration; state backup; paper cycle; and explicit operator live
activation. The first live order uses the 0.25% canary risk default and one concurrent position.
Operational status must display grade fraction, counter-bias multiplier, 3% throttle, 5% entry halt,
8% emergency review, 1.5% daily halt, three-stop halt, correlation/open-risk utilization, stale-equity
state, and the confirmed 30% hard-DQ boundary.

- [ ] **Step 4: Run Opus review**

Apply `general-review-protocol` to all runtime commits. Any path that books before reconciliation, bypasses RiskPolicy, signs outside TWAK, fabricates stop/DOL, or blocks a protective exit is release-blocking.

- [ ] **Step 5: Commit documentation fixes**

```bash
git add README.md .env.example docs/track1-spot-runbook.md
git commit -m "docs: add Track 1 spot activation runbook"
```

## Self-Review

- **Coverage:** eligibility/identity REQ-010-015 -> Tasks 3/4A; CMC REQ-020-024 -> Tasks 4/4A/12; scanner REQ-030-039A -> Tasks 1/2/5; risk REQ-040-049A -> Tasks 6/7/14; executability REQ-050-056 -> Tasks 9/14; execution REQ-060-070 -> Tasks 8-10; positions REQ-080-084 -> Tasks 7/11/14; compliance REQ-090-095 -> Task 14; x402 REQ-110-115 -> Task 12; registration and migration -> Tasks 13-15.
- **Locked authority:** TWAK signs swaps and x402. `bnbagent-sdk` is ERC-8004 identity only. CMC never creates setup authority.
- **Accepted limits:** scanner supersession semantics remain untouched and are consumed as scanner-owned provenance; macro-distant H12 pivots reduce size or skip; compliance execution remains disabled; live defaults remain conservative until causal replay evidence exists.
- **Dependency:** Task 1 cannot substitute a scanner SHA until the scanner-contract plan is implemented and reviewed. All other tasks may be developed against the local editable scanner but the operational gate remains closed.
