# Scanner Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit Track 1 aggressive scanner contract that accepts the fixed spot-long
profile constraint, keeps setup authorization inside the scanner, and exports governing-POI,
Direct-QML identity/lifecycle provenance, structural-stop, campaign-DOL, checklist-DOL,
authorization, and advisory-M15 evidence
without requiring an H12 sweep or M15 data.

**Architecture:** Preserve all legacy/research defaults. Add `execution_mode="track1_aggressive"`
as an explicit opt-in and `allowed_side="Long"` as a strategy-profile constraint, not a
MIDAS-derived market prediction. The scanner passes the same mode and side through grading and
entry reporting, resolves stop/DOL independently of sweep qualification, and exclusively returns
`authorized | monitor_only | no_trade` after validating H12 discount, A-/B-family grade, QML containment
inside the selected same-side governing H12 POI, the scanner's existing lifecycle-permitted QML
selection, stop, and campaign DOL. This slice consumes the existing lifecycle/supersession filter
as-is; it does not add or reinterpret lifecycle detector semantics. M15 detectors
still run when data exists; their evidence is advisory and never changes aggressive entry geometry.

**Tech Stack:** Python 3.11+, `uv`, `pytest`, `pandas`, frozen dataclasses. **Implementation
repo:** `trading-scanner/` on branch `build/scanner-core` (NOT the midas repo). Per
`trading-scanner/CLAUDE.md`: Task 1 and Task 6 (level/authorization semantics) =
**Opus implementer**; Tasks 2–5 and Task 7 (mechanical wiring/verification) = **Sonnet implementer**; all reviews = **Opus**
via `general-review-protocol`.

**Recon basis:** `midas/docs/superpowers/specs/2026-06-21-recon-findings.md` §1 (esp. §1a/§1b ⚠️
sweep-gating, §1c full REQ-039 record, §1f scope). **Out of scope (frozen):** QML supersession,
H4, D1.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/magic_scanner/detectors/trade_levels.py` | Sweep-independent canonical resolver: `(entry, stop, campaign_dol)` from one as-of eval | **Create** |
| `tests/test_trade_levels.py` | Prove resolution works on sweep-optional setups + dominant-pick direction | **Create** |
| `src/magic_scanner/authorization.py` | Track 1 profile constraint, governing-POI containment, and scanner-owned authorization result | **Create** |
| `tests/test_authorization.py` | Prove naked QMLs cannot authorize and sweep-optional contained QMLs can | **Create** |
| `src/magic_scanner/scan.py` | `ScanResult` export fields + `scan_pair` wiring + relaxed `len(ltf)` guard | Modify `:56-88`, `:131-158` |
| `tests/test_scan.py` | Prove the export record is present + `None` when no direction | Modify |
| `src/magic_scanner/detectors/entry.py` | Make `df_15m` optional/empty-tolerant in `derive_entry_report`/`derive_entry_setup`; reach the existing Aggressive branch | Modify `:700-712`, `:764`, `:846-901` |
| `tests/test_entry.py` | Prove `df_15m=None` reaches Direct QML while present M15 evidence remains detectable | Modify |
| `src/magic_scanner/inputs.py` | Plumb `prefer_aggressive` through `grade`/`assemble_inputs`; relax `len(ltf)` guard so H1+QML fills #6 entry | Modify `:286-301` |
| `tests/test_inputs.py` | Prove aggressive entry auto-fills #6 DOL from H1+QML without M15 | Modify |

---

## Verified signatures (write tests against THESE — do not guess)

```python
# scan.py:56-88
@dataclass(frozen=True)
class ScanResult:
    symbol: str
    inputs: ChecklistInputs
    result: ChecklistResultV2
    confirmation_kind: str = "none"
    entry: EntrySetup | None = None

# scan.py:91 — def scan_pair(symbol, frames, **grade_kwargs) -> ScanResult

# detectors/risk.py:17-38
@dataclass(frozen=True)
class StopPlan:
    level: float; anchor: float; source: StopSource; anchor_bar: int   # StopSource = "h12_sweep_extreme"|"h12_pivot"

def resolve_structural_stop(df_h12, *, direction, entry, qml_level=None, left=15, right=10,
                            buffer_frac=0.05, require_reclaim=True, allow_pivot_fallback=True) -> StopPlan | None

# detectors/dol.py:69-83, 231-239
@dataclass(frozen=True)
class DolTarget:
    level: float; source: str   # "equal_pool"|"prior_day"|"prior_week"|"range_opposite"

def dol_targets(df, direction, *, left=15, right=10, equal_tol=1e-6, ref_price=None) -> list[DolTarget]
# dominant pick (mirror entry.py:553-557): Long -> max(level); Short -> min(level)

# detectors/entry.py — current signature; Task 3 changes only df_15m to optional.
def derive_entry_report(
    df_h1: pd.DataFrame,
    df_15m: pd.DataFrame,
    *,
    direction: str,
    df_h12: pd.DataFrame | None = None,
    depth: int = 3,
    mss_left: int = 2,
    mss_right: int = 2,
    h12_left: int = 15,
    h12_right: int = 10,
    prefer_aggressive: bool = False,
) -> EntrySetup
#   Aggressive branch already exists at :893-894 (prefer_aggressive) and :900-901 (QML-only).
#   checklist-DOL stays on inputs.draw_on_liquidity (bool) — already present, NOT re-added here.

