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
