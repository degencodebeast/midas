# Track 1 Live Canary Scoring Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the live Track 1 execution core first, then layer the mandatory 0.25% supervised canary, cost-viability promotion, normal RiskPolicy scoring, qualification pace tracking, and agent narrative on top.

**Architecture:** Phase 0 wires the missing live engine: deployable scanner pin, TWAK quote provider, live execution coordinator, live exits, chain-position rebuild, VPS bring-up, systemd shape, and supervised canary smoke gate. Phase 1 then adds the canary/scoring state machine, cost-viability gate, qualification monitor, and narrative journal around `runner.run_cycle` without allowing any advisory or dashboard layer to alter scanner, RiskPolicy, TWAK, or reconciliation authority.

**Tech Stack:** Python 3.11+, pytest, dataclasses, Decimal money math, existing `magic_agent` runtime modules, TWAK CLI wrapper through `magic_agent.twak.TwakRunner`, ccxt-backed Gate.io frames for live market data.

---

## Source Specification

Implement against:

- `../../../../spec/spec-process-track1-live-canary-scoring-run.md`
- `midas/docs/superpowers/plans/2026-06-21-midas-spot-runtime-plan.md`
- `midas/docs/superpowers/plans/2026-06-21-app-assembly-plan.md`
- `midas/docs/track1-spot-runbook.md`

Do not implement smart-money routing or an LLM supervisor in this plan. The agent narrative here is deterministic journaling and dashboard-readable state only.

## File Structure

Create:

- `midas/src/magic_agent/live_quotes.py` - TWAK-backed exact-size quote provider.
- `midas/src/magic_agent/live_balances.py` - live balance and BSC RPC ports used by `ExecutionCoordinator`.
- `midas/src/magic_agent/live_exits.py` - TWAK-backed protective sell quote/execute ports.
- `midas/src/magic_agent/live_positions.py` - chain-truth open-position rebuild on restart.
- `midas/src/magic_agent/live_mode.py` - canary/scoring state machine and promotion helper.
- `midas/src/magic_agent/cost_viability.py` - quote-cost gate for promotion into normal scoring mode.
- `midas/src/magic_agent/qualification.py` - minimum trade-count pace monitor.
- `midas/src/magic_agent/agent_narrative.py` - append-only JSONL journal for canary/promotion/scoring events.
- `midas/deploy/twak-vps-bringup.sh` - quote-only VPS verification script for an existing registered TWAK wallet.
- `midas/deploy/midas-agent.service` - disabled systemd unit skeleton.
- `midas/tests/test_scanner_deploy_pin.py`
- `midas/tests/test_live_quotes.py`
- `midas/tests/test_live_balances.py`
- `midas/tests/test_live_exits.py`
- `midas/tests/test_live_positions.py`
- `midas/tests/test_deploy_artifacts.py`
- `midas/tests/test_live_mode.py`
- `midas/tests/test_cost_viability.py`
- `midas/tests/test_qualification.py`
- `midas/tests/test_agent_narrative.py`
- `midas/tests/test_live_canary_integration.py`

Modify:

- `midas/src/magic_agent/runtime_state.py` - persist canary/scoring metadata.
- `midas/src/magic_agent/app.py` - assemble live-mode collaborators and expose them on `App`.
- `midas/src/magic_agent/runner.py` - run cost/promotion/qualification/narrative after submission without touching protective-exit precedence.
- `midas/src/magic_agent/status.py` - surface canary/scoring and qualification state.
- `midas/src/magic_agent/cli.py` - expose operator flags for auto-promotion and live canary status.
- `midas/pyproject.toml` - replace machine-local scanner source with deployable git pin.
- `midas/uv.lock` - lock the deployable scanner source.
- `midas/docs/track1-spot-runbook.md` - document canary-to-scoring operation and gates.

Do not restructure unrelated modules. Keep all new logic in focused files and only wire it through existing seams.

## Phase Order

Execute Phase 0 before Task 1. The original canary/scoring tasks are valid only after the live execution core exists. Do not start Task 1 until Tasks 0A-0F are complete or explicitly waived by the operator with a written reason.

## Task 0A: Scanner Deploy Pin

**Files:**
- Modify: `midas/pyproject.toml`
- Modify: `midas/uv.lock`
- Test: `midas/tests/test_scanner_deploy_pin.py`

- [ ] **Step 1: Write the failing deploy-pin test**

Create `midas/tests/test_scanner_deploy_pin.py`:

```python
import pathlib
import tomllib


REQUIRED_SCANNER_SHA = "5f92552e8fdd688808e2709eefc176ab681b7f4f"
REQUIRED_SCANNER_URL = "https://github.com/degencodebeast/trading-scanner"


def test_scanner_source_is_vps_resolvable_git_pin():
    pyproject = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    source = pyproject["tool"]["uv"]["sources"]["magic-scanner"]

    assert source["git"] == REQUIRED_SCANNER_URL
    assert source["rev"] == REQUIRED_SCANNER_SHA
    assert not source["git"].startswith("file:")


def test_uv_lock_uses_same_deployable_scanner_pin():
    text = pathlib.Path("uv.lock").read_text()

    assert REQUIRED_SCANNER_URL in text
    assert REQUIRED_SCANNER_SHA in text
    assert "file:///Users/degencodebeast/Projects/personal/trading/trading-scanner" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd midas
uv run pytest tests/test_scanner_deploy_pin.py -q
```

Expected: FAIL because `pyproject.toml` currently pins `magic-scanner` to a machine-local `file://` URL.

- [ ] **Step 3: Obtain push approval and publish scanner commit**

Run only after the operator explicitly says `push approved`:

```bash
git -C ../trading-scanner status --short
git -C ../trading-scanner rev-parse 5f92552e8fdd688808e2709eefc176ab681b7f4f
git -C ../trading-scanner push origin 5f92552e8fdd688808e2709eefc176ab681b7f4f:refs/heads/build/scanner-contract
git -C ../trading-scanner ls-remote origin 5f92552e8fdd688808e2709eefc176ab681b7f4f
```

Expected:

- `status --short` prints no tracked-file changes that belong to another task.
- `rev-parse` prints `5f92552e8fdd688808e2709eefc176ab681b7f4f`.
- `ls-remote` prints the same SHA.

If push approval is not granted, stop this task and report that live VPS deployment cannot be made clone-compatible yet.

- [ ] **Step 4: Replace file source with git source**

Edit `midas/pyproject.toml` under `[tool.uv.sources]`:

```toml
magic-scanner = { git = "https://github.com/degencodebeast/trading-scanner", rev = "5f92552e8fdd688808e2709eefc176ab681b7f4f" }
```

Then run:

```bash
cd midas
uv lock --upgrade-package magic-scanner
```

Expected: `uv.lock` resolves `magic-scanner` from `https://github.com/degencodebeast/trading-scanner` at `5f92552e8fdd688808e2709eefc176ab681b7f4f`.

- [ ] **Step 5: Run deploy-pin tests**

Run:

```bash
cd midas
uv run pytest tests/test_scanner_deploy_pin.py tests/test_scanner_contract.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd midas
git add pyproject.toml uv.lock tests/test_scanner_deploy_pin.py
git commit -m "build: use deployable scanner git pin"
```

## Task 0B: Live TWAK Quote Provider

**Files:**
- Create: `midas/src/magic_agent/live_quotes.py`
- Test: `midas/tests/test_live_quotes.py`

- [ ] **Step 1: Write the failing tests**

Create `midas/tests/test_live_quotes.py`:

```python
from decimal import Decimal

import pytest

from magic_agent.live_quotes import TwakQuoteProvider
from magic_agent.risk_policy import QuantityCaps
from magic_agent.spot_models import AuthorizedSetup


class FakeTwak:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append((args, timeout))
        return self.payload


def _payload(**changes):
    data = {
        "output_qty": "10",
        "minimum_output": "9.95",
        "impact_bps": "12",
        "slippage_bps": "14",
        "expires_at": "2026-06-22T12:05:00Z",
        "gas_usd": "0.04",
        "fee_usd": "0.02",
        "notional_usd": "100",
        "asset": "USDC",
        "network": "bsc",
    }
    data.update(changes)
    return {"success": True, "data": data}


def test_twak_quote_provider_returns_prepare_exact_order_surface_for_buy():
    twak = FakeTwak(_payload())
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is True
    assert result.reasons == ()
    assert result.quantity_caps == QuantityCaps.unbounded()
    assert result.quote["output_qty"] == "10"
    assert result.quote["gas_usd"] == "0.04"
    assert twak.calls == [(
        [
            "swap", "10", "USDC", "zec-bsc",
            "--chain", "bsc", "--quote-only", "--json",
        ],
        60,
    )]


def test_twak_quote_provider_uses_probe_quantity_when_quantity_is_none():
    twak = FakeTwak(_payload())
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), None)

    assert result.approved is True
    assert twak.calls[0][0][1] == "1"


def test_twak_quote_provider_fails_closed_on_unexpected_network_or_asset():
    twak = FakeTwak(_payload(asset="USDT", network="ethereum"))
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is False
    assert "unexpected_asset" in result.reasons
    assert "unexpected_network" in result.reasons
    assert result.quote is None


def test_twak_quote_provider_fails_closed_on_missing_cost_fields():
    bad = _payload()
    del bad["data"]["gas_usd"]
    twak = FakeTwak(bad)
    provider = TwakQuoteProvider(
        twak=twak,
        wallet_address="0xwallet",
        stable_symbol="USDC",
        chain="bsc",
        now="2026-06-22T12:00:00Z",
    )

    result = provider(AuthorizedSetup.example(identity_key="zec-bsc"), Decimal("10"))

    assert result.approved is False
    assert result.reasons == ("missing_gas_usd",)
    assert result.quote is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_live_quotes.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'magic_agent.live_quotes'`.