# New public profile contract defined by this plan:
ExecutionMode = Literal["research_confirmation", "track1_aggressive"]
AuthorizationState = Literal["authorized", "monitor_only", "no_trade"]
# MIDAS passes allowed_side="Long" because Track 1 is spot-long only. The scanner validates it;
# MIDAS does not derive or authorize market direction.
```

---

### Task 1: Sweep-independent trade-levels resolver

**Files:**
- Create: `src/magic_scanner/detectors/trade_levels.py`
- Test: `tests/test_trade_levels.py`

This is the load-bearing task (recon §1a/§1b ⚠️): campaign-DOL is today resolved only *after* the
H12 sweep gate (`entry.py:510`), so a sweep-optional aggressive setup gets no DOL. This resolver
composes the existing standalone functions with no lifecycle/sweep precondition.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_levels.py
import pandas as pd
import pytest

import magic_scanner.detectors.trade_levels as trade_levels_mod
from magic_scanner.detectors.trade_levels import resolve_trade_levels, TradeLevels
from magic_scanner.detectors.risk import StopPlan
from magic_scanner.detectors.dol import DolTarget


def _closed_h12_frame() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=4, freq="12h", tz="UTC", name="open_time")
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [102.0, 103.0, 104.0, 105.0],
            "low": [98.0, 99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0, 104.0],
            "volume": [1000.0, 1001.0, 1002.0, 1003.0],
            "close_time": idx + pd.Timedelta(hours=12),
        },
        index=idx,
    )


def test_composes_stop_and_campaign_dol_without_lifecycle_or_sweep_gate(monkeypatch):
    df = _closed_h12_frame()
    expected_stop = StopPlan(94.5, 95.0, "h12_pivot", 1)
    expected_targets = [DolTarget(112.0, "prior_day"), DolTarget(120.0, "prior_week")]
    stop_call = {}

    def fake_stop(frame, **kwargs):
        stop_call.update(kwargs)
        assert frame is df
        return expected_stop

    monkeypatch.setattr(trade_levels_mod, "resolve_structural_stop", fake_stop)
    monkeypatch.setattr(
        trade_levels_mod,
        "dol_targets",
        lambda frame, direction, **kwargs: expected_targets,
    )

    levels = resolve_trade_levels(df, direction="Long", entry=100.0, qml_level=100.0)

    assert levels == TradeLevels(
        entry=100.0,
        stop=expected_stop,
        campaign_dol=expected_targets[1],
        dol_candidates=tuple(expected_targets),
    )
    assert stop_call["allow_pivot_fallback"] is True


@pytest.mark.parametrize(
    ("direction", "targets", "expected"),
    [
        ("Long", [DolTarget(110.0, "prior_day"), DolTarget(125.0, "prior_week")], 125.0),
        ("Short", [DolTarget(90.0, "prior_day"), DolTarget(75.0, "prior_week")], 75.0),
    ],
)
def test_campaign_dol_uses_directional_furthest_target(monkeypatch, direction, targets, expected):
    df = _closed_h12_frame()
    monkeypatch.setattr(trade_levels_mod, "resolve_structural_stop", lambda *a, **k: None)
    monkeypatch.setattr(trade_levels_mod, "dol_targets", lambda *a, **k: targets)

    levels = resolve_trade_levels(df, direction=direction, entry=100.0, qml_level=100.0)

    assert levels is not None
    assert levels.campaign_dol is not None
    assert levels.campaign_dol.level == expected


def test_returns_none_on_invalid_direction_or_empty():
    df = _closed_h12_frame()
    assert resolve_trade_levels(df, direction="—", entry=120.0) is None
    assert resolve_trade_levels(pd.DataFrame(), direction="Long", entry=120.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trade_levels.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'magic_scanner.detectors.trade_levels'`

- [ ] **Step 3: Write the resolver**

```python
# src/magic_scanner/detectors/trade_levels.py
"""Sweep-independent canonical trade-level resolution (stop + campaign DOL).

The lifecycle classifier resolves these only on the post-sweep path
(``classify_qml_lifecycle`` early-returns ``no_coherent_sweep``). The aggressive
Direct-QML doctrine permits a sweep-OPTIONAL lower-grade trade when a concrete
campaign DOL exists, so this module re-resolves the SAME canonical stop
(``resolve_structural_stop`` with pivot fallback) and campaign-DOL target
(``dol_targets`` dominant pick) with no sweep precondition, from ONE as-of frame.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from magic_scanner.detectors.dol import DolTarget, dol_targets
from magic_scanner.detectors.risk import StopPlan, resolve_structural_stop


@dataclass(frozen=True)
class TradeLevels:
    """Stop + campaign DOL resolved from one as-of H12 evaluation."""

    entry: float
    stop: StopPlan | None
    campaign_dol: DolTarget | None
    dol_candidates: tuple[DolTarget, ...]


def resolve_trade_levels(
    df_h12: pd.DataFrame,
    *,
    direction: str,
    entry: float,
    qml_level: float | None = None,
    left: int = 15,
    right: int = 10,
    buffer_frac: float = 0.05,
) -> TradeLevels | None:
    """Resolve (stop, campaign_dol) for ``direction`` with NO sweep precondition.

    Mirrors the lifecycle's dominant-DOL pick (Long -> furthest/max level, Short ->
    furthest/min) and the canonical stop hierarchy (sweep-extreme first, then major
    H12 pivot via ``allow_pivot_fallback=True``). Returns ``None`` only when the
    direction is invalid, the frame is empty, or NEITHER a stop nor a DOL resolves.
    """
    if direction not in ("Long", "Short") or df_h12.empty or not np.isfinite(entry):
        return None

    ref = float(qml_level) if qml_level is not None else float(entry)
    stop = resolve_structural_stop(
        df_h12,
        direction=direction,
        entry=float(entry),
        qml_level=qml_level,
        left=left,
        right=right,
        buffer_frac=buffer_frac,
        allow_pivot_fallback=True,
    )
    targets = dol_targets(df_h12, direction, left=left, right=right, ref_price=ref)
    campaign: DolTarget | None = None
    if targets:
        campaign = (
            max(targets, key=lambda t: t.level)
            if direction == "Long"
            else min(targets, key=lambda t: t.level)
        )

    if stop is None and campaign is None:
        return None
    return TradeLevels(
        entry=float(entry),
        stop=stop,
        campaign_dol=campaign,
        dol_candidates=tuple(targets),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trade_levels.py -q`
Expected: PASS (4 passed: composition, two directional cases, invalid input)

- [ ] **Step 5: Commit**

```bash
git add src/magic_scanner/detectors/trade_levels.py tests/test_trade_levels.py
git commit -m "feat(scanner): sweep-independent trade-levels resolver (stop + campaign DOL)"
```

---

### Task 2: Export the trade-levels record on `ScanResult`

