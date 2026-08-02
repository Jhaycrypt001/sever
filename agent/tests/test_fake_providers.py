"""Fake providers and provider selection (ADR-021/058)."""

import pytest

from aiagent.adapters.fake import (
    FakeAgentPolicy,
    FakeApprovalRevoker,
    FakeApprovalSource,
    FakeThreatIntel,
)
from aiagent.application import run_scan
from aiagent.config import Settings
from aiagent.domain.models import (
    AgentStep,
    AgentStepKind,
    AskAction,
    FinishAction,
    RevocationStatus,
    RiskTier,
    ScanAction,
)
from aiagent.domain.ports import ApprovalRevoker
from aiagent.tasks import build_policy, build_providers, build_revoker


def settings_with(providers: str) -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        backend_internal_url="http://localhost:8000",
        internal_api_token="t",
        agent_model_id="claude-opus-4-8",
        providers=providers,
        scan_chain_ids=["1", "8453"],
        goplus_api_key="",
        keeperhub_api_url="https://app.keeperhub.com",
        keeperhub_api_key="",
        keeperhub_simulate_only=False,
        agent_max_steps=5,
        agent_max_cost_usd=2.0,
        agent_orchestrator="langgraph",
        llm_cost_input_per_mtok=5.0,
        llm_cost_output_per_mtok=25.0,
        search_cost_per_call=0.008,
        llm_backend="anthropic",
        llm_base_url="http://localhost:11434",
        llm_timeout_seconds=60.0,
        llm_max_retries=2,
        model_fallbacks=[],
    )


class NullSink:
    def mark_started(self, job_id: str) -> None: ...
    def deliver(self, job_id: str, results: list) -> None: ...
    def report_failure(self, job_id: str, error: str) -> None: ...


def test_build_providers_selects_fakes() -> None:
    source, threat_intel = build_providers(settings_with("fake"))
    assert isinstance(source, FakeApprovalSource)
    assert isinstance(threat_intel, FakeThreatIntel)


def test_build_providers_live_requires_credentials(monkeypatch) -> None:
    """The live path still fails fast without keys (covered by ADR-020 at
    worker startup; this guards the factory itself)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        build_providers(settings_with("live"))


def test_build_revoker_selects_the_fake() -> None:
    assert isinstance(build_revoker(settings_with("fake")), FakeApprovalRevoker)


def test_build_revoker_selects_keeperhub_live() -> None:
    from aiagent.adapters.keeperhub import KeeperHubApprovalRevoker

    revoker: ApprovalRevoker = build_revoker(settings_with("live"))
    assert isinstance(revoker, KeeperHubApprovalRevoker)


def test_fake_run_exercises_the_full_risk_cascade() -> None:
    """One deterministic run covers every risk tier (ADR-058) + sorting."""
    results = run_scan(
        "job-1", "0xwallet", ["1"], FakeApprovalSource(), FakeThreatIntel(), NullSink()
    )

    symbols = [r.token_symbol for r in results]
    assert symbols == ["fake-dangerous", "fake-watch", "fake-safe"]  # most dangerous first
    by_symbol = {r.token_symbol: r for r in results}
    assert by_symbol["fake-dangerous"].tier == RiskTier.DANGEROUS
    assert by_symbol["fake-watch"].tier == RiskTier.WATCH
    assert by_symbol["fake-safe"].tier == RiskTier.SAFE
    assert all(r.explanation for r in results)


def test_fake_source_is_deterministic() -> None:
    source = FakeApprovalSource()
    assert source.fetch_approvals("0xwallet", "1") == source.fetch_approvals("0xwallet", "1")


def test_fake_policy_scans_ethereum_then_base_then_finishes() -> None:
    policy = FakeAgentPolicy()
    first = policy.decide("scan wallet 0xabc", [], [])
    assert first == ScanAction(chain_id="1", reason="Start with Ethereum mainnet")

    one_step = [AgentStep(seq=1, kind=AgentStepKind.SCAN, detail="1", reason="r", new_hits=3)]
    second = policy.decide("scan wallet 0xabc", one_step, [])
    assert isinstance(second, ScanAction) and second.chain_id == "8453"

    two_steps = one_step + [
        AgentStep(seq=2, kind=AgentStepKind.SCAN, detail="8453", reason="r", new_hits=0)
    ]
    assert isinstance(policy.decide("scan wallet 0xabc", two_steps, []), FinishAction)


def test_build_policy_selects_the_fake(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDERS", "fake")
    assert isinstance(build_policy(Settings.from_env()), FakeAgentPolicy)


def test_fake_revoker_always_succeeds_with_a_synthetic_tx_hash() -> None:
    findings = run_scan(
        "job-1", "0xwallet", ["1"], FakeApprovalSource(), FakeThreatIntel(), NullSink()
    )
    dangerous = next(f for f in findings if f.tier == RiskTier.DANGEROUS)

    revoked = FakeApprovalRevoker().revoke(dangerous)

    assert revoked.revocation_status == RevocationStatus.REVOKED
    assert revoked.revocation_tx_hash is not None


def test_fake_policy_asks_once_on_an_ambiguous_goal() -> None:
    policy = FakeAgentPolicy()
    first = policy.decide("scan wallet 0xabc, ambiguous scope", [], [])
    assert isinstance(first, AskAction)

    # The task folds the answer into the goal on resume: no second question.
    resumed = policy.decide(
        'scan wallet 0xabc, ambiguous scope (user clarification: "ethereum only")', [], []
    )
    assert isinstance(resumed, ScanAction)