- [ ] **Step 3: Implement TWAK quote provider**

Create `midas/src/magic_agent/live_quotes.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from magic_agent.risk_policy import QuantityCaps


@dataclass(frozen=True)
class TwakQuoteResult:
    approved: bool
    reasons: tuple[str, ...]
    quantity_caps: QuantityCaps
    quote: dict | None


_REQUIRED_QUOTE_FIELDS = (
    "output_qty",
    "minimum_output",
    "impact_bps",
    "slippage_bps",
    "expires_at",
    "gas_usd",
    "fee_usd",
    "notional_usd",
    "asset",
    "network",
)


class TwakQuoteProvider:
    """TWAK-backed quote provider matching prepare_exact_order's callable contract."""

    def __init__(
        self,
        *,
        twak,
        wallet_address: str,
        stable_symbol: str = "USDC",
        chain: str = "bsc",
        now: str,
        probe_quantity: Decimal = Decimal("1"),
    ) -> None:
        self._twak = twak
        self._wallet_address = wallet_address
        self._stable_symbol = stable_symbol
        self._chain = chain
        self._now = now
        self._probe_quantity = probe_quantity

    def __call__(self, setup, quantity: Decimal | None) -> TwakQuoteResult:
        qty = self._probe_quantity if quantity is None else quantity
        if qty <= 0:
            return TwakQuoteResult(False, ("nonpositive_quote_quantity",), QuantityCaps.unbounded(), None)
        payload = self._twak.json([
            "swap", str(qty), self._stable_symbol, setup.identity_key,
            "--chain", self._chain, "--quote-only", "--json",
        ])
        data = payload.get("data", payload)
        reasons = self._validate(data)
        if reasons:
            return TwakQuoteResult(False, reasons, QuantityCaps.unbounded(), None)
        quote = {field: str(data[field]) for field in _REQUIRED_QUOTE_FIELDS}
        quote["price"] = str(setup.entry)
        quote["symbol"] = setup.symbol
        quote["recipient"] = self._wallet_address
        return TwakQuoteResult(True, (), QuantityCaps.unbounded(), quote)

    def _validate(self, data: dict) -> tuple[str, ...]:
        reasons: list[str] = []
        for field in _REQUIRED_QUOTE_FIELDS:
            if field not in data:
                reasons.append(f"missing_{field}")
        if reasons:
            return tuple(reasons)
        if str(data["asset"]).upper() != self._stable_symbol.upper():
            reasons.append("unexpected_asset")
        if str(data["network"]).lower() != self._chain.lower():
            reasons.append("unexpected_network")
        for field in ("output_qty", "minimum_output", "notional_usd"):
            if Decimal(str(data[field])) <= 0:
                reasons.append(f"nonpositive_{field}")
        return tuple(reasons)
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_live_quotes.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd midas
git add src/magic_agent/live_quotes.py tests/test_live_quotes.py
git commit -m "feat: add twak quote provider"
```

## Task 0C: Live Balance/RPC Ports And Execution Coordinator Wiring

**Files:**
- Create: `midas/src/magic_agent/live_balances.py`
- Modify: `midas/src/magic_agent/app.py`
- Test: `midas/tests/test_live_balances.py`
- Test: `midas/tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Create `midas/tests/test_live_balances.py`:

```python
from decimal import Decimal

from magic_agent.live_balances import TwakBalanceReader, StaticRpcClient


class FakeTwak:
    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        return {
            "success": True,
            "data": {
                "stable": "1000",
                "token": "3.5",
            },
        }


def test_twak_balance_reader_returns_decimal_snapshot():
    twak = FakeTwak()
    reader = TwakBalanceReader(twak=twak, stable_symbol="USDC", chain="bsc")

    snapshot = reader.snapshot("0xtoken")

    assert snapshot == {"stable": Decimal("1000"), "token": Decimal("3.5")}
    assert twak.calls == [["wallet", "balance", "--chain", "bsc", "--token", "0xtoken", "--json"]]


def test_static_rpc_client_waits_and_counts_confirmations():
    rpc = StaticRpcClient(wallet_nonce_value=7, receipt={"status": "0x1", "blockNumber": "0x10"}, confirmation_count=3)

    assert rpc.wallet_nonce() == 7
    assert rpc.wait_receipt("0xabc") == {"status": "0x1", "blockNumber": "0x10"}
    assert rpc.confirmations({"blockNumber": "0x10"}) == 3