**Files:**
- Modify: `src/magic_scanner/scan.py:56-88` (dataclass), `:152-158` (return)
- Test: `tests/test_scan.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scan.py  (add)
import magic_scanner.scan as scan_mod
from magic_scanner.detectors.dol import DolTarget
from magic_scanner.detectors.risk import StopPlan
from magic_scanner.detectors.trade_levels import TradeLevels


def test_scanresult_exports_trade_levels_with_full_provenance(monkeypatch):
    expected = TradeLevels(
        entry=97.0,
        stop=StopPlan(89.5, 90.0, "h12_pivot", 8),
        campaign_dol=DolTarget(120.0, "prior_week"),
        dol_candidates=(DolTarget(120.0, "prior_week"),),
    )
    calls = []

    def fake_resolve(frame, **kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(scan_mod, "resolve_trade_levels", fake_resolve)
    res = scan_pair(
        "CAD/JPY",
        _qml_chain_frames(),
        trade_direction="Long",
        h12_left=2,
        h12_right=2,
    )

    assert res.levels == expected
    assert res.levels.stop == StopPlan(89.5, 90.0, "h12_pivot", 8)
    assert res.levels.campaign_dol == DolTarget(120.0, "prior_week")
    assert calls == [{
        "direction": "Long",
        "entry": 97.0,
        "qml_level": 97.0,
        "left": 2,
        "right": 2,
    }]


def test_scanresult_levels_none_without_direction():
    res = scan_pair("BTC/USDT", _min_frames(h1=_h1_orderflow_window()))
    assert res.inputs.trade_direction == "—"
    assert res.entry is None
    assert res.levels is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scan.py -q -k trade_levels`
Expected: FAIL — `AttributeError: 'ScanResult' object has no attribute 'levels'`

- [ ] **Step 3: Add the field + wire `scan_pair`**

```python
# scan.py — imports
from magic_scanner.detectors.trade_levels import TradeLevels, resolve_trade_levels

# scan.py:84-88 — add the field
    confirmation_kind: str = "none"
    entry: EntrySetup | None = None
    levels: TradeLevels | None = None   # stop + campaign DOL, one as-of eval (REQ-039)
```

```python
# scan.py — inside scan_pair, after entry_report is derived (~after :150), before the return:
    levels: TradeLevels | None = None
    if inputs.trade_direction in ("Long", "Short") and h12_df is not None and len(h12_df):
        qml = getattr(entry_report, "qml_key_level", None)
        anchor_entry = float(qml) if qml is not None else (
            float(entry_report.entry) if entry_report is not None
            and entry_report.entry is not None else None
        )
        if anchor_entry is not None:
            levels = resolve_trade_levels(
                h12_df,
                direction=inputs.trade_direction,
                entry=anchor_entry,
                qml_level=qml,
                left=grade_kwargs.get("h12_left", 15),
                right=grade_kwargs.get("h12_right", 10),
            )

    return ScanResult(
        symbol=symbol,
        inputs=inputs,
        result=result,
        confirmation_kind=confirmation_kind,
        entry=entry_report,
        levels=levels,
    )
```

> Note: the **checklist-DOL** stays on `inputs.draw_on_liquidity` (bool) — already present; do not
> duplicate it here. `levels` adds the two genuinely missing exports (stop + campaign DOL).
> The public REQ-039 mapping is:
> `levels.stop.level` → `stop_level`, `levels.stop.source` → `stop_source`,
> `levels.stop.anchor` → `stop_anchor`, `levels.stop.anchor_bar` → `stop_anchor_bar`,
> `levels.campaign_dol.level` → `campaign_dol_level`,
> `levels.campaign_dol.source` → `campaign_dol_source`, and
> `inputs.draw_on_liquidity` → checklist-DOL. MIDAS consumes these paths directly.
> The full record therefore comes from one scanner evaluation without parallel MIDAS derivation.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scan.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/magic_scanner/scan.py tests/test_scan.py
git commit -m "feat(scanner): export trade-levels record (stop + campaign DOL) on ScanResult"
```

---

### Task 3: Make `df_15m` optional in the entry model (aggressive when M15 absent)

**Files:**
- Modify: `src/magic_scanner/detectors/entry.py` (`derive_entry_report` `:764`, confirmation calls `:846-856`; `derive_entry_setup` `:700-712`)
- Test: `tests/test_entry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entry.py  (add)
def test_derive_entry_report_aggressive_when_no_m15():
    """A valid H1 QML with NO 15m frame yields a Direct-QML Aggressive entry."""
    report = derive_entry_report(_H1_DF, None, direction="Long")
    assert report.entry_type == "Aggressive"
    assert report.confirmation_kind == "none"
    assert report.entry == pytest.approx(report.qml_key_level)
    assert report.chained is False
    assert report.mss is False
    assert report.qml_id is not None
    assert report.qml_state == "unknown"  # no H12 lifecycle frame was supplied


def test_derive_entry_setup_tuple_aggressive_without_m15():
    entry, etype, kind = derive_entry_setup(
        _H1_DF, None, direction="Long",
    )
    assert entry == pytest.approx(97.0)
    assert etype == "Aggressive"
    assert kind == "none"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_entry.py -q -k "no_m15 or without_m15"`
Expected: FAIL — currently `TypeError`/`AttributeError` indexing `df_15m` (None) at `entry.py:846-863`

- [ ] **Step 3: Make `df_15m` empty-tolerant without changing detector semantics**

```diff
# entry.py — change only the M15 annotation in BOTH existing public signatures;
# preserve every other argument and the existing derive_entry_setup forwarding body.
-    df_15m: pd.DataFrame,
+    df_15m: pd.DataFrame | None,
```

Then replace the unconditional confirmation-evidence block in `derive_entry_report` with:

```python
    _has_ltf = df_15m is not None and len(df_15m) > 0
    if _has_ltf:
        chained = detect_chained_scob(
            df_15m,
            side,
            zone=(qml.zone_bottom, qml.zone_top),
            after_close_time=reclaim_close,
        )
        mss = detect_mss(
            df_15m,
            side,
            left=mss_left,
            right=mss_right,
            after_close_time=reclaim_close,
            max_after_close=_M15_MSS_CONFIRMATION_WINDOW,
        )
    else:
        chained = None
        mss = None

    mss_tied = mss is not None
    chained_time = (
        df_15m["close_time"].iloc[chained.scob2_confirm_bar]
        if df_15m is not None and chained is not None
        else None
    )
    mss_time = (
        df_15m["close_time"].iloc[mss.break_bar]
        if df_15m is not None and mss is not None
        else None
    )
    chained_fired = chained is not None
    both = chained_fired and mss_tied

    # Keep the existing earliest-event block, but make the type invariant explicit.
    if both:
        assert df_15m is not None
        ch_first = detect_chained_scob(
            df_15m,
            side,
            zone=(qml.zone_bottom, qml.zone_top),
            after_close_time=reclaim_close,
            earliest=True,
        )
        mss_first = detect_mss(
            df_15m,
            side,
            left=mss_left,
            right=mss_right,
            after_close_time=reclaim_close,
            max_after_close=_M15_MSS_CONFIRMATION_WINDOW,
            earliest=True,
        )
        assert ch_first is not None and mss_first is not None
        ch_first_t = df_15m["close_time"].iloc[ch_first.scob2_confirm_bar]
        mss_first_t = df_15m["close_time"].iloc[mss_first.break_bar]
        first = "mss" if mss_first_t < ch_first_t else "chained_scob"
    elif chained_fired:
        first = "chained_scob"
    elif mss_tied:
        first = "mss"
    else:
        first = None
