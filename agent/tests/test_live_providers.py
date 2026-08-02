"""Live provider tests (ADR-012): opt-in — they call real network services.

Run explicitly, with real keys in the environment (repo-root `.env`):

    RUN_LIVE_TESTS=1 uv run pytest tests/test_live_providers.py -v

Never run in CI (cost, keys, network flakiness). Purpose: catch **provider
drift** — renamed fields, a model that stops following the JSON instructions
— which the defensive parsing everywhere else would otherwise degrade
silently (a bad explanation quietly becoming empty). Run them after bumping
`AGENT_MODEL_ID` or when a deployment reports degraded explanations.

Deliberately excludes KeeperHub: a live execution test would let
`RUN_LIVE_TESTS=1` accidentally submit a real onchain transaction. Verifying
the executor against Sepolia is a manual, deliberate act (see SETUP.md), not
something an opt-in pytest fixture should be able to trigger.
"""

import os

import pytest

from aiagent.domain.models import RawApproval, RiskTier, ScanAction

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="live provider tests are opt-in (RUN_LIVE_TESTS=1) — they hit real APIs",
)

# A real wallet with a live, GoPlus-flagged malicious approval at the time
# this was written — a stable fixture for the drift check as long as it
# stays unrevoked. If it starts failing, verify with a fresh curl before
# assuming provider drift: https://api.gopluslabs.io/api/v2/token_approval_security/1
_WALLET_WITH_KNOWN_FLAGGED_APPROVAL = "0x47ac0Fb4F2D84898e4D9E7b4DaB3C24507a6D503"


def test_goplus_returns_approvals_our_mapping_understands() -> None:
    from aiagent.adapters.goplus import GoPlusApprovalSource

    approvals = GoPlusApprovalSource().fetch_approvals(_WALLET_WITH_KNOWN_FLAGGED_APPROVAL, "1")

    assert approvals, "live GoPlus call returned no approvals for a wallet known to have some"
    for approval in approvals:
        assert approval.token_address.startswith("0x")
        assert approval.spender_address.startswith("0x")
    # Field-shape drift check: at least one entry should carry the signals
    # `classify_risk` reads (a GoPlus schema change would silently break this).
    assert all({"malicious_address", "is_open_source"} <= set(a.raw) for a in approvals)


def test_claude_threat_intel_explains_a_dangerous_approval() -> None:
    from aiagent.adapters.chat_model import make_chat_model
    from aiagent.adapters.llm import LlmThreatIntel
    from aiagent.config import Settings

    approval = RawApproval(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="USDC",
        spender_address="0xbad000000000000000000000000000000bad00",
        approved_amount="Unlimited",
        raw={"malicious_address": True, "malicious_behavior": ["phishing_activities"]},
    )
    assessment = LlmThreatIntel(make_chat_model(Settings.from_env(), max_tokens=256)).assess_many(
        [approval]
    )[0]

    # The tier is deterministic (ADR-058) regardless of the model; only the
    # explanation is the model's job, and it must not come back empty.
    assert assessment.tier == RiskTier.DANGEROUS
    assert assessment.explanation, "model returned no explanation"


def test_claude_policy_starts_a_fresh_scan_with_a_chain() -> None:
    from aiagent.adapters.chat_model import make_chat_model
    from aiagent.adapters.llm import LlmAgentPolicy
    from aiagent.config import Settings

    action = LlmAgentPolicy(make_chat_model(Settings.from_env(), max_tokens=256)).decide(
        "scan wallet 0xabc for risky token approvals (chains: 1,8453)", [], []
    )

    # Fresh loop, clear goal, nothing scanned: a sane policy scans.
    assert isinstance(action, ScanAction), f"expected a scan, got {action!r}"
    assert action.chain_id.strip()
    assert action.reason.strip()