```

Append to `midas/tests/test_app.py`:

```python
def test_build_app_twak_wires_real_live_ports_when_injected(tmp_path, monkeypatch):
    from types import SimpleNamespace

    for name in (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_WALLET_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    ):
        monkeypatch.setenv(name, "test-secret")

    fake_scanner = SimpleNamespace(scan=lambda candidate: None)
    fake_cmc = FixtureCmcClient()
    fake_frame_source = SimpleNamespace()
    fake_twak = SimpleNamespace(json=lambda args, timeout=60: {"success": True, "data": {}})
    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=fake_scanner,
        cmc_client=fake_cmc,
        frame_source=fake_frame_source,
        twak_runner=fake_twak,
        live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
        live_balances=SimpleNamespace(snapshot=lambda identity_key: {"stable": "999", "token": "1"}),
    )

    assert app.mode == "twak"
    assert app.execution_coordinator.__class__.__name__ == "ExecutionCoordinator"
    assert app.state.canary_mode is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_live_balances.py tests/test_app.py::test_build_app_twak_wires_real_live_ports_when_injected -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'magic_agent.live_balances'` or `TypeError` for unsupported `build_app` parameters.

- [ ] **Step 3: Implement balance/RPC ports**

Create `midas/src/magic_agent/live_balances.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class TwakBalanceReader:
    def __init__(self, *, twak, stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
        self._stable_symbol = stable_symbol
        self._chain = chain

    def snapshot(self, identity_key: str) -> dict[str, Decimal]:
        payload = self._twak.json([
            "wallet", "balance",
            "--chain", self._chain,
            "--token", identity_key,
            "--json",
        ])
        data = payload.get("data", payload)
        return {
            "stable": Decimal(str(data["stable"])),
            "token": Decimal(str(data["token"])),
        }


@dataclass
class StaticRpcClient:
    wallet_nonce_value: int = 0
    receipt: dict | None = None
    confirmation_count: int = 2

    def wallet_nonce(self) -> int:
        return self.wallet_nonce_value

    def wait_receipt(self, tx_hash: str) -> dict:
        return dict(self.receipt or {"status": "0x1", "transactionHash": tx_hash})

    def confirmations(self, receipt: dict) -> int:
        return self.confirmation_count
```

- [ ] **Step 4: Wire live coordinator construction**

Modify `midas/src/magic_agent/app.py`.

Import:

```python
from magic_agent.execution_coordinator import ExecutionCoordinator
from magic_agent.live_balances import StaticRpcClient, TwakBalanceReader
from magic_agent.live_quotes import TwakQuoteProvider
from magic_agent.twak import TwakRunner
```

Add parameters to `build_app`:

```python
    twak_runner: Any = None,
    live_rpc: Any = None,
    live_balances: Any = None,
```

In live mode, construct defaults:

```python
    live_twak = twak_runner or TwakRunner()
    wallet_address = os.environ.get("WALLET_ADDRESS", "")
    live_rpc = live_rpc or StaticRpcClient()
    live_balances = live_balances or TwakBalanceReader(twak=live_twak)
```

For live executability:

```python
        quote_provider = TwakQuoteProvider(
            twak=live_twak,
            wallet_address=wallet_address,
            stable_symbol="USDC",
            chain="bsc",
            now=_iso(state),
        )
        executability = live_executability or ExecutabilityAdapter(quote_provider)
```

For live execution port:

```python
    if live_mode:
        execution_port = live_execution_coordinator or ExecutionCoordinator(
            twak=live_twak,
            rpc=live_rpc,
            balances=live_balances,
            journal=chain_journal,
            positions=position_manager,
            registry=registry,
        )
    else:
        execution_port = paper_adapter
```

Keep the `_require_live_env()` fail-closed check in Phase 0. It must include `WALLET_ADDRESS`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_live_balances.py tests/test_app.py::test_build_app_twak_wires_real_live_ports_when_injected -q
```

Expected: PASS.

- [ ] **Step 6: Run coordinator regressions**

Run:

```bash
cd midas
uv run pytest tests/test_execution_coordinator.py tests/test_reconcile.py tests/test_twak.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd midas
git add src/magic_agent/live_balances.py src/magic_agent/app.py tests/test_live_balances.py tests/test_app.py
git commit -m "feat: wire live execution coordinator ports"
```

## Task 0D: Live Exit Ports And Chain Position Rebuild

**Files:**
- Create: `midas/src/magic_agent/live_exits.py`
- Create: `midas/src/magic_agent/live_positions.py`
- Modify: `midas/src/magic_agent/app.py`
- Test: `midas/tests/test_live_exits.py`
- Test: `midas/tests/test_live_positions.py`

- [ ] **Step 1: Write the failing tests**

Create `midas/tests/test_live_exits.py`:

```python
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.live_exits import TwakSellPorts
from magic_agent.position_manager import ReconciledPosition


class FakeTwak:
    def __init__(self):
        self.calls = []

    def json(self, args, *, timeout=60):
        self.calls.append(args)
        return {"success": True, "data": {"tx_hash": "0xsell"}}


def test_twak_sell_ports_quote_and_execute_sell():
    twak = FakeTwak()
    ports = TwakSellPorts(twak=twak, chain="bsc")
    position = ReconciledPosition(
        intent_id="intent-1",
        quantity=Decimal("2"),
        identity_key="0xtoken",
        symbol="ZEC/USDT",
    )
    decision = SimpleNamespace(exit_quantity=Decimal("1.5"))

    quote = ports.sell_probe(position, Decimal("1.5"))
    ports.execute(position, decision, quote)

    assert quote.approved is True
    assert twak.calls[0] == [
        "swap", "1.5", "0xtoken", "USDC",
        "--chain", "bsc", "--quote-only", "--sell", "--json",
    ]
    assert twak.calls[1] == [
        "swap", "1.5", "0xtoken", "USDC",
        "--chain", "bsc", "--sell", "--json",
    ]
```

Create `midas/tests/test_live_positions.py`:

```python
from decimal import Decimal

from magic_agent.live_positions import rebuild_positions_from_chain
from magic_agent.position_manager import ReconciledPosition
from magic_agent.spot_models import AuthorizedSetup, SpotIntent, ActionPurpose


class FakeBalances:
    def __init__(self, token):
        self.token = token

    def snapshot(self, identity_key):
        return {"stable": Decimal("100"), "token": self.token}


def test_rebuild_positions_from_reconciled_journal_and_chain_balance():
    setup = AuthorizedSetup.example(identity_key="0xtoken", symbol="ZEC/USDT")
    intent = SpotIntent("intent-1", setup, Decimal("5"), "buy", ActionPurpose.STRATEGY)
    record = type("Record", (), {
        "state": "RECONCILED",
        "evidence": {"intent": {"intent_id": "intent-1"}},
    })()

    rebuilt = rebuild_positions_from_chain(
        records=[record],
        intents={"intent-1": intent},
        balances=FakeBalances(Decimal("3.5")),
    )

    assert rebuilt == [
        ReconciledPosition(
            "intent-1",
            Decimal("3.5"),
            Decimal("10"),
            symbol="ZEC/USDT",
            identity_key="0xtoken",
            entry=Decimal("100"),
            stop=Decimal("90"),
            campaign_dol=Decimal("120"),
        )
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_live_exits.py tests/test_live_positions.py -q
```

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement live sell ports**

Create `midas/src/magic_agent/live_exits.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LiveSellQuote:
    approved: bool
    quote: dict | None
    reasons: tuple[str, ...] = ()


class TwakSellPorts:
    def __init__(self, *, twak, stable_symbol: str = "USDC", chain: str = "bsc") -> None:
        self._twak = twak
        self._stable_symbol = stable_symbol
        self._chain = chain

    def sell_probe(self, position, quantity: Decimal) -> LiveSellQuote:
        if quantity <= 0:
            return LiveSellQuote(False, None, ("nonpositive_exit_quantity",))
        token = position.identity_key
        payload = self._twak.json([
            "swap", str(quantity), token, self._stable_symbol,
            "--chain", self._chain, "--quote-only", "--sell", "--json",
        ])
        return LiveSellQuote(True, payload.get("data", payload), ())

    def execute(self, position, decision, quote) -> str:
        token = position.identity_key
        payload = self._twak.json([
            "swap", str(decision.exit_quantity), token, self._stable_symbol,
            "--chain", self._chain, "--sell", "--json",
        ])
        return payload.get("data", {}).get("tx_hash") or payload.get("tx_hash") or "SUBMITTED"
```

- [ ] **Step 4: Implement chain position rebuild**

Create `midas/src/magic_agent/live_positions.py`:

```python
from __future__ import annotations

from decimal import Decimal

from magic_agent.execution_journal import ExecutionState
from magic_agent.position_manager import ReconciledPosition


def _is_reconciled(record) -> bool:
    state = getattr(record, "state", None)
    return state == "RECONCILED" or state is ExecutionState.RECONCILED


def rebuild_positions_from_chain(*, records, intents: dict[str, object], balances) -> list[ReconciledPosition]:
    rebuilt: list[ReconciledPosition] = []
    for record in records:
        if not _is_reconciled(record):
            continue
        intent_id = getattr(record, "intent_id", None) or record.evidence.get("intent", {}).get("intent_id")
        if intent_id not in intents:
            continue
        intent = intents[intent_id]
        setup = intent.setup
        snapshot = balances.snapshot(setup.identity_key)
        quantity = Decimal(str(snapshot["token"]))
        if quantity <= 0:
            continue
        rebuilt.append(ReconciledPosition(
            intent_id,
            quantity,
            setup.entry - setup.structural_stop,
            symbol=setup.symbol,
            identity_key=setup.identity_key,
            entry=setup.entry,
            stop=setup.structural_stop,
            campaign_dol=setup.campaign_dol,
        ))
    return rebuilt
```

- [ ] **Step 5: Wire live exits in App**

Modify `midas/src/magic_agent/app.py`.

Import:

```python
from magic_agent.live_exits import TwakSellPorts
```

When `live_mode` is true after `position_manager` is created, bind:

```python
    if live_mode:
        live_sell_ports = TwakSellPorts(twak=live_twak)
        position_manager.sell_probe = live_sell_ports.sell_probe
        position_manager.execute = live_sell_ports.execute
```

Keep `position_manager.observe` using the live `GateioFrameSource` so protective stop/DOL observations stay price-driven.

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_live_exits.py tests/test_live_positions.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd midas
git add src/magic_agent/live_exits.py src/magic_agent/live_positions.py src/magic_agent/app.py tests/test_live_exits.py tests/test_live_positions.py
git commit -m "feat: add live exits and chain position rebuild"
```

## Task 0E: VPS Bring-Up Script, Systemd Skeleton, And Kill-Switch Gate

**Files:**
- Create: `midas/deploy/twak-vps-bringup.sh`
- Create: `midas/deploy/midas-agent.service`
- Modify: `midas/src/magic_agent/app.py`
- Modify: `midas/src/magic_agent/runner.py`
- Test: `midas/tests/test_deploy_artifacts.py`
- Test: `midas/tests/test_spot_runtime.py`

- [ ] **Step 1: Write failing deploy artifact tests**

Create `midas/tests/test_deploy_artifacts.py`:

```python
from pathlib import Path


def test_twak_vps_bringup_is_quote_only_and_existing_wallet_safe():
    text = Path("deploy/twak-vps-bringup.sh").read_text()

    assert "QUOTE-ONLY" in text
    assert "--quote-only" in text
    assert "twak wallet create" not in text
    assert "twak compete register" in text
    assert 'RUN_COMPETE_REGISTER:-0' in text
    assert "WALLET_ADDRESS" in text
    assert "No real swaps" in text


def test_systemd_unit_is_disabled_skeleton_with_env_file():
    text = Path("deploy/midas-agent.service").read_text()

    assert "EnvironmentFile=/etc/midas/agent.env" in text
    assert "ExecStart=/opt/midas/.venv/bin/magic-agent run --executor twak" in text
    assert text.count("[Service]") == 1
    assert "live ports must exist" in text
```

Append to `midas/tests/test_spot_runtime.py`:

```python
def test_kill_switch_blocks_entries_but_not_exits(tmp_path):
    app, executions, alerts, now = _app(authorized=True)
    exit_calls = []

    def process_exits(observed_at):
        exit_calls.append(observed_at)
        return 0

    app.position_manager.process_exits = process_exits
    kill_switch = tmp_path / "HALT_NEW_ENTRIES"
    kill_switch.write_text("halt", encoding="utf-8")
    app.kill_switch_path = kill_switch

    from magic_agent.runner import run_cycle
    run_cycle(app, now)

    assert exit_calls == [now]
    assert executions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_deploy_artifacts.py tests/test_spot_runtime.py::test_kill_switch_blocks_entries_but_not_exits -q
```

Expected: FAIL because deploy artifacts and kill-switch gate do not exist.

- [ ] **Step 3: Add VPS bring-up script**

Create `midas/deploy/twak-vps-bringup.sh`:

```bash
#!/usr/bin/env bash
# MIDAS TWAK VPS bring-up. Layer 1 only: verify installed/authenticated TWAK,
# existing registered wallet, balances, competition status, and quote-only shape.
# No real swaps. No wallet creation. No default competition registration.
set -euo pipefail

echo "No real swaps. This script performs status checks and QUOTE-ONLY smoke only."

: "${TWAK_ACCESS_ID:?export TWAK_ACCESS_ID}"
: "${TWAK_HMAC_SECRET:?export TWAK_HMAC_SECRET}"
: "${TWAK_WALLET_PASSWORD:?export TWAK_WALLET_PASSWORD for headless unlock}"
: "${WALLET_ADDRESS:?export WALLET_ADDRESS for the already-registered BSC wallet}"
: "${GOLD_CONTRACT:?export GOLD_CONTRACT for quote-only sell smoke}"

node --version
npm --version
npm install -g @trustwallet/cli
twak --version

if ! twak auth status --json >/tmp/twak-auth-status.json 2>/dev/null; then
  twak init
fi
twak auth status --json
twak wallet status --json

TWAK_ADDRESS_JSON="$(twak wallet address --chain bsc --json)"
echo "$TWAK_ADDRESS_JSON"
if ! echo "$TWAK_ADDRESS_JSON" | grep -qi "$WALLET_ADDRESS"; then
  echo "TWAK wallet address does not match WALLET_ADDRESS=$WALLET_ADDRESS" >&2
  exit 2
fi

twak wallet balance --json
echo "Verify BNB gas reserve and USDC/USDT trading capital manually before live."

twak compete status --json
if [ "${RUN_COMPETE_REGISTER:-0}" = "1" ]; then
  twak compete register
  twak compete status --json
fi

twak swap 1 USDC "$WALLET_ADDRESS" --chain bsc --quote-only --json
twak swap 1 "$GOLD_CONTRACT" "$WALLET_ADDRESS" --chain bsc --quote-only --sell --json

echo "TWAK quote-only bring-up complete. Live trading still requires operator approval."
```

- [ ] **Step 4: Add systemd skeleton**

Create `midas/deploy/midas-agent.service`:

```ini
[Unit]
Description=MIDAS Track-1 spot agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=midas
WorkingDirectory=/opt/midas
# live ports must exist and the operator must approve before enabling this unit.
# /etc/midas/agent.env must be root-owned chmod 600 and contain:
# TWAK_ACCESS_ID, TWAK_HMAC_SECRET, TWAK_WALLET_PASSWORD, WALLET_ADDRESS,
# BSC_RPC_URL, CMC_API_KEY.
EnvironmentFile=/etc/midas/agent.env
ExecStart=/opt/midas/.venv/bin/magic-agent run --executor twak
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Add kill-switch gate after protective exits**

Modify `midas/src/magic_agent/app.py`.

Add field to `App`:

```python
    kill_switch_path: Path | None = None
```

In `build_app`, pass:

```python
        kill_switch_path=base / "HALT_NEW_ENTRIES",
```

Modify `midas/src/magic_agent/runner.py` after `process_exits(now)` and before any entry gate:

```python
    if getattr(app, "kill_switch_path", None) is not None and app.kill_switch_path.exists():
        app.exclusion_journal.append_code("TRACK1", "kill_switch_halt_new_entries", now)
        app.compliance.observe(app.execution_journal.confirmed_records(), now)
        app.state_journal.save(app.state.as_dict())
        app.position_store.save(app.position_manager.book)
        app.publish_status()
        return
```

Do not place this before `process_exits(now)`. Protective exits must still run.

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_deploy_artifacts.py tests/test_spot_runtime.py::test_kill_switch_blocks_entries_but_not_exits -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd midas
chmod +x deploy/twak-vps-bringup.sh
git add deploy/twak-vps-bringup.sh deploy/midas-agent.service src/magic_agent/app.py src/magic_agent/runner.py tests/test_deploy_artifacts.py tests/test_spot_runtime.py
git commit -m "ops: add twak vps bring-up and kill switch"
```

## Task 0F: Supervised Live Canary Smoke Gate

**Files:**
- Modify: `midas/docs/track1-spot-runbook.md`
- Test: `midas/tests/test_smoke.py`

- [ ] **Step 1: Write failing runbook gate test**

Append to `midas/tests/test_smoke.py`:

```python
def test_runbook_has_supervised_live_canary_gate():
    from pathlib import Path

    text = Path("docs/track1-spot-runbook.md").read_text(encoding="utf-8")

    assert "Supervised live canary gate" in text
    assert "operator approval" in text
    assert "quote-only smoke" in text
    assert "confirmed and reconciled" in text
    assert "do not enable systemd" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd midas
uv run pytest tests/test_smoke.py::test_runbook_has_supervised_live_canary_gate -q
```

Expected: FAIL because the runbook lacks the exact supervised live canary gate.

- [ ] **Step 3: Add supervised gate to runbook**

Add this section to `midas/docs/track1-spot-runbook.md`:

```markdown
## Supervised live canary gate

Do not enable systemd or run autonomous live mode before this gate is complete.

Prerequisites:

- scanner dependency is a deployable git pin at `5f92552e8fdd688808e2709eefc176ab681b7f4f`
- `deploy/twak-vps-bringup.sh` has passed quote-only smoke on the VPS
- TWAK wallet address matches `WALLET_ADDRESS`
- BNB gas reserve and USDC/USDT trading capital are funded
- kill-switch file path is known
- operator approval is explicit for the first live canary

Canary observation sequence:

1. Start with `magic-agent run --executor twak --max-iters 1` under direct operator supervision.
2. Confirm a scanner-authorized setup exists.
3. Confirm RiskPolicy caps the first live entry to `canary_risk_fraction = 0.0025`.
4. Confirm TWAK quote is fresh and exact-size.
5. Confirm TWAK submit returns a transaction hash.
6. Confirm the chain receipt reaches the required confirmations.
7. Confirm balance-delta reconciliation books the position.
8. Confirm the position appears in status and journals as confirmed and reconciled.

If any step fails, stop autonomous activation. Do not promote to normal scoring mode.
```

- [ ] **Step 4: Run focused test**

Run:

```bash
cd midas
uv run pytest tests/test_smoke.py::test_runbook_has_supervised_live_canary_gate -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd midas
git add docs/track1-spot-runbook.md tests/test_smoke.py
git commit -m "docs: add supervised live canary gate"
```

## Task 1: Live Mode State And Persistence

**Files:**
- Create: `midas/src/magic_agent/live_mode.py`
- Modify: `midas/src/magic_agent/runtime_state.py`
- Test: `midas/tests/test_live_mode.py`

- [ ] **Step 1: Write the failing tests**

Add `midas/tests/test_live_mode.py`:

```python
from decimal import Decimal

from magic_agent.live_mode import LiveModeState
from magic_agent.runtime_state import RuntimeState


def test_live_mode_initial_state_requires_canary():
    state = RuntimeState.new_session(Decimal("10000"))

    assert state.canary_mode is True
    assert state.live_mode_state()["mode"] == "canary"
    assert state.live_mode_state()["canary_required"] is True
    assert state.live_mode_state()["canary_completed"] is False


def test_live_mode_persists_canary_promotion_fields():
    state = RuntimeState.new_session(Decimal("10000"))
    state.canary_completed = True
    state.canary_mode = False
    state.canary_intent_id = "intent-1"
    state.canary_reconciled_at = "2026-06-22T12:00:00+00:00"
    state.promotion_reason = "canary_reconciled_cost_viable"
    state.normal_scoring_started_at = "2026-06-22T12:01:00+00:00"
    state.cost_viability_evidence = {
        "approved": True,
        "estimated_round_trip_cost_bps": "76",
    }

    restored = RuntimeState.from_dict(state.as_dict())

    assert restored.canary_mode is False
    assert restored.canary_completed is True
    assert restored.canary_intent_id == "intent-1"
    assert restored.canary_reconciled_at == "2026-06-22T12:00:00+00:00"
    assert restored.promotion_reason == "canary_reconciled_cost_viable"
    assert restored.normal_scoring_started_at == "2026-06-22T12:01:00+00:00"
    assert restored.cost_viability_evidence == {
        "approved": True,
        "estimated_round_trip_cost_bps": "76",
    }
    assert restored.live_mode_state()["mode"] == "normal_scoring"


def test_live_mode_from_legacy_state_defaults_to_canary():
    legacy = RuntimeState.new_session(Decimal("10000")).as_dict()
    for key in (
        "canary_completed",
        "canary_intent_id",
        "canary_reconciled_at",
        "promotion_reason",
        "normal_scoring_started_at",
        "cost_viability_evidence",
        "auto_promote_after_canary",
    ):
        legacy.pop(key, None)

    restored = RuntimeState.from_dict(legacy)

    assert restored.canary_mode is True
    assert restored.canary_completed is False
    assert restored.auto_promote_after_canary is True
    assert restored.live_mode_state()["mode"] == "canary"


def test_live_mode_state_reports_halted_review_when_exposure_blocked():
    state = RuntimeState.new_session(Decimal("10000"))
    state.blocks_new_exposure = True

    assert state.live_mode_state()["mode"] == "halted_review"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_live_mode.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'magic_agent.live_mode'` or `AttributeError` for missing `RuntimeState` fields.

- [ ] **Step 3: Add live mode model**

Create `midas/src/magic_agent/live_mode.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


LiveMode = Literal["canary", "normal_scoring", "halted_review"]


@dataclass(frozen=True)
class LiveModeState:
    mode: LiveMode
    canary_required: bool
    canary_completed: bool
    canary_intent_id: str | None
    canary_reconciled_at: str | None
    promotion_reason: str | None
    normal_scoring_started_at: str | None
    cost_viability_evidence: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "canary_required": self.canary_required,
            "canary_completed": self.canary_completed,
            "canary_intent_id": self.canary_intent_id,
            "canary_reconciled_at": self.canary_reconciled_at,
            "promotion_reason": self.promotion_reason,
            "normal_scoring_started_at": self.normal_scoring_started_at,
            "cost_viability_evidence": self.cost_viability_evidence,
        }
