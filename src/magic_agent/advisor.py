"""Bounded LLM advisor (L5, optional stretch).

The advisor is ADVISORY ONLY. The deterministic scanner + decision layer are the
sole source of truth; this advisor may ONLY recommend *reducing size* or *waiting*,
with a short reason. The runner applies its output strictly through
``decision.clamp_advice`` (size-down / wait-only), so even a misbehaving or hallucinating
model can never invent, re-grade, flip, or up-size a trade.

Safety contract:
  * The system prompt is OPERATING DOCTRINE (how to operate trades), NOT the ICT
    strategy (the scanner owns that).
  * ``size_factor`` is clamped to [0, 1] here too (defense-in-depth; ``clamp_advice``
    clamps again downstream).
  * ANY failure — a raising client, non-JSON output, wrong shape, unknown action —
    returns ``None`` (= "no advice"), which keeps the pure deterministic path. The
    advisor NEVER raises.
  * The injected ``client`` callable does all I/O, so there is no mandatory runtime
    dependency and tests run fully offline with a fake client.
"""
from __future__ import annotations

import json
from typing import Callable

from magic_agent.decision import LlmAdvice
from magic_agent.models import ContextSnapshot, Setup

# Operating doctrine — how to OPERATE/risk-manage trades, not the trading strategy.
# Intentionally free of any ICT/strategy vocabulary (order blocks, FVGs, sweeps, ...);
# the deterministic scanner is the sole strategy author.
DEFAULT_DOCTRINE = (
    "You are a bounded execution and risk operator for an automated trading agent. "
    "The deterministic scanner is the source of truth for every trade: its symbol, "
    "direction, rating, entry, stop, and target are FINAL. You may ONLY recommend "
    "reducing size or waiting. You may NEVER invent trades, change direction, increase "
    "size, re-grade, or alter entries/stops/targets. If conditions look adverse you may "
    "trim size (size_factor < 1) or defer (action 'wait'); otherwise pass it through "
    "unchanged (action 'take', size_factor 1). "
    'Respond with STRICT JSON only: {"action": "take"|"wait", '
    '"size_factor": <number in 0..1>, "reasoning": "<one short sentence>"}.'
)

_VALID_ACTIONS = {"take", "wait"}


class LlmAdvisor:
    """Callable advisor: ``(setup, context) -> LlmAdvice | None``.

    ``client`` is an injected callable ``client(system_prompt, user_prompt) -> str``
    returning the model's raw text. All network/SDK details live behind it, so this
    class is pure parsing + bounding and never raises.
    """

    def __init__(
        self,
        client: Callable[[str, str], str],
        *,
        system_prompt: str = DEFAULT_DOCTRINE,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt

    def __call__(self, setup: Setup, context: ContextSnapshot) -> LlmAdvice | None:
        try:
            user_prompt = self._build_user_prompt(setup, context)
            raw = self._client(self._system_prompt, user_prompt)
            return self._parse(raw)
        except Exception:
            # Fail-safe: any error → no advice → deterministic path (no clamp).
            return None

    @staticmethod
    def _build_user_prompt(setup: Setup, context: ContextSnapshot) -> str:
        """Compact, factual prompt: the scanner Setup facts + the market context."""
        return (
            "Scanner setup (source of truth):\n"
            f"  symbol: {setup.symbol}\n"
            f"  direction: {setup.direction.value}\n"
            f"  rating: {setup.rating}\n"
            f"  entry: {setup.entry}\n"
            f"  stop_loss: {setup.stop_loss}\n"
            f"  take_profit: {setup.take_profit}\n"
            f"  confirmation: {setup.confirmation_kind}\n"
            "Market context:\n"
            f"  regime: {context.regime}\n"
            f"  risk_flag: {context.risk_flag}\n"
            f"  context_status: {context.status}\n"
            "Recommend ONLY size-down or wait. Respond with strict JSON."
        )

    @staticmethod
    def _parse(raw: object) -> LlmAdvice | None:
        if not isinstance(raw, str):
            return None
        data = json.loads(raw)  # may raise -> caught by __call__ -> None
        if not isinstance(data, dict):
            return None

        action = data.get("action")
        if action not in _VALID_ACTIONS:
            return None  # unknown/missing action → no advice (fail-safe)

        # size_factor clamped to [0, 1] (defense-in-depth). Missing → 1.0 (no change).
        raw_factor = data.get("size_factor", 1.0)
        try:
            factor = float(raw_factor)
        except (TypeError, ValueError):
            return None
        factor = min(1.0, max(0.0, factor))

        reasoning = data.get("reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        return LlmAdvice(action_hint=action, size_factor=factor, reasoning=reasoning)