```

Preserve the lifecycle result the existing H12 QML selector already computes. Declare the cache
immediately before `if df_h12 is not None`, populate it inside `_active_qml`, and read it after the
selected `qml = qmls[-1]`. This is additive provenance only; do not change `_active_qml` acceptance
semantics:

```python
    lifecycle_by_qml: dict[tuple[int, float], QmlLifecycleState] = {}

    # Inside the existing _active_qml(qml), immediately after classify_qml_lifecycle(...):
            lifecycle_by_qml[(qml.reclaim_bar, float(qml.key_level))] = state

    # Immediately after qml = qmls[-1]:
    selected_lifecycle = lifecycle_by_qml.get((qml.reclaim_bar, float(qml.key_level)))
    qml_id = f"{side}:{qml.reclaim_bar}:{float(qml.key_level):.12g}"
    qml_state = selected_lifecycle["state"] if selected_lifecycle is not None else "unknown"
    qml_state_reason = (
        selected_lifecycle["reason"] if selected_lifecycle is not None else "h12_lifecycle_unavailable"
    )
```

Extend `EntrySetup` with defaulted additive fields so legacy positional constructors remain valid:

```python
    qml_id: str | None = None
    qml_state: Literal["active", "protected", "played_out", "unknown"] = "unknown"
    qml_state_reason: str = "h12_lifecycle_unavailable"
```

Add `Literal` to the existing typing imports. In the final `EntrySetup(...)` return, pass
`qml_id=qml_id`, `qml_state=qml_state`, and `qml_state_reason=qml_state_reason`. `_EMPTY_SETUP`
continues to use the defaults. This exposes the scanner's existing lifecycle decision so MIDAS can
consume it without reclassification or reviving a superseded/rejected QML.

> The Aggressive branch (`entry.py:900-901`) already produces `(qml.key_level, "Aggressive",
> "none")`; this change only ensures it is *reached* when `df_15m` is None/empty by skipping the
> M15-indexing detectors. When M15 exists, always run both detectors even in aggressive mode so
> `EntrySetup.chained`/`mss` remain advisory evidence; only entry geometry and
> `confirmation_kind` are forced aggressive. Do not alter the branch itself.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_entry.py -q`
Expected: PASS (no regressions in the existing Phase-4 entry tests)

- [ ] **Step 5: Commit**

```bash
git add src/magic_scanner/detectors/entry.py tests/test_entry.py
git commit -m "feat(scanner): df_15m optional in entry model -> Direct-QML aggressive without M15"
```

---

### Task 4: Add the explicit Track 1 aggressive profile and propagate it consistently

**Files:**
- Modify: `src/magic_scanner/types.py`, `src/magic_scanner/inputs.py`, `src/magic_scanner/scan.py`
- Test: `tests/test_inputs.py`, `tests/test_scan.py`

`prefer_aggressive` is an entry-detector implementation detail. The public API is an explicit
`execution_mode`; the generic scanner default remains research/confirmation behavior. MIDAS passes
the fixed `allowed_side="Long"` Track 1 profile constraint. This is not MIDAS-derived direction:
the scanner must still return an authorization result before the candidate becomes tradeable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inputs.py — add inside TestEntryDerivation
def test_track1_mode_forces_aggressive_but_preserves_default_confirmation(self):
    frames = self._frames_with_ltf(_ltf_chain())
    default = assemble_inputs(
        frames,
        weekly_left=_LEFT, weekly_right=_RIGHT,
        h12_left=_LEFT, h12_right=_RIGHT,
        trade_direction="Long",
    )
    aggressive = assemble_inputs(
        frames,
        weekly_left=_LEFT, weekly_right=_RIGHT,
        h12_left=_LEFT, h12_right=_RIGHT,
        trade_direction="Long",
        execution_mode="track1_aggressive",
    )
    assert default.entry_type == "Confirmation"
    assert aggressive.entry_type == "Aggressive"


# tests/test_scan.py
def test_scan_pair_track1_mode_is_explicit_consistent_and_keeps_m15_evidence():
    default = scan_pair(
        "CAD/JPY", _qml_chain_frames(), trade_direction="Long",
        h12_left=2, h12_right=2,
    )
    aggressive = scan_pair(
        "CAD/JPY", _qml_chain_frames(), execution_mode="track1_aggressive",
        allowed_side="Long", h12_left=2, h12_right=2,
    )
    assert default.inputs.entry_type == "Confirmation"
    assert default.confirmation_kind == "chained_scob"
    assert aggressive.inputs.h12_liquidity_sweep is False  # direction comes from the profile
    assert aggressive.inputs.trade_direction == "Long"
    assert aggressive.inputs.entry_type == "Aggressive"
    assert aggressive.entry is not None
    assert aggressive.entry.entry_type == "Aggressive"
    assert aggressive.entry.confirmation_kind == "none"
    assert aggressive.entry.chained is True       # advisory evidence preserved
    assert aggressive.entry.entry == pytest.approx(aggressive.entry.qml_key_level)


