"""Live Ollama test (ADR-041): opt-in — free, but needs a local server.

Run explicitly, with an Ollama server up and the model pulled:

    ollama pull qwen3:14b
    RUN_OLLAMA_TESTS=1 AGENT_LLM_BACKEND=ollama AGENT_MODEL_ID=qwen3:14b \
        uv run pytest tests/test_live_ollama.py -v

Never run in CI (needs a GPU-ish host and a pulled model). Purpose: the same
drift check as the paid live tests (ADR-012), for the local backend — a small
model that stops following the JSON instructions degrades silently through
the defensive parsing (the explanation becomes empty, the policy finishes
early). The risk tier itself is unaffected either way (ADR-058) — this test
exercises the explanation, not the tier.
"""

import os

import pytest

from aiagent.domain.models import RawApproval, RiskTier

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_TESTS") != "1",
    reason="Ollama live test is opt-in (RUN_OLLAMA_TESTS=1) — it needs a local server",
)


def test_local_model_explains_a_dangerous_approval() -> None:
    from aiagent.adapters.chat_model import make_chat_model
    from aiagent.adapters.llm import LlmThreatIntel
    from aiagent.config import Settings

    settings = Settings.from_env()
    assert settings.llm_backend == "ollama", "run with AGENT_LLM_BACKEND=ollama"

    approval = RawApproval(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="USDC",
        spender_address="0xbad000000000000000000000000000000bad00",
        approved_amount="Unlimited",
        raw={"malicious_address": True, "malicious_behavior": ["phishing_activities"]},
    )
    assessment = LlmThreatIntel(make_chat_model(settings, max_tokens=256)).assess_many([approval])[
        0
    ]

    # The bar is the same as for the hosted model: the tier is deterministic
    # (ADR-058) regardless of model quality; the explanation must not be empty.
    assert assessment.tier == RiskTier.DANGEROUS
    assert assessment.explanation, "local model returned no explanation"
