# tests/test_advisor.py
"""L5: bounded LLM advisor — operating-doctrine prompt, clamped, fail-safe.

The advisor is ADVISORY ONLY: it reaches the decision solely through
``clamp_advice`` (wired in the runner), which guarantees size-down/wait-only. These
tests inject a FAKE client (no network) and prove:
  * a valid "take" / "wait" response parses into the right ``LlmAdvice``
  * an out-of-range ``size_factor`` is clamped into [0, 1] (defense-in-depth)
  * garbage / invalid JSON / a raising client → ``None`` (never raises)
  * the user prompt carries the scanner Setup + context facts
  * the system prompt is OPERATING DOCTRINE (not the ICT strategy)
"""
import json

from magic_agent.advisor import DEFAULT_DOCTRINE, LlmAdvisor
from magic_agent.decision import LlmAdvice
from magic_agent.models import ContextSnapshot, Setup, Side


def _setup() -> Setup:
    return Setup("BNB/USDT", Side.LONG, "A", "risk_on",
                 600.0, 588.0, 636.0, "chained_scob")


def _context() -> ContextSnapshot:
    return ContextSnapshot(regime="risk_off", risk_flag="elevated", status="ok")


class _FakeClient:
    """Records prompts, returns a canned string."""

    def __init__(self, response: str):
        self.response = response
        self.system_prompt = None
        self.user_prompt = None

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return self.response


def test_take_response_parses_into_llm_advice():
    client = _FakeClient(json.dumps(
        {"action": "take", "size_factor": 0.5, "reasoning": "adverse regime, trim"}))
    advice = LlmAdvisor(client)(_setup(), _context())
    assert isinstance(advice, LlmAdvice)
    assert advice.action_hint == "take"
    assert advice.size_factor == 0.5
    assert advice.reasoning == "adverse regime, trim"


def test_wait_response_parses_into_llm_advice():
    client = _FakeClient(json.dumps(
        {"action": "wait", "size_factor": 1.0, "reasoning": "no edge right now"}))
    advice = LlmAdvisor(client)(_setup(), _context())
    assert isinstance(advice, LlmAdvice)
    assert advice.action_hint == "wait"


def test_size_factor_above_one_is_clamped_to_one():
    client = _FakeClient(json.dumps(
        {"action": "take", "size_factor": 5.0, "reasoning": "tries to size up"}))
    advice = LlmAdvisor(client)(_setup(), _context())
    assert advice is not None
    assert advice.size_factor == 1.0  # clamped — the LLM can never size up


def test_negative_size_factor_is_clamped_to_zero():
    client = _FakeClient(json.dumps(
        {"action": "take", "size_factor": -1.0, "reasoning": "negative"}))
    advice = LlmAdvisor(client)(_setup(), _context())
    assert advice is not None
    assert advice.size_factor == 0.0


def test_invalid_json_returns_none():
    advice = LlmAdvisor(_FakeClient("this is not json {{{"))(_setup(), _context())
    assert advice is None


def test_garbage_shape_returns_none():
    # Valid JSON, but not the expected object (missing/garbage action).
    advice = LlmAdvisor(_FakeClient(json.dumps([1, 2, 3])))(_setup(), _context())
    assert advice is None


def test_unknown_action_returns_none():
    advice = LlmAdvisor(_FakeClient(json.dumps(
        {"action": "buy_the_dip", "size_factor": 1.0})))(_setup(), _context())
    assert advice is None


def test_raising_client_returns_none_never_raises():
    def _boom(system_prompt, user_prompt):
        raise RuntimeError("network down")

    # Must NOT propagate — failures fall back to the deterministic path.
    advice = LlmAdvisor(_boom)(_setup(), _context())
    assert advice is None


def test_none_response_returns_none():
    advice = LlmAdvisor(_FakeClient(None))(_setup(), _context())
    assert advice is None


def test_user_prompt_carries_setup_and_context_facts():
    client = _FakeClient(json.dumps({"action": "take", "size_factor": 1.0}))
    LlmAdvisor(client)(_setup(), _context())
    prompt = client.user_prompt
    assert "BNB/USDT" in prompt          # symbol
    assert "long" in prompt.lower()      # direction
    assert "A" in prompt                 # rating
    assert "risk_off" in prompt          # regime
    assert "elevated" in prompt          # risk_flag


def test_default_doctrine_is_operating_doctrine_not_strategy():
    doc = DEFAULT_DOCTRINE.lower()
    # Operating doctrine: scanner is source of truth; only reduce/wait; strict JSON.
    assert "source of truth" in doc
    assert "size" in doc
    assert "wait" in doc
    assert "json" in doc
    # Must NOT encode the ICT strategy (the scanner owns that, not the advisor).
    for strategy_term in ("order block", "fair value gap", "liquidity sweep",
                          "breaker", "smart money"):
        assert strategy_term not in doc


def test_default_system_prompt_is_passed_to_client():
    client = _FakeClient(json.dumps({"action": "take", "size_factor": 1.0}))
    LlmAdvisor(client)(_setup(), _context())
    assert client.system_prompt == DEFAULT_DOCTRINE


def test_custom_system_prompt_is_used():
    client = _FakeClient(json.dumps({"action": "take", "size_factor": 1.0}))
    LlmAdvisor(client, system_prompt="custom doctrine")(_setup(), _context())
    assert client.system_prompt == "custom doctrine"