def test_track1_mode_requires_the_fixed_long_profile_constraint():
    with pytest.raises(ValueError, match="allowed_side='Long'"):
        scan_pair("CAD/JPY", _qml_chain_frames(), execution_mode="track1_aggressive")


def test_allowed_side_is_rejected_outside_track1_mode():
    with pytest.raises(ValueError, match="allowed_side is Track 1 only"):
        scan_pair("CAD/JPY", _qml_chain_frames(), allowed_side="Long")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_inputs.py tests/test_scan.py -q -k track1`
Expected: FAIL — `assemble_inputs`/`scan_pair` do not accept `execution_mode` or `allowed_side`.

- [ ] **Step 3: Thread the flag through assembly + scan**

```python
# types.py
from typing import Literal
ExecutionMode = Literal["research_confirmation", "track1_aggressive"]

# inputs.py — import ExecutionMode and add it to assemble_inputs:
from magic_scanner.types import ExecutionMode

    execution_mode: ExecutionMode = "research_confirmation",

# Validate once, then map the public mode to the detector detail:
    if execution_mode not in ("research_confirmation", "track1_aggressive"):
        raise ValueError(f"unsupported execution_mode: {execution_mode!r}")
    prefer_aggressive = execution_mode == "track1_aggressive"

# Forward it into the existing auto-derive call:
        d_entry, d_type, _ = derive_entry_setup(
            h1_for_entry, ltf_for_entry, direction=trade_direction,
            df_h12=h12_df, h12_left=h12_left, h12_right=h12_right,
            prefer_aggressive=prefer_aggressive,
        )
```

```python
# scan.py — explicit public mode; legacy behavior remains the default.
from typing import Literal

from magic_scanner.types import ExecutionMode


def scan_pair(
    symbol: str,
    frames: dict[str, pd.DataFrame],
    *,
    execution_mode: ExecutionMode = "research_confirmation",
    allowed_side: Literal["Long"] | None = None,
    **grade_kwargs,
) -> ScanResult:
    if execution_mode == "track1_aggressive":
        if allowed_side != "Long":
            raise ValueError("track1_aggressive requires allowed_side='Long'")
        explicit = grade_kwargs.get("trade_direction")
        if explicit not in (None, "Long"):
            raise ValueError("trade_direction conflicts with allowed_side='Long'")
        grade_kwargs["trade_direction"] = "Long"
    elif allowed_side is not None:
        raise ValueError("allowed_side is Track 1 only")

    # The SAME mode reaches grade and the surfaced report.
    inputs, result = grade(frames, execution_mode=execution_mode, **grade_kwargs)
    prefer_aggressive = execution_mode == "track1_aggressive"

    entry_report: EntrySetup | None = None
    confirmation_kind = "none"
    h1_df = frames.get(grade_kwargs.get("h1_tf", "1h"))
    ltf_df = frames.get(grade_kwargs.get("ltf_tf", "15m"))
    h12_df = frames.get(grade_kwargs.get("h12_tf", "12h"))
    if (
        inputs.trade_direction in ("Long", "Short")
        and h1_df is not None and len(h1_df)
        and ltf_df is not None and len(ltf_df)
    ):
        entry_report = derive_entry_report(
            h1_df, ltf_df, direction=inputs.trade_direction, df_h12=h12_df,
            h12_left=grade_kwargs.get("h12_left", 15),
            h12_right=grade_kwargs.get("h12_right", 10),
            prefer_aggressive=prefer_aggressive,
        )
        confirmation_kind = entry_report.confirmation_kind
```

> The default remains `research_confirmation`; existing `test_scan_pair_surfaces_confirmation_kind`
> stays unchanged and green. MIDAS must call `scan_symbols(
> symbols, execution_mode="track1_aggressive", allowed_side="Long")` explicitly. No post-grade default
> split is permitted: `inputs`, `entry`, and later authorization must share the same mode.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_entry.py tests/test_inputs.py tests/test_scan.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/magic_scanner/types.py src/magic_scanner/inputs.py src/magic_scanner/scan.py tests/test_inputs.py tests/test_scan.py
git commit -m "feat(scanner): add explicit Track 1 aggressive profile"
```

---

### Task 5: Relax the `len(ltf)` guards so H1+QML derives Direct QML without M15

**Files:**
- Modify: `src/magic_scanner/inputs.py:286-301`, `src/magic_scanner/scan.py:136-140`
- Test: `tests/test_inputs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inputs.py — add inside TestEntryDerivation
def test_track1_aggressive_needs_no_m15_to_derive_qml_entry(self):
    frames = self._frames_with_ltf(_ltf_chain())
    del frames["15m"]
    inputs = assemble_inputs(
        frames,
        weekly_left=_LEFT, weekly_right=_RIGHT,
        h12_left=_LEFT, h12_right=_RIGHT,
        trade_direction="Long",
        execution_mode="track1_aggressive",
    )
    assert inputs.trade_direction == "Long"
    assert inputs.entry == pytest.approx(97.0)
    assert inputs.entry_type == "Aggressive"


# tests/test_scan.py
def test_scan_pair_track1_aggressive_needs_no_m15():
    frames = _qml_chain_frames()
    del frames["15m"]
    result = scan_pair(
        "CAD/JPY", frames,
        execution_mode="track1_aggressive", allowed_side="Long",
        h12_left=2, h12_right=2,
    )
    assert result.inputs.trade_direction == "Long"
    assert result.inputs.entry == pytest.approx(97.0)
    assert result.inputs.entry_type == "Aggressive"
    assert result.entry is not None
    assert result.entry.entry == pytest.approx(97.0)
    assert result.entry.chained is False
    assert result.entry.mss is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_inputs.py tests/test_scan.py -q -k needs_no_m15`
Expected: FAIL — `inputs.entry is None` because the `len(ltf_for_entry)` guard skips the derive.

- [ ] **Step 3: Relax the guards**

