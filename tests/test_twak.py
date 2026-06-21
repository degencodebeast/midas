from dataclasses import dataclass
from decimal import Decimal

from magic_agent.executability import prepare_exact_order, validate_round_trip
from magic_agent.risk_policy import (
    MarketRiskContext,
    PortfolioRiskState,
    QuantityCaps,
    RiskConfig,
    RiskPolicy,
)
from magic_agent.spot_models import AuthorizedSetup
from magic_agent.twak import TwakError, TwakRunner


@dataclass
class _Process:
    returncode: int
    stdout: str
    stderr: str = ""


def _run(returncode: int, stdout: str, stderr: str = ""):
    def invoke(command, **kwargs):
        return _Process(returncode, stdout, stderr)

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


def test_failure_path_redacts_secret_from_raised_error():
    secret = "hmac_DEADBEEFCAFE_FAKE_TOKEN"
    runner = TwakRunner(
        run=_run(2, "", f"auth failed for {secret}"),
    )
    try:
        runner.json(["wallet", "balance", "--password", secret, "--json"])
        assert False, "nonzero exit must raise"
    except TwakError as exc:
        assert secret not in str(exc)
        assert secret not in runner.last_redacted_command


def test_missing_impact_or_sell_route_fails_closed():
    buy = {
        "output_qty": "10",
        "provider": "rango",
        "minimum_output": "9.8",
        "slippage_bps": "20",
        "expires_at": "2026-06-21T00:01:00Z",
    }
    result = validate_round_trip(buy, None, now="2026-06-21T00:00:00Z")
    assert result.approved is False
    assert "missing_sell_quote" in result.reasons


def test_final_quote_is_refreshed_at_risk_sized_quantity():
    requested = []

    def quote_provider(setup, quantity):
        requested.append(quantity)
        return type(
            "Quote",
            (),
            {
                "approved": True,
                "reasons": (),
                "quantity_caps": QuantityCaps.unbounded(),
                "quote": {"quantity": None if quantity is None else str(quantity)},
            },
        )()

    prepared = prepare_exact_order(
        setup=AuthorizedSetup.example(),
        market=MarketRiskContext.aligned(),
        risk_state=PortfolioRiskState.example(),
        risk_policy=RiskPolicy(RiskConfig.defaults()),
        quote_provider=quote_provider,
    )
    assert prepared.approved
    assert requested[0] is None  # capacity discovery only
    assert requested[-1] == prepared.risk.final_qty
    assert Decimal(prepared.quote["quantity"]) == prepared.risk.final_qty