```

- [ ] **Step 4: Extend RuntimeState persistence**

Modify `midas/src/magic_agent/runtime_state.py`:

```python
from typing import Any
```

Add fields to `RuntimeState` after `blocks_new_exposure`:

```python
    canary_completed: bool = False
    canary_intent_id: str | None = None
    canary_reconciled_at: str | None = None
    promotion_reason: str | None = None
    normal_scoring_started_at: str | None = None
    cost_viability_evidence: dict[str, Any] | None = None
    auto_promote_after_canary: bool = True
```

Extend `_BOOL_FIELDS`:

```python
_BOOL_FIELDS: tuple[str, ...] = (
    "equity_fresh",
    "reconciled_position",
    "canary_mode",
    "blocks_new_exposure",
    "canary_completed",
    "auto_promote_after_canary",
)
```

Add `_OPTIONAL_STRING_FIELDS` near the field constants:

```python
_OPTIONAL_STRING_FIELDS: tuple[str, ...] = (
    "canary_intent_id",
    "canary_reconciled_at",
    "promotion_reason",
    "normal_scoring_started_at",
)
```

Add this method to `RuntimeState`:

```python
    def live_mode_state(self) -> dict:
        """Project canary/scoring mode into the status/dashboard contract."""
        if self.blocks_new_exposure:
            mode = "halted_review"
        elif self.canary_mode:
            mode = "canary"
        else:
            mode = "normal_scoring"
        return {
            "mode": mode,
            "canary_required": self.canary_mode,
            "canary_completed": self.canary_completed,
            "canary_intent_id": self.canary_intent_id,
            "canary_reconciled_at": self.canary_reconciled_at,
            "promotion_reason": self.promotion_reason,
            "normal_scoring_started_at": self.normal_scoring_started_at,
            "cost_viability_evidence": self.cost_viability_evidence,
        }