```python
# inputs.py:286-301 — drop the ltf non-empty requirement; H1 + a QML is enough.
    if entry is None or entry_type == _UNSET:
        h1_for_entry = frames.get(h1_tf)
        ltf_for_entry = frames.get(ltf_tf)   # may be None -> df_15m optional (Task 3)
        if trade_direction in ("Long", "Short") and h1_for_entry is not None and len(h1_for_entry):
            d_entry, d_type, _ = derive_entry_setup(
                h1_for_entry, ltf_for_entry, direction=trade_direction,
                df_h12=h12_df, h12_left=h12_left, h12_right=h12_right,
                prefer_aggressive=(execution_mode == "track1_aggressive"),
            )
            if entry is None:
                entry = d_entry
            if entry_type == _UNSET:
                entry_type = d_type
```

```python
# scan.py:136-140 — relax the scan_pair guard to H1-only (ltf optional):
    if (
        inputs.trade_direction in ("Long", "Short")
        and h1_df is not None and len(h1_df)
    ):
        entry_report = derive_entry_report(
            h1_df, ltf_df, direction=inputs.trade_direction, df_h12=h12_df,
            h12_left=grade_kwargs.get("h12_left", 15),
            h12_right=grade_kwargs.get("h12_right", 10),
            prefer_aggressive=(execution_mode == "track1_aggressive"),
        )
        confirmation_kind = entry_report.confirmation_kind
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_inputs.py tests/test_scan.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/magic_scanner/inputs.py src/magic_scanner/scan.py tests/test_inputs.py tests/test_scan.py
git commit -m "feat(scanner): derive Direct-QML entry when M15 is absent"
```

---

### Task 6: Scanner-owned governing-POI authorization

**Files:**
- Create: `src/magic_scanner/authorization.py`
- Create: `tests/test_authorization.py`
- Modify: `src/magic_scanner/scan.py`

The fixed Track 1 side is only a profile constraint. MIDAS never turns it into a trade. The
scanner must prove that the selected QML is inside the selected active same-side governing H12
POI, that the POI sits in the correct half of the H12 dealing range, and that grade/stop/DOL pass.

- [ ] **Step 1: Write the failing authorization tests**

```python
# tests/test_authorization.py
from dataclasses import replace

import pandas as pd
import pytest

import magic_scanner.authorization as auth_mod
from magic_scanner.authorization import authorize_track1_setup
from magic_scanner.detectors.dol import DolTarget
from magic_scanner.detectors.entry import EntrySetup
from magic_scanner.detectors.poi import HtfPoiSourceSet, HtfPoiZone
from magic_scanner.detectors.risk import StopPlan
from magic_scanner.detectors.swings import DealingRange
from magic_scanner.detectors.trade_levels import TradeLevels
from magic_scanner.types import ChecklistInputs, ChecklistResultV2


def _entry() -> EntrySetup:
    return EntrySetup(97.0, "Aggressive", "none", False, False, False, None,
                      97.0, pd.Timestamp("2026-01-02", tz="UTC"), None, None)


def _levels() -> TradeLevels:
    return TradeLevels(97.0, StopPlan(89.5, 90.0, "h12_pivot", 8),
                       DolTarget(120.0, "prior_week"),
                       (DolTarget(120.0, "prior_week"),))


def _inputs() -> ChecklistInputs:
    return ChecklistInputs(
        h12_liquidity_sweep=False,       # load-bearing sweep-optional case
        htf_poi=True,
        draw_on_liquidity=False,         # checklist DOL may remain false
        htf_bias="Bullish",
        weekly_range="Discount",
        h12_range="Discount",
        trade_direction="Long",
        entry_type="Aggressive",
    )


def _result(rating: str = "B") -> ChecklistResultV2:
    return ChecklistResultV2(
        has_direction=True,
        regime="aligned",
        binary_score=3,
        directional_score=5,
        score=8,
        show_score=True,
        tier=3,
        rating=rating,
        reason_tag="test",
        display_text="test",
    )


def test_contained_qml_authorizes_without_sweep_or_checklist_dol(monkeypatch):
    zone = HtfPoiZone("Breaker", "Bullish", 100.0, 95.0, 5, 7, True, "lux_breaker_block")
    monkeypatch.setattr(auth_mod, "build_htf_poi_source",
                        lambda *a, **k: HtfPoiSourceSet((zone,), (), (zone,)))
    monkeypatch.setattr(auth_mod, "dealing_range",
                        lambda *a, **k: DealingRange(140.0, 60.0, 100.0, "Bullish"))

    decision = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}),
        requested_side="Long", inputs=_inputs(), result=_result(),
        entry=_entry(), levels=_levels(), left=2, right=2,
    )

    assert decision.state == "authorized"
    assert decision.authorized_direction == "Long"
    assert decision.poi_authorized is True
    assert decision.governing_poi == zone
    assert decision.reasons == ()


def test_naked_qml_is_monitor_only_and_never_authorized(monkeypatch):
    zone = HtfPoiZone("Breaker", "Bullish", 90.0, 85.0, 5, 7, True, "lux_breaker_block")
    monkeypatch.setattr(auth_mod, "build_htf_poi_source",
                        lambda *a, **k: HtfPoiSourceSet((zone,), (), (zone,)))
    monkeypatch.setattr(auth_mod, "dealing_range",
                        lambda *a, **k: DealingRange(140.0, 60.0, 100.0, "Bullish"))

    decision = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}),
        requested_side="Long", inputs=_inputs(), result=_result(),
        entry=_entry(), levels=_levels(), left=2, right=2,
    )

    assert decision.state == "monitor_only"
    assert decision.authorized_direction is None
    assert decision.poi_authorized is False
    assert "qml_outside_governing_poi" in decision.reasons


def test_missing_campaign_dol_is_monitor_only(monkeypatch):
    zone = HtfPoiZone("Breaker", "Bullish", 100.0, 95.0, 5, 7, True, "lux_breaker_block")
    monkeypatch.setattr(auth_mod, "build_htf_poi_source",
                        lambda *a, **k: HtfPoiSourceSet((zone,), (), (zone,)))
    monkeypatch.setattr(auth_mod, "dealing_range",
                        lambda *a, **k: DealingRange(140.0, 60.0, 100.0, "Bullish"))
    levels = TradeLevels(
        entry=97.0,
        stop=StopPlan(89.5, 90.0, "h12_pivot", 8),
        campaign_dol=None,
        dol_candidates=(),
    )

    decision = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}),
        requested_side="Long", inputs=_inputs(), result=_result(),
        entry=_entry(), levels=levels, left=2, right=2,
    )

    assert decision.state == "monitor_only"
    assert decision.authorized_direction is None
    assert "campaign_dol_unresolved" in decision.reasons


def test_missing_qml_is_no_trade(monkeypatch):
    monkeypatch.setattr(auth_mod, "build_htf_poi_source",
                        lambda *a, **k: HtfPoiSourceSet((), (), ()))
    monkeypatch.setattr(auth_mod, "dealing_range", lambda *a, **k: None)

    decision = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}),
        requested_side="Long", inputs=_inputs(), result=_result(),
        entry=None, levels=None, left=2, right=2,
    )

    assert decision.state == "no_trade"
    assert decision.authorized_direction is None
    assert "no_valid_qml" in decision.reasons


def test_played_out_qml_cannot_be_reauthorized(monkeypatch):
    zone = HtfPoiZone("Breaker", "Bullish", 100.0, 95.0, 5, 7, True, "lux_breaker_block")
    monkeypatch.setattr(auth_mod, "build_htf_poi_source",
                        lambda *a, **k: HtfPoiSourceSet((zone,), (), (zone,)))
    monkeypatch.setattr(auth_mod, "dealing_range",
                        lambda *a, **k: DealingRange(140.0, 60.0, 100.0, "Bullish"))
    stale = replace(_entry(), qml_state="played_out", qml_state_reason="campaign_dol_met")
    decision = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}), requested_side="Long", inputs=_inputs(),
        result=_result("B"), entry=stale, levels=_levels(), left=2, right=2,
    )
    assert decision.state == "monitor_only"
    assert "qml_lifecycle_terminal" in decision.reasons


@pytest.mark.parametrize(
    ("rating", "h12_range", "expected_reason"),
    [
        ("C", "Discount", "grade_below_b_family"),
        ("B", "Premium", "h12_not_discount"),
    ],
)
def test_grade_and_h12_location_fail_closed(
    monkeypatch, rating, h12_range, expected_reason,
):
    zone = HtfPoiZone("Breaker", "Bullish", 100.0, 95.0, 5, 7, True, "lux_breaker_block")
    monkeypatch.setattr(auth_mod, "build_htf_poi_source",
                        lambda *a, **k: HtfPoiSourceSet((zone,), (), (zone,)))
    monkeypatch.setattr(auth_mod, "dealing_range",
                        lambda *a, **k: DealingRange(140.0, 60.0, 100.0, "Bullish"))
    inputs = _inputs()
    inputs = ChecklistInputs(
        h12_liquidity_sweep=inputs.h12_liquidity_sweep,
        orderflow=inputs.orderflow,
        htf_poi=inputs.htf_poi,
        draw_on_liquidity=inputs.draw_on_liquidity,
        htf_bias=inputs.htf_bias,
        weekly_range=inputs.weekly_range,
        h12_range=h12_range,
        trade_direction=inputs.trade_direction,
        entry_type=inputs.entry_type,
    )

    decision = authorize_track1_setup(
        pd.DataFrame({"close": [97.0]}),
        requested_side="Long", inputs=inputs, result=_result(rating),
        entry=_entry(), levels=_levels(), left=2, right=2,
    )

    assert decision.state == "monitor_only"
    assert decision.authorized_direction is None
    assert expected_reason in decision.reasons


# tests/test_scan.py — add imports at module top:
from magic_scanner.authorization import SetupAuthorization


def test_scan_pair_track1_mode_exposes_scanner_authorization(monkeypatch):
    expected = SetupAuthorization(
        state="monitor_only",
        requested_side="Long",
        authorized_direction=None,
        poi_authorized=False,
        governing_poi=None,
        qml_id="Long:10:97",
        qml_state="active",
        reasons=("campaign_dol_unresolved",),
    )
    monkeypatch.setattr(scan_mod, "authorize_track1_setup", lambda *a, **k: expected)

    result = scan_pair(
        "CAD/JPY", _qml_chain_frames(),
        execution_mode="track1_aggressive", allowed_side="Long",
        h12_left=2, h12_right=2,
    )

    assert result.authorization == expected
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/test_authorization.py -q`
Expected: FAIL — `magic_scanner.authorization` does not exist.

