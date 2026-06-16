# src/magic_agent/judge_trace.py
"""One-screen, zero-funds, zero-network proof that the policy engine works
(modelled on aegis scripts/judge-trace.mjs). Runs a canned PASSING intent and a
canned DENIED (cap-breaching) intent through the REAL run_policies and renders the
verdicts. The deterministic 'prove the safety story in 10 seconds' demo artifact."""
from __future__ import annotations

from magic_agent.models import Action, ExecutionIntent
from magic_agent.policy import PolicyState, run_policies, PolicyConfig

_PASS = ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 0.5, 600.0, 588.0, 636.0, leverage=3.0)
_DENY = ExecutionIntent("BNB/USDT", Action.ENTER_LONG, 0.5, 600.0, 588.0, 636.0, leverage=50.0)  # over leverage
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
