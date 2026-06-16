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