- [ ] **Step 3: Implement the scanner-owned authorization service**

```python
# src/magic_scanner/authorization.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from magic_scanner.detectors.entry import EntrySetup
from magic_scanner.detectors.poi import (
    HtfPoiZone, build_htf_poi_source, select_governing_htf_poi,
)
from magic_scanner.detectors.swings import dealing_range
from magic_scanner.detectors.trade_levels import TradeLevels
from magic_scanner.types import ChecklistInputs, ChecklistResultV2

AuthorizationState = Literal["authorized", "monitor_only", "no_trade"]


@dataclass(frozen=True)
class SetupAuthorization:
    state: AuthorizationState
    requested_side: Literal["Long"]
    authorized_direction: Literal["Long"] | None
    poi_authorized: bool
    governing_poi: HtfPoiZone | None
    qml_id: str | None
    qml_state: Literal["active", "protected", "played_out", "unknown"] | None
    reasons: tuple[str, ...]


def authorize_track1_setup(
    df_h12: pd.DataFrame,
    *,
    requested_side: Literal["Long"],
    inputs: ChecklistInputs,
    result: ChecklistResultV2,
    entry: EntrySetup | None,
    levels: TradeLevels | None,
    left: int = 15,
    right: int = 10,
) -> SetupAuthorization:
    reasons: list[str] = []
    qml = entry.qml_key_level if entry is not None else None
    source = build_htf_poi_source(df_h12, bias="Bullish", left=left, right=right)
    governing = (
        select_governing_htf_poi(source.all_zones, bias="Bullish", current_price=float(qml))
        if qml is not None else None
    )
    rng = dealing_range(df_h12, left=left, right=right)
    contained = bool(
        governing is not None and governing.active and governing.direction == "Bullish"
        and governing.bottom <= float(qml) <= governing.top
    ) if qml is not None else False
    in_half = bool(
        governing is not None and rng is not None
        and rng.low <= (governing.top + governing.bottom) / 2.0 <= rng.eq
    )
    poi_authorized = contained and in_half

    if inputs.h12_range != "Discount":
        reasons.append("h12_not_discount")
    if entry is None or entry.entry is None or qml is None:
        reasons.append("no_valid_qml")
    if entry is not None and entry.qml_state == "played_out":
        reasons.append("qml_lifecycle_terminal")
    if not poi_authorized:
        reasons.append("qml_outside_governing_poi")
    if not str(result.rating).startswith(("A", "B")):
        reasons.append("grade_below_b_family")
    if levels is None or levels.stop is None:
        reasons.append("canonical_stop_unresolved")
    if levels is None or levels.campaign_dol is None:
        reasons.append("campaign_dol_unresolved")

    if not reasons:
        state: AuthorizationState = "authorized"
    elif entry is not None and entry.entry is not None:
        state = "monitor_only"
    else:
        state = "no_trade"
    return SetupAuthorization(
        state=state,
        requested_side=requested_side,
        authorized_direction="Long" if state == "authorized" else None,
        poi_authorized=poi_authorized,
        governing_poi=governing,
        qml_id=entry.qml_id if entry is not None else None,
        qml_state=entry.qml_state if entry is not None else None,
        reasons=tuple(reasons),
    )
```