```

In `RuntimeState.as_dict`, after boolean fields:

```python
        for name in _OPTIONAL_STRING_FIELDS:
            payload[name] = getattr(self, name)
        payload["cost_viability_evidence"] = self.cost_viability_evidence
```

In `RuntimeState.from_dict`, replace the boolean loop with legacy-safe reads:

```python
        for name in _BOOL_FIELDS:
            if name in data:
                kwargs[name] = bool(data[name])
        for name in _OPTIONAL_STRING_FIELDS:
            kwargs[name] = data.get(name)
        kwargs["cost_viability_evidence"] = data.get("cost_viability_evidence")
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_live_mode.py -q
```

Expected: PASS.

- [ ] **Step 6: Run runtime-state regressions**

Run:

```bash
cd midas
uv run pytest tests/test_runtime_state.py tests/test_app.py::test_build_app_restores_persisted_runtime_state_on_restart -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd midas
git add src/magic_agent/live_mode.py src/magic_agent/runtime_state.py tests/test_live_mode.py
git commit -m "feat: persist live canary mode state"
```

## Task 2: Cost Viability Gate

**Files:**
- Create: `midas/src/magic_agent/cost_viability.py`
- Test: `midas/tests/test_cost_viability.py`

- [ ] **Step 1: Write the failing tests**

Add `midas/tests/test_cost_viability.py`:

```python
from decimal import Decimal

from magic_agent.cost_viability import CostViabilityConfig, evaluate_cost_viability


def _quote(**changes):
    quote = {
        "output_qty": "10",
        "provider": "twak",
        "minimum_output": "9.95",
        "impact_bps": "12",
        "slippage_bps": "14",
        "expires_at": "2026-06-22T12:05:00Z",
        "gas_usd": "0.04",
        "fee_usd": "0.02",
        "notional_usd": "100",
    }
    quote.update(changes)
    return quote


def test_cost_viability_approves_reasonable_round_trip_costs():
    decision = evaluate_cost_viability(
        buy_quote=_quote(),
        sell_quote=_quote(impact_bps="10", slippage_bps="11"),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("150")),
    )

    assert decision.approved is True
    assert decision.denied_by == ()
    assert decision.evidence["estimated_round_trip_cost_bps"] == "59.00"


