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