- [ ] **Step 4: Wire authorization onto `ScanResult` only in explicit Track 1 mode**

```python
# scan.py
from magic_scanner.authorization import SetupAuthorization, authorize_track1_setup

# Add this ScanResult field after `levels`:
    authorization: SetupAuthorization | None = None

# In scan_pair, after entry_report and levels are resolved:
    authorization = None
    if execution_mode == "track1_aggressive":
        authorization = authorize_track1_setup(
            h12_df,
            requested_side="Long",
            inputs=inputs,
            result=result,
            entry=entry_report,
            levels=levels,
            left=grade_kwargs.get("h12_left", 15),
            right=grade_kwargs.get("h12_right", 10),
        )

# Replace the Task-2 return with the complete return record:
    return ScanResult(
        symbol=symbol,
        inputs=inputs,
        result=result,
        confirmation_kind=confirmation_kind,
        entry=entry_report,
        levels=levels,
        authorization=authorization,
    )
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_authorization.py tests/test_scan.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/magic_scanner/authorization.py src/magic_scanner/scan.py tests/test_authorization.py tests/test_scan.py
git commit -m "feat(scanner): add scanner-owned Track 1 POI authorization"
```

---

### Task 7: Integration gate — full suite + parity/golden green

**Files:**
- Test: whole suite (no new file)

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — including the existing parity (`tests/test_golden.py`, the #1–#8 checklist gate)
and entry-model golden (`tests/test_golden_entry.py`) with **no regressions**. Record the
pass/deselect/xfail counts.

- [ ] **Step 2: Optional live observation (not an acceptance gate)**

Run:
```bash
uv run python -c "
from magic_scanner.feed import fetch_multi_tf
from magic_scanner.scan import scan_pair
f = fetch_multi_tf('BTC/USDT', ['1w','12h','1h'], limit=300)  # NOTE: no 15m
r = scan_pair('BTC/USDT', f, execution_mode='track1_aggressive', allowed_side='Long')
print('dir', r.inputs.trade_direction, 'etype', r.inputs.entry_type, 'kind', r.confirmation_kind)
print('levels', r.levels)
print('authorization', r.authorization)
"
```
Observation only: record the result exactly as returned. Market state may legitimately produce
`monitor_only`/`no_trade`; no pass/fail conclusion comes from this network call. Deterministic tests
in Tasks 1–6 are the acceptance evidence.

- [ ] **Step 3: Opus review**

Dispatch `general-review-protocol` (Opus) over the Task 1–6 diffs. Verify: (a) `resolve_trade_levels`
has no sweep precondition and uses `allow_pivot_fallback=True`; (b) the dominant-pick direction
matches `entry.py:553-557`; (c) legacy `scan_pair` remains confirmation-first unless the caller
explicitly requests `execution_mode="track1_aggressive"`; (d) the identical mode reaches `grade`
and `entry_report`; (e) M15 evidence is preserved when present; (f) no future leak is introduced;
(g) checklist-DOL is not duplicated; (h) only `authorization.state == "authorized"` carries an
`authorized_direction`; and (i) a QML outside the selected governing POI cannot authorize.

- [ ] **Step 4: Commit (if review-driven fixes)**

```bash
git add -A && git commit -m "test(scanner): integration gate for trade-levels export + aggressive entry"
```

---

## Self-Review (planner)

- **Spec coverage** (recon §1): §1a sweep-independent DOL → Task 1; §1b stop + pivot fallback +
  provenance → Task 1/Task 2; §1c full REQ-039 record (entry/stop/campaign-DOL/checklist-DOL, one
  as-of frame/config) → Task 2; §1d M15 spread plus existing selected-QML lifecycle provenance →
  Task 3/Task 5; explicit aggressive mode and fixed
  spot-long profile → Task 4; governing-POI authorization/no-naked-QML → Task 6. ✅
- **Type consistency:** `TradeLevels`/`StopPlan`/`DolTarget` names and fields identical across Tasks
  1–2; `resolve_trade_levels` signature stable; public `ExecutionMode` maps once to the existing
  `prefer_aggressive` detector flag; `SetupAuthorization` is the only tradeability authority. ✅
- **Placeholder scan:** tests use the real `_H1_DF`, `_h1_qml`, `_ltf_chain`,
  `_qml_chain_frames`, and `_min_frames` helpers or define complete deterministic data inline. No
  conditional assertion can false-green. ✅
- **Scope:** no changes to QML supersession semantics / H4 / D1 (frozen); Task 3 only exports the
  existing classifier result for the selected QML. The exit-lifecycle (campaign-DOL completion,
  opposing-HTF invalidation, RiskPolicy reduction) is **MIDAS-runtime plan**, not here — this plan
  only *exports* the levels MIDAS will act on. ✅

## Open items carried to the MIDAS-runtime plan (NOT this plan)
- MIDAS consuming `ScanResult.levels.stop` instead of the fabricated `setup_view.py:33-38` stop.
- Acting on `campaign_dol` for exit (campaign-DOL completion).
- The TWAK canary, x402 authority decision, DecisionPipeline (entry+exit), replay driver.