def test_cost_viability_fails_closed_on_missing_quote():
    decision = evaluate_cost_viability(
        buy_quote=None,
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert decision.approved is False
    assert "missing_buy_quote" in decision.denied_by


def test_cost_viability_fails_closed_on_malformed_quote():
    bad = _quote()
    bad.pop("notional_usd")

    decision = evaluate_cost_viability(
        buy_quote=bad,
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert decision.approved is False
    assert "buy_missing_notional_usd" in decision.denied_by


def test_cost_viability_fails_closed_on_expired_quote():
    decision = evaluate_cost_viability(
        buy_quote=_quote(expires_at="2026-06-22T11:59:59Z"),
        sell_quote=_quote(),
        intended_risk_fraction=Decimal("0.005"),
        now="2026-06-22T12:00:00Z",
    )

    assert decision.approved is False
    assert "buy_quote_expired" in decision.denied_by


def test_cost_viability_denies_costs_that_dominate_tiny_trade():
    decision = evaluate_cost_viability(
        buy_quote=_quote(gas_usd="2.50", fee_usd="2.50", notional_usd="20"),
        sell_quote=_quote(gas_usd="2.50", fee_usd="2.50", notional_usd="20"),
        intended_risk_fraction=Decimal("0.0025"),
        now="2026-06-22T12:00:00Z",
        config=CostViabilityConfig(max_round_trip_cost_bps=Decimal("150")),
    )

    assert decision.approved is False
    assert decision.denied_by == ("round_trip_cost_too_high",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_cost_viability.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'magic_agent.cost_viability'`.

- [ ] **Step 3: Implement cost viability**

Create `midas/src/magic_agent/cost_viability.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class CostViabilityConfig:
    max_round_trip_cost_bps: Decimal = Decimal("150")


@dataclass(frozen=True)
class CostViabilityDecision:
    approved: bool
    denied_by: tuple[str, ...]
    evidence: dict[str, Any]


_REQUIRED = {
    "output_qty",
    "minimum_output",
    "impact_bps",
    "slippage_bps",
    "expires_at",
    "gas_usd",
    "fee_usd",
    "notional_usd",
}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _decimal(value: object, code: str, denied: list[str]) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        denied.append(code)
        return Decimal("0")


def _validate_quote(name: str, quote: dict | None, now: str, denied: list[str]) -> dict[str, Decimal]:
    if quote is None:
        denied.append(f"missing_{name}_quote")
        return {}
    missing = sorted(_REQUIRED - set(quote))
    for field in missing:
        denied.append(f"{name}_missing_{field}")
    if missing:
        return {}
    try:
        if _parse_time(str(quote["expires_at"])) <= _parse_time(now):
            denied.append(f"{name}_quote_expired")
    except ValueError:
        denied.append(f"{name}_malformed_expires_at")
    values = {
        "output_qty": _decimal(quote["output_qty"], f"{name}_malformed_output_qty", denied),
        "minimum_output": _decimal(quote["minimum_output"], f"{name}_malformed_minimum_output", denied),
        "impact_bps": _decimal(quote["impact_bps"], f"{name}_malformed_impact_bps", denied),
        "slippage_bps": _decimal(quote["slippage_bps"], f"{name}_malformed_slippage_bps", denied),
        "gas_usd": _decimal(quote["gas_usd"], f"{name}_malformed_gas_usd", denied),
        "fee_usd": _decimal(quote["fee_usd"], f"{name}_malformed_fee_usd", denied),
        "notional_usd": _decimal(quote["notional_usd"], f"{name}_malformed_notional_usd", denied),
    }
    if values["output_qty"] <= 0:
        denied.append(f"{name}_nonpositive_output")
    if values["minimum_output"] <= 0:
        denied.append(f"{name}_nonpositive_minimum_output")
    if values["notional_usd"] <= 0:
        denied.append(f"{name}_nonpositive_notional")
    return values


def evaluate_cost_viability(
    *,
    buy_quote: dict | None,
    sell_quote: dict | None,
    intended_risk_fraction: Decimal,
    now: str,
    config: CostViabilityConfig | None = None,
) -> CostViabilityDecision:
    cfg = config or CostViabilityConfig()
    denied: list[str] = []
    buy = _validate_quote("buy", buy_quote, now, denied)
    sell = _validate_quote("sell", sell_quote, now, denied)
    if denied:
        return CostViabilityDecision(False, tuple(denied), {
            "approved": False,
            "intended_risk_fraction": str(intended_risk_fraction),
            "denied_by": tuple(denied),
        })
    notional = min(buy["notional_usd"], sell["notional_usd"])
    fixed_cost_bps = (buy["gas_usd"] + buy["fee_usd"] + sell["gas_usd"] + sell["fee_usd"]) / notional * Decimal("10000")
    variable_cost_bps = buy["impact_bps"] + buy["slippage_bps"] + sell["impact_bps"] + sell["slippage_bps"]
    round_trip = fixed_cost_bps + variable_cost_bps
    evidence = {
        "approved": round_trip <= cfg.max_round_trip_cost_bps,
        "intended_risk_fraction": str(intended_risk_fraction),
        "estimated_notional_usd": str(notional),
        "buy_impact_bps": str(buy["impact_bps"]),
        "sell_impact_bps": str(sell["impact_bps"]),
        "estimated_round_trip_cost_bps": f"{round_trip:.2f}",
        "max_round_trip_cost_bps": str(cfg.max_round_trip_cost_bps),
    }
    if round_trip > cfg.max_round_trip_cost_bps:
        return CostViabilityDecision(False, ("round_trip_cost_too_high",), evidence)
    return CostViabilityDecision(True, (), evidence)
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_cost_viability.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd midas
git add src/magic_agent/cost_viability.py tests/test_cost_viability.py
git commit -m "feat: add live cost viability gate"
```

## Task 3: Qualification Pace Monitor

**Files:**
- Create: `midas/src/magic_agent/qualification.py`
- Test: `midas/tests/test_qualification.py`

- [ ] **Step 1: Write the failing tests**

Add `midas/tests/test_qualification.py`:

```python
from datetime import datetime, timezone

from magic_agent.qualification import QualificationConfig, evaluate_qualification_pace


def test_qualification_pace_on_track():
    decision = evaluate_qualification_pace(
        completed_trade_count=2,
        now=datetime(2026, 6, 24, 0, 0, tzinfo=timezone.utc),
        config=QualificationConfig(
            minimum_trade_count=7,
            window_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
        ),
    )

    assert decision.behind_pace is False
    assert decision.required_by_now == 2
    assert decision.warning is None


def test_qualification_pace_behind_is_advisory_only():
    decision = evaluate_qualification_pace(
        completed_trade_count=0,
        now=datetime(2026, 6, 25, 0, 0, tzinfo=timezone.utc),
        config=QualificationConfig(
            minimum_trade_count=7,
            window_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
        ),
    )

    assert decision.behind_pace is True
    assert decision.required_by_now == 3
    assert decision.can_force_trade is False
    assert decision.warning == "minimum_trade_count_behind_pace"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_qualification.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'magic_agent.qualification'`.

- [ ] **Step 3: Implement qualification monitor**

Create `midas/src/magic_agent/qualification.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil


@dataclass(frozen=True)
class QualificationConfig:
    minimum_trade_count: int
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class QualificationPace:
    minimum_trade_count: int
    completed_trade_count: int
    required_by_now: int
    behind_pace: bool
    warning: str | None
    can_force_trade: bool = False

    def as_dict(self) -> dict:
        return {
            "minimum_trade_count": self.minimum_trade_count,
            "completed_trade_count": self.completed_trade_count,
            "required_by_now": self.required_by_now,
            "behind_pace": self.behind_pace,
            "warning": self.warning,
            "can_force_trade": self.can_force_trade,
        }


def evaluate_qualification_pace(
    *,
    completed_trade_count: int,
    now: datetime,
    config: QualificationConfig,
) -> QualificationPace:
    if config.minimum_trade_count < 0:
        raise ValueError("minimum_trade_count must not be negative")
    total_seconds = max(1.0, (config.window_end - config.window_start).total_seconds())
    elapsed_seconds = min(max(0.0, (now - config.window_start).total_seconds()), total_seconds)
    required = min(
        config.minimum_trade_count,
        ceil(config.minimum_trade_count * elapsed_seconds / total_seconds),
    )
    behind = completed_trade_count < required
    return QualificationPace(
        minimum_trade_count=config.minimum_trade_count,
        completed_trade_count=completed_trade_count,
        required_by_now=required,
        behind_pace=behind,
        warning="minimum_trade_count_behind_pace" if behind else None,
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_qualification.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd midas
git add src/magic_agent/qualification.py tests/test_qualification.py
git commit -m "feat: add qualification pace monitor"
```

## Task 4: Agent Narrative Journal

**Files:**
- Create: `midas/src/magic_agent/agent_narrative.py`
- Test: `midas/tests/test_agent_narrative.py`

- [ ] **Step 1: Write the failing tests**

Add `midas/tests/test_agent_narrative.py`:

```python
import json
from datetime import datetime, timezone
from decimal import Decimal

from magic_agent.agent_narrative import AgentNarrativeJournal, live_entry_reconciled_event
from magic_agent.risk_policy import RiskDecision
from magic_agent.spot_models import AuthorizedSetup


def test_agent_narrative_writes_reconciled_entry_event(tmp_path):
    path = tmp_path / "agent_narrative.jsonl"
    journal = AgentNarrativeJournal(path)
    setup = AuthorizedSetup.example(symbol="TRX/USDT", grade="B+", raw_grade="B+")
    risk = RiskDecision(
        approved=True,
        denied_by=None,
        reasons=(),
        risk_fraction=Decimal("0.0025"),
        risk_budget_usd=Decimal("2.50"),
        base_qty=Decimal("100"),
        final_qty=Decimal("99"),
    )

    journal.append(live_entry_reconciled_event(
        now=datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc),
        setup=setup,
        risk=risk,
        mode="canary",
        execution_state="RECONCILED",
        tx_hash="0xabc",
        reason="supervised_canary_reconciled",
    ))

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [{
        "event": "live_entry_reconciled",
        "execution": {
            "quote_provider": "twak",
            "signer": "twak",
            "state": "RECONCILED",
            "tx_hash": "0xabc",
        },
        "reason": "supervised_canary_reconciled",
        "risk": {
            "denied_by": [],
            "final_qty": "99",
            "mode": "canary",
            "risk_fraction": "0.0025",
        },
        "scanner": {
            "authorized": True,
            "campaign_dol": "120",
            "effective_grade": "B+",
            "entry": "100",
            "raw_grade": "B+",
            "structural_stop": "90",
        },
        "symbol": "TRX/USDT",
        "ts": "2026-06-22T12:00:00+00:00",
    }]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_agent_narrative.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'magic_agent.agent_narrative'`.

- [ ] **Step 3: Implement narrative journal**

Create `midas/src/magic_agent/agent_narrative.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class AgentNarrativeJournal:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, event: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

    def records(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip()]


def live_entry_reconciled_event(
    *,
    now: datetime,
    setup,
    risk,
    mode: str,
    execution_state: str,
    tx_hash: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "ts": now.isoformat(),
        "event": "live_entry_reconciled",
        "symbol": setup.symbol,
        "scanner": {
            "authorized": True,
            "raw_grade": setup.raw_grade,
            "effective_grade": setup.grade,
            "entry": str(setup.entry),
            "structural_stop": str(setup.structural_stop),
            "campaign_dol": str(setup.campaign_dol),
        },
        "risk": {
            "mode": mode,
            "risk_fraction": str(risk.risk_fraction),
            "final_qty": str(risk.final_qty),
            "denied_by": [] if risk.denied_by is None else [risk.denied_by],
        },
        "execution": {
            "signer": "twak",
            "quote_provider": "twak",
            "state": execution_state,
            "tx_hash": tx_hash,
        },
        "reason": reason,
    }
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_agent_narrative.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd midas
git add src/magic_agent/agent_narrative.py tests/test_agent_narrative.py
git commit -m "feat: add live agent narrative journal"
```

## Task 5: Wire Canary Promotion Into The Runtime Loop

**Files:**
- Modify: `midas/src/magic_agent/app.py`
- Modify: `midas/src/magic_agent/paper_adapter.py`
- Modify: `midas/src/magic_agent/quotes.py`
- Modify: `midas/src/magic_agent/runner.py`
- Test: `midas/tests/test_live_canary_integration.py`

- [ ] **Step 1: Write the failing tests**

Add `midas/tests/test_live_canary_integration.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from magic_agent.app import build_app
from magic_agent.runner import run_cycle
from magic_agent.spot_models import AuthorizedSetup


class _CostGate:
    def __init__(self, approved=True):
        self.approved = approved
        self.calls = 0

    def evaluate(self, *, buy_quote, sell_quote, intended_risk_fraction, now):
        self.calls += 1
        return SimpleNamespace(
            approved=self.approved,
            denied_by=() if self.approved else ("round_trip_cost_too_high",),
            evidence={
                "approved": self.approved,
                "estimated_round_trip_cost_bps": "76",
                "intended_risk_fraction": str(intended_risk_fraction),
            },
        )


def _authorizing_app(tmp_path, cost_gate):
    setup = AuthorizedSetup.example(grade="A", raw_grade="A")
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=SimpleNamespace(scan=lambda candidate: setup),
        gold_candidate_symbol="ZEC",
    )
    app.cost_viability = cost_gate
    return app


def test_reconciled_canary_promotes_to_normal_scoring(tmp_path):
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app = _authorizing_app(tmp_path, _CostGate(approved=True))

    run_cycle(app, now)

    assert app.state.canary_completed is True
    assert app.state.canary_mode is False
    assert app.state.canary_intent_id is not None
    assert app.state.promotion_reason == "canary_reconciled_cost_viable"
    assert app.state.cost_viability_evidence["estimated_round_trip_cost_bps"] == "76"
    assert app.state.live_mode_state()["mode"] == "normal_scoring"


def test_failed_cost_viability_blocks_promotion(tmp_path):
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app = _authorizing_app(tmp_path, _CostGate(approved=False))

    run_cycle(app, now)

    assert app.state.canary_completed is True
    assert app.state.canary_mode is True
    assert app.state.promotion_reason == "cost_viability_failed"
    assert app.state.cost_viability_evidence["approved"] is False


def test_normal_scoring_uses_grade_sizing_after_promotion(tmp_path):
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    app = _authorizing_app(tmp_path, _CostGate(approved=True))

    run_cycle(app, now)
    first_fraction = app.execution_coordinator.confirmed_records()[0].evidence["policy"].risk_fraction

    app.position_manager.book.clear()
    run_cycle(app, now)
    second_fraction = app.execution_coordinator.confirmed_records()[1].evidence["policy"].risk_fraction

    assert first_fraction == Decimal("0.0025")
    assert second_fraction == Decimal("0.005")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_live_canary_integration.py -q
```

Expected: FAIL with `AttributeError: 'App' object has no attribute 'cost_viability'` or assertion failures because promotion is not wired.

- [ ] **Step 3: Add cost gate adapter to App**

Modify `midas/src/magic_agent/app.py`.

Import:

```python
from magic_agent.cost_viability import evaluate_cost_viability
from magic_agent.agent_narrative import AgentNarrativeJournal, live_entry_reconciled_event
```

Add fields to `App`:

```python
    cost_viability: Any = None
    agent_narrative: AgentNarrativeJournal | None = None
```

Add methods to `App`:

```python
    def evaluate_cost_viability(self, *, buy_quote: dict | None, sell_quote: dict | None, intended_risk_fraction, now) -> Any:
        if self.cost_viability is not None:
            return self.cost_viability.evaluate(
                buy_quote=buy_quote,
                sell_quote=sell_quote,
                intended_risk_fraction=intended_risk_fraction,
                now=now,
            )
        return evaluate_cost_viability(
            buy_quote=buy_quote,
            sell_quote=sell_quote,
            intended_risk_fraction=intended_risk_fraction,
            now=now.isoformat(),
        )

    def after_entry_submission(self, *, intent, result: str, quote: dict | None, risk, now) -> None:
        if result != "RECONCILED":
            return
        if self.agent_narrative is not None:
            self.agent_narrative.append(live_entry_reconciled_event(
                now=now,
                setup=intent.setup,
                risk=risk,
                mode="canary" if self.state.canary_mode else "normal_scoring",
                execution_state=result,
                tx_hash=None,
                reason="supervised_canary_reconciled" if self.state.canary_mode else "live_entry_reconciled",
            ))
        if not self.state.canary_mode:
            return
        decision = self.evaluate_cost_viability(
            buy_quote=quote,
            sell_quote=quote,
            intended_risk_fraction=risk.risk_fraction,
            now=now,
        )
        self.state.canary_completed = True
        self.state.canary_intent_id = intent.intent_id
        self.state.canary_reconciled_at = now.isoformat()
        self.state.cost_viability_evidence = decision.evidence
        if decision.approved and self.state.auto_promote_after_canary:
            self.state.canary_mode = False
            self.state.promotion_reason = "canary_reconciled_cost_viable"
            self.state.normal_scoring_started_at = now.isoformat()
        else:
            self.state.promotion_reason = "cost_viability_failed" if not decision.approved else "manual_promotion_required"
```

In `build_app`, pass the journal:

```python
        agent_narrative=AgentNarrativeJournal(base / "agent_narrative.jsonl"),
```

- [ ] **Step 4: Add paper quote cost fields and policy evidence**

Modify `midas/src/magic_agent/quotes.py` in `PaperQuoteProvider._leg`. Add these fields to the returned quote dict:

```python
            "gas_usd": "0",
            "fee_usd": "0",
            "notional_usd": str(qty * entry),
```

Modify `midas/src/magic_agent/paper_adapter.py` in `PaperExecutionAdapter.submit`. Replace:

```python
        self.records.append(PaperExecutionRecord(
            intent=intent, quote=quote, evidence={"simulated": True},
        ))
```

With:

```python
        self.records.append(PaperExecutionRecord(
            intent=intent,
            quote=quote,
            evidence={"simulated": True, "policy": policy},
        ))
```

- [ ] **Step 5: Wire runner submission result**

Modify `midas/src/magic_agent/runner.py`.

Replace:

```python
            app.execution_coordinator.submit(
                decision.intent, quote=prepared.quote, policy=prepared.risk,
            )
```

With:

```python
            result = app.execution_coordinator.submit(
                decision.intent, quote=prepared.quote, policy=prepared.risk,
            )
            app.after_entry_submission(
                intent=decision.intent,
                result=result,
                quote=prepared.quote,
                risk=prepared.risk,
                now=now,
            )
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_live_canary_integration.py -q
```

Expected: PASS.

- [ ] **Step 7: Run paper-loop regressions**

Run:

```bash
cd midas
uv run pytest tests/test_app.py tests/test_spot_runtime.py tests/test_risk_policy.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd midas
git add src/magic_agent/app.py src/magic_agent/paper_adapter.py src/magic_agent/quotes.py src/magic_agent/runner.py tests/test_live_canary_integration.py
git commit -m "feat: promote reconciled canary to scoring mode"
```

## Task 6: Qualification Pace In Runtime And Status

**Files:**
- Modify: `midas/src/magic_agent/app.py`
- Modify: `midas/src/magic_agent/status.py`
- Test: `midas/tests/test_live_canary_integration.py`
- Test: `midas/tests/test_status.py`

- [ ] **Step 1: Write the failing tests**

Append to `midas/tests/test_live_canary_integration.py`:

```python
from magic_agent.qualification import QualificationConfig


def test_qualification_pace_is_advisory_and_cannot_force_trade(tmp_path):
    now = datetime(2026, 6, 25, 0, 0, tzinfo=timezone.utc)
    app = build_app(
        mode="paper",
        root_dir=tmp_path,
        scanner_gateway=SimpleNamespace(scan=lambda candidate: None),
    )
    app.qualification_config = QualificationConfig(
        minimum_trade_count=7,
        window_start=datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
    )

    run_cycle(app, now)

    assert app.qualification_pace["behind_pace"] is True
    assert app.qualification_pace["can_force_trade"] is False
    assert app.execution_coordinator.confirmed_records() == []
```

Append to `midas/tests/test_status.py`:

```python
def test_status_includes_live_mode_and_qualification():
    from decimal import Decimal
    from magic_agent.runtime_state import RuntimeState

    class Executor:
        def get_position(self):
            from magic_agent.models import PositionState
            return PositionState()

        def get_account(self, *, mark_price):
            from magic_agent.models import AccountState
            return AccountState(equity=10000.0, available=10000.0, currency="USDT")

    state = RuntimeState.new_session(Decimal("10000"))
    status = build_status(
        Executor(),
        symbol="ZEC/USDT",
        mode="paper",
        venue="paper",
        mark_price=0.0,
        starting_equity=10000.0,
        live_mode=state.live_mode_state(),
        qualification={
            "minimum_trade_count": 7,
            "completed_trade_count": 0,
            "required_by_now": 3,
            "behind_pace": True,
            "warning": "minimum_trade_count_behind_pace",
            "can_force_trade": False,
        },
    )

    assert status["live_mode"]["mode"] == "canary"
    assert status["qualification"]["behind_pace"] is True
    assert status["qualification"]["can_force_trade"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_live_canary_integration.py::test_qualification_pace_is_advisory_and_cannot_force_trade tests/test_status.py::test_status_includes_live_mode_and_qualification -q
```

Expected: FAIL because `App` has no qualification fields and `build_status` does not accept `live_mode` or `qualification`.

- [ ] **Step 3: Add qualification fields to App**

Modify `midas/src/magic_agent/app.py`.

Import:

```python
from magic_agent.qualification import QualificationConfig, evaluate_qualification_pace
```

Add fields to `App`:

```python
    qualification_config: QualificationConfig | None = None
    qualification_pace: dict | None = None
```

Add method:

```python
    def update_qualification_pace(self, now) -> None:
        if self.qualification_config is None:
            self.qualification_pace = None
            return
        completed = len(self.execution_journal.confirmed_records())
        self.qualification_pace = evaluate_qualification_pace(
            completed_trade_count=completed,
            now=now,
            config=self.qualification_config,
        ).as_dict()
        if self.qualification_pace["behind_pace"]:
            self.exclusion_journal.append_code("TRACK1", "minimum_trade_count_behind_pace", now)
```

In `build_app`, initialize:

```python
        qualification_config=None,
        qualification_pace=None,
```

- [ ] **Step 4: Update runner to record pace without forcing trades**

Modify `midas/src/magic_agent/runner.py`.

Before each `app.publish_status()` call, add:

```python
        app.update_qualification_pace(now)
```

At the end-of-cycle save path, add before `app.publish_status()`:

```python
    app.update_qualification_pace(now)
```

Do not use `qualification_pace` to alter scanner, RiskPolicy, or submission flow.

- [ ] **Step 5: Extend status contract**

Modify `midas/src/magic_agent/status.py`.

Change `build_status` signature:

```python
    live_mode: dict | None = None,
    qualification: dict | None = None,
```

Add keys to the returned dict:

```python
        "live_mode": live_mode,
        "qualification": qualification,
```

Modify `App.publish_status` call in `midas/src/magic_agent/app.py`:

```python
            live_mode=self.state.live_mode_state(),
            qualification=self.qualification_pace,
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_live_canary_integration.py::test_qualification_pace_is_advisory_and_cannot_force_trade tests/test_status.py::test_status_includes_live_mode_and_qualification -q
```

Expected: PASS.

- [ ] **Step 7: Run status/API regressions**

Run:

```bash
cd midas
uv run pytest tests/test_status.py tests/test_ui_status_contract.py tests/test_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd midas
git add src/magic_agent/app.py src/magic_agent/runner.py src/magic_agent/status.py tests/test_live_canary_integration.py tests/test_status.py
git commit -m "feat: surface qualification pace in runtime status"
```

## Task 7: Live TWAK Assembly Regression Gate

**Files:**
- Modify: `midas/src/magic_agent/app.py`
- Modify: `midas/src/magic_agent/cli.py`
- Test: `midas/tests/test_app.py`
- Test: `midas/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `midas/tests/test_app.py`:

```python
def test_build_app_twak_still_fails_closed_without_required_secrets(tmp_path, monkeypatch):
    for name in (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_KEYSTORE_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="missing live secret"):
        build_app(mode="twak", root_dir=tmp_path)


def test_build_app_twak_assembles_live_ports_and_canary_layer_with_fakes(tmp_path, monkeypatch):
    for name in (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_KEYSTORE_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    ):
        monkeypatch.setenv(name, "test-secret")

    fake_scanner = SimpleNamespace(scan=lambda candidate: None)
    fake_cmc = FixtureCmcClient()
    fake_frame_source = SimpleNamespace()
    fake_twak = SimpleNamespace(json=lambda args, timeout=60: {"success": True, "data": {}})
    app = build_app(
        mode="twak",
        root_dir=tmp_path,
        scanner_gateway=fake_scanner,
        cmc_client=fake_cmc,
        frame_source=fake_frame_source,
        twak_runner=fake_twak,
        live_rpc=SimpleNamespace(wallet_nonce=lambda: 1, wait_receipt=lambda tx: {"status": "0x1"}, confirmations=lambda receipt: 2),
        live_balances=SimpleNamespace(snapshot=lambda identity_key: {"stable": "999", "token": "1"}),
    )

    assert app.mode == "twak"
    assert app.execution_coordinator.__class__.__name__ == "ExecutionCoordinator"
    assert app.executability.__class__.__name__ == "ExecutabilityAdapter"
    assert app.state.canary_mode is True
    assert app.agent_narrative is not None
    assert hasattr(app, "evaluate_cost_viability")
    assert hasattr(app, "update_qualification_pace")
```

Append to `midas/tests/test_cli.py`:

```python
def test_cli_twak_mode_passes_executor_to_build_app(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from magic_agent import cli

    captured = {}

    def fake_build_app(*, mode, root_dir):
        captured["mode"] = mode
        captured["root_dir"] = root_dir
        return SimpleNamespace()

    def fake_run_live(app, *, clock, max_iters):
        captured["max_iters"] = max_iters
        return 1

    monkeypatch.setattr("magic_agent.app.build_app", fake_build_app)
    monkeypatch.setattr("magic_agent.live.run_live", fake_run_live)
    args = SimpleNamespace(executor="twak", max_iters=1, root_dir=tmp_path)

    cli._cmd_run(args)

    assert captured == {"mode": "twak", "root_dir": tmp_path, "max_iters": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd midas
uv run pytest tests/test_app.py::test_build_app_twak_still_fails_closed_without_required_secrets tests/test_app.py::test_build_app_twak_assembles_live_ports_and_canary_layer_with_fakes tests/test_cli.py::test_cli_twak_mode_passes_executor_to_build_app -q
```

Expected: FAIL if the Phase 0 live assembly or Tasks 1-6 canary/scoring layer did not leave a complete `mode="twak"` app surface.

- [ ] **Step 3: Harden live assembly regression surface**

Modify `midas/src/magic_agent/app.py`.

Ensure `_LIVE_REQUIRED_ENV` includes the wallet address:

```python
_LIVE_REQUIRED_ENV = (
    "TWAK_ACCESS_ID",
    "TWAK_HMAC_SECRET",
    "TWAK_KEYSTORE_PASSWORD",
    "BSC_RPC_URL",
    "CMC_API_KEY",
    "WALLET_ADDRESS",
)
```

Ensure `build_app(mode="twak")` still constructs the live `ExecutionCoordinator`, live `ExecutabilityAdapter`, `AgentNarrativeJournal`, canary state, cost-viability method, qualification method, kill-switch path, and `_LiveExecutionView` from Phase 0. Do not restore the old live stub exception, and do not require `live_execution_coordinator` or `live_executability` injection for normal live construction.

- [ ] **Step 4: Make CLI monkeypatch-friendly**

Modify `midas/src/magic_agent/cli.py` inside `_cmd_run`:

```python
    import magic_agent.app as app_module
    import magic_agent.live as live_module

    mode = "paper" if args.executor == "paper" else "twak"
    root_dir = getattr(args, "root_dir", None)
    app = app_module.build_app(mode=mode, root_dir=root_dir)
    clock = live_module.make_bar_aligned_clock()
    live_module.run_live(app, clock=clock, max_iters=getattr(args, "max_iters", None))
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd midas
uv run pytest tests/test_app.py::test_build_app_twak_still_fails_closed_without_required_secrets tests/test_app.py::test_build_app_twak_assembles_live_ports_and_canary_layer_with_fakes tests/test_cli.py::test_cli_twak_mode_passes_executor_to_build_app -q
```

Expected: PASS.

- [ ] **Step 6: Run CLI/App regressions**

Run:

```bash
cd midas
uv run pytest tests/test_app.py tests/test_cli.py tests/test_execution_coordinator.py tests/test_twak.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd midas
git add src/magic_agent/app.py src/magic_agent/cli.py tests/test_app.py tests/test_cli.py
git commit -m "feat: add fail-closed twak app assembly gate"
```

## Task 8: Runbook And Operator Smoke Gates

**Files:**
- Modify: `midas/docs/track1-spot-runbook.md`
- Test: `midas/tests/test_smoke.py`

- [ ] **Step 1: Write the failing documentation smoke test**

Append to `midas/tests/test_smoke.py`:

```python
def test_runbook_documents_live_canary_to_scoring_process():
    from pathlib import Path

    text = Path("docs/track1-spot-runbook.md").read_text(encoding="utf-8")

    assert "mandatory first live canary" in text
    assert "canary_risk_fraction = 0.0025" in text
    assert "promote to normal scoring mode" in text
    assert "minimum trade-count pace" in text
    assert "cost viability" in text
    assert "smart-money and LLM supervisor are deferred" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd midas
uv run pytest tests/test_smoke.py::test_runbook_documents_live_canary_to_scoring_process -q
```

Expected: FAIL because the runbook does not contain all required phrases.

- [ ] **Step 3: Update runbook**

Add this section to `midas/docs/track1-spot-runbook.md`:

```markdown
## Track 1 Live Canary To Scoring Run

The first `magic-agent run --executor twak` activation uses a mandatory first live canary. The canary is a supervised safety gate, not the scoring mode.

Canary rules:

- `canary_risk_fraction = 0.0025`
- scanner authorization is still required
- RiskPolicy approval is still required
- exact-size TWAK quote is required
- TWAK submission must confirm on BSC
- balance-delta reconciliation must book the position
- failed, timed-out, or `BROADCAST_UNKNOWN` canary attempts do not promote

Promotion rules:

- after a reconciled canary and passing cost viability, the runtime may promote to normal scoring mode
- normal scoring uses RiskPolicy sizing: A-family aligned up to 0.50%, B-family aligned up to 0.25%, counter-bias with the 0.50x multiplier
- hard-DQ, daily halt, drawdown throttle, concurrency cap, token cap, stable reserve, stale equity, and consecutive-stop halt remain active

Qualification:

- the runtime tracks minimum trade-count pace
- behind-pace warnings may increase operator attention or discovery urgency
- behind-pace status cannot force trades or bypass scanner authorization

Cost viability:

- before autonomous normal scoring mode, live quotes must show that gas, swap fees, slippage, and impact do not obviously dominate the intended order size
- malformed, expired, missing, or zero-output quote data fails closed

Agent narrative:

- the dashboard and journals should show observed -> scanned -> authorized or denied -> sized -> quoted -> signed -> reconciled -> monitored or exited
- smart-money and LLM supervisor are deferred from the live canary critical path
- future smart-money or LLM features must be advisory, non-blocking, and unable to alter scanner, stop, DOL, or RiskPolicy authority
```

- [ ] **Step 4: Run documentation smoke test**

Run:

```bash
cd midas
uv run pytest tests/test_smoke.py::test_runbook_documents_live_canary_to_scoring_process -q
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

Run:

```bash
cd midas
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd midas
git add docs/track1-spot-runbook.md tests/test_smoke.py
git commit -m "docs: document live canary scoring process"
```

## Final Review Checklist

- [ ] `uv run pytest -q` passes in `midas`.
- [ ] `magic-scanner` resolves from `https://github.com/degencodebeast/trading-scanner` at `5f92552e8fdd688808e2709eefc176ab681b7f4f`; no committed `file://` scanner source remains.
- [ ] `TwakQuoteProvider` returns exact-size quote fields required by `prepare_exact_order` and cost viability: `output_qty`, `minimum_output`, `impact_bps`, `slippage_bps`, `expires_at`, `gas_usd`, `fee_usd`, and `notional_usd`.
- [ ] `build_app(mode="twak")` constructs live TWAK quote, live execution coordinator, live sell ports, live frame source, and live execution journal view without requiring injected ports.
- [ ] `build_app(mode="twak")` fails closed when any required live secret is absent, including `WALLET_ADDRESS`.
- [ ] Live booking still flows only through receipt confirmation and balance-delta reconciliation; no optimistic booking path exists.
- [ ] Live protective exits use TWAK sell ports and run before kill-switch/new-entry gates.
- [ ] Live restart rebuilds open position truth from chain balances and reconciled execution evidence, not paper `positions.json`.
- [ ] `deploy/twak-vps-bringup.sh` is quote-only, assumes the existing registered wallet, does not create a wallet, and does not register competition unless `RUN_COMPETE_REGISTER=1`.
- [ ] `deploy/midas-agent.service` is a disabled skeleton with one `[Service]` section and an `EnvironmentFile=/etc/midas/agent.env` reference.
- [ ] Kill-switch file halts new entries but does not block protective exits.
- [ ] `RuntimeState.as_dict()` and `RuntimeState.from_dict()` preserve canary/scoring fields and remain legacy-safe.
- [ ] First live-capable entry is canary-sized through `MarketRiskContext(canary=True)`.
- [ ] A reconciled canary does not promote unless cost viability approves.
- [ ] After promotion, `state.canary_mode` is false and RiskPolicy uses normal grade-based sizing.
- [ ] Qualification pace can warn but has no code path into scanner authorization, RiskPolicy sizing, TWAK submission, or position booking.
- [ ] Protective exits still run before every new-entry gate.
- [ ] Agent narrative writes facts only; it does not invoke smart-money, LLM, or advisory calls.
- [ ] `magic-agent run --executor paper --max-iters 1` still exits 0.
- [ ] VPS operator-run quote-only smoke passes before any supervised live canary is approved.
- [ ] No real TWAK swap is executed by tests or automation.
- [ ] No smart-money or LLM supervisor is implemented in this plan.

## Execution Notes

- Use a fresh task branch or isolated worktree for execution.
- Commit after each task.
- For Task 7, Context7 did not provide exact Trust Wallet Agent Kit CLI documentation. Treat real TWAK argument shapes as unverified until a credentialed VPS smoke run confirms them. Keep all live signing behind `TwakRunner` and injected ports.
- Do not push scanner or MIDAS branches without explicit operator approval.
