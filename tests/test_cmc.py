# tests/test_cmc.py
"""P1.8-2: real env-gated CMC client SEAM (OBSERVE-ONLY; mapping deferred).

These tests inject FAKE fetchers/makers (no network) and prove:
  * the env-gated factory returns None when no API key (safe "not configured")
  * with a key, the factory wires the (injected) client_maker, honoring base_url
  * the client makes/observes a real F&G reading but returns a DELIBERATELY
    non-vetoing observe-only context (neutral/low) REGARDLESS of the reading,
    and LOGS the raw reading
  * integration: the observe-only snapshot feeds decision.gate as NON-VETOING
    (allow=True, size_multiplier=1.0) — proving observe-only does not gate trades
"""
from __future__ import annotations

import logging

from magic_agent.cli import _default_cmc_client_factory, _make_cmc_client
from magic_agent.context import CmcContextAdapter
from magic_agent.decision import gate
from magic_agent.models import ContextSnapshot, Setup, Side


def _fg_json(value, classification):
    """A sample CMC Fear & Greed `/v3/fear-and-greed/latest` response shape."""
    return {"data": {"value": value, "value_classification": classification}}


# ---------------------------------------------------------------------------
# _default_cmc_client_factory: env-gated wiring (no network)
# ---------------------------------------------------------------------------

def test_default_cmc_client_factory_no_key_returns_none(monkeypatch):
    """No API key in env => factory returns None => CmcContextAdapter(client=None)
    => 'unavailable' (honest 'CMC not configured'; the SAFE default). No network."""
    monkeypatch.delenv("MAGIC_AGENT_CMC_API_KEY", raising=False)

    def _exploding_maker(*, api_key, base_url):  # pragma: no cover - must not run
        raise AssertionError("client_maker called despite missing API key")

    assert _default_cmc_client_factory(client_maker=_exploding_maker) is None


def test_default_cmc_client_factory_with_key_wires_maker(monkeypatch):
    """Key present => factory calls the (injected) client_maker with api_key and
    returns its result — asserting wiring, NO network."""
    monkeypatch.setenv("MAGIC_AGENT_CMC_API_KEY", "cmc-test-key")
    monkeypatch.delenv("MAGIC_AGENT_CMC_BASE_URL", raising=False)

    captured = {}
    sentinel = object()

    def _fake_maker(*, api_key, base_url):
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return sentinel

    result = _default_cmc_client_factory(client_maker=_fake_maker)
    assert result is sentinel
    assert captured["api_key"] == "cmc-test-key"
    # base_url unset => the maker's default is used (factory passes no override)
    assert captured["base_url"] == "https://pro-api.coinmarketcap.com"


def test_default_cmc_client_factory_honors_base_url_override(monkeypatch):
    """MAGIC_AGENT_CMC_BASE_URL overrides the base_url passed to client_maker."""
    monkeypatch.setenv("MAGIC_AGENT_CMC_API_KEY", "cmc-test-key")
    monkeypatch.setenv("MAGIC_AGENT_CMC_BASE_URL", "https://example.test")

    captured = {}

    def _fake_maker(*, api_key, base_url):
        captured["base_url"] = base_url
        return object()

    _default_cmc_client_factory(client_maker=_fake_maker)
    assert captured["base_url"] == "https://example.test"


# ---------------------------------------------------------------------------
# _make_cmc_client: observe-only client (no network via injected fetch)
# ---------------------------------------------------------------------------

def test_make_cmc_client_returns_neutral_low_regardless_of_reading():
    """The observe-only client returns EXACTLY {"regime":"neutral","risk_flag":"low"}
    even for an 'Extreme Fear' reading — proving it does NOT map to a veto."""
    def _fake_fetch():
        return _fg_json(8, "Extreme Fear")

    client = _make_cmc_client(api_key="k", fetch=_fake_fetch)
    assert client("BNB/USDT") == {"regime": "neutral", "risk_flag": "low"}

    # Also for a greed reading — still neutral/low (no mapping at all).
    client2 = _make_cmc_client(api_key="k", fetch=lambda: _fg_json(92, "Extreme Greed"))
    assert client2("BNB/USDT") == {"regime": "neutral", "risk_flag": "low"}


def test_make_cmc_client_logs_raw_reading(caplog):
    """The client LOGS the raw F&G reading (the 'surface real CMC output' rule)."""
    client = _make_cmc_client(api_key="k", fetch=lambda: _fg_json(8, "Extreme Fear"))
    with caplog.at_level(logging.INFO, logger="magic_agent.cmc"):
        client("BNB/USDT")
    text = caplog.text
    assert "8" in text
    assert "Extreme Fear" in text


# ---------------------------------------------------------------------------
# Integration: observe-only snapshot is NON-VETOING through decision.gate
# ---------------------------------------------------------------------------

def test_make_cmc_client_fetch_error_degrades_to_unavailable():
    """A failing fetch (network error / timeout surface as a raise) must degrade to
    'unavailable' through the adapter — NEVER leak a spurious non-neutral context."""
    def _boom():
        raise RuntimeError("CMC fetch failed / timed out")

    client = _make_cmc_client(api_key="k", fetch=_boom)
    snap = CmcContextAdapter(client=client).get_context("BNB/USDT")
    assert snap == ContextSnapshot(regime="neutral", risk_flag="low", status="unavailable")


def test_make_cmc_client_malformed_payload_degrades_to_unavailable():
    """A malformed CMC payload (missing keys) must degrade to 'unavailable', not leak
    a non-neutral context (proves the veto-safety property at this seam)."""
    client = _make_cmc_client(api_key="k", fetch=lambda: {"unexpected": "shape"})
    snap = CmcContextAdapter(client=client).get_context("BNB/USDT")
    assert snap.status == "unavailable"


def test_adapter_with_observe_only_client_yields_ok_neutral_low():
    """CmcContextAdapter wrapping the observe-only client yields an 'ok' snapshot of
    neutral/low (so the gate sees a usable, non-vetoing context)."""
    client = _make_cmc_client(api_key="k", fetch=lambda: _fg_json(8, "Extreme Fear"))
    snap = CmcContextAdapter(client=client).get_context("BNB/USDT")
    assert snap == ContextSnapshot(regime="neutral", risk_flag="low", status="ok")


def test_observe_only_context_is_non_vetoing_in_gate():
    """Feed the observe-only snapshot to decision.gate with an A/B setup => the gate
    is NON-VETOING (allow=True, size_multiplier=1.0). Proves observe-only never gates."""
    client = _make_cmc_client(api_key="k", fetch=lambda: _fg_json(8, "Extreme Fear"))
    snap = CmcContextAdapter(client=client).get_context("BNB/USDT")

    a_setup = Setup("BNB/USDT", Side.LONG, "A", "risk_on",
                    600.0, 588.0, 636.0, "chained_scob")
    verdict_a = gate(a_setup, snap)
    assert verdict_a.allow is True
    assert verdict_a.size_multiplier == 1.0

    b_setup = Setup("BNB/USDT", Side.SHORT, "B", "risk_off",
                    600.0, 612.0, 564.0, "chained_scob")
    verdict_b = gate(b_setup, snap)
    assert verdict_b.allow is True
    assert verdict_b.size_multiplier == 1.0
