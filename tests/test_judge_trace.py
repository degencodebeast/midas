# tests/test_judge_trace.py
from magic_agent.judge_trace import judge_trace_report
from magic_agent.policy import PolicyConfig


def test_report_shows_pass_and_deny():
    cfg = PolicyConfig(max_leverage=5.0, max_daily_loss=50.0, require_stop=True)
    report = judge_trace_report(cfg)
    assert "PASS" in report and "DENY" in report
    assert "max_leverage" in report   # the cap-breaching intent names the tripped policy
    assert "zero funds" in report.lower() or "no funds" in report.lower()
