"""Fake providers and provider selection (ADR-021/058)."""

import re
from dataclasses import replace

from aiagent.adapters.fake import (
    ASK_SENTINEL,
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


def test_build_providers_live_without_an_llm_key_uses_the_deterministic_explainer(
    monkeypatch,
) -> None:
    """ADR-060: no key means templated explanations, not a broken pipeline.
    The approval source stays the real GoPlus adapter either way — only the
    prose degrades, never the scanning or the risk classification."""
    from aiagent.adapters.deterministic import DeterministicThreatIntel
    from aiagent.adapters.goplus import GoPlusApprovalSource

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source, threat_intel = build_providers(settings_with("live"))

    assert isinstance(source, GoPlusApprovalSource)
    assert isinstance(threat_intel, DeterministicThreatIntel)


def test_build_revoker_selects_the_fake() -> None:
    assert isinstance(build_revoker(settings_with("fake")), FakeApprovalRevoker)


def test_build_revoker_selects_keeperhub_live() -> None:
    from aiagent.adapters.keeperhub import KeeperHubApprovalRevoker

    revoker: ApprovalRevoker = build_revoker(settings_with("live"))
    assert isinstance(revoker, KeeperHubApprovalRevoker)


def test_build_revoker_prefers_the_jobs_own_key() -> None:
    """ADR-076: the scanning account's key executes as *their* delegated
    wallet. Without this the worker always executes as its environment
    wallet, and KeeperHub refuses every revocation for anyone else."""
    settings = settings_with("live")
    assert settings.keeperhub_api_key == ""

    revoker = build_revoker(settings, api_key="kh_owner")

    assert revoker._api_key == "kh_owner"  # noqa: SLF001 - asserting the wiring


def test_build_revoker_falls_back_to_the_environment_key() -> None:
    """Accounts that have not connected a key (and pre-ADR-076 backends,
    which send no key at all) keep the previous behaviour."""
    settings = replace(settings_with("live"), keeperhub_api_key="kh_env")

    revoker = build_revoker(settings, api_key=None)

    assert revoker._api_key == "kh_env"  # noqa: SLF001 - asserting the wiring


def test_build_revoker_ignores_a_blank_job_key() -> None:
    """An empty string is absence, not a key: it must not shadow the
    environment fallback and send KeeperHub an unauthenticated request."""
    settings = replace(settings_with("live"), keeperhub_api_key="kh_env")

    revoker = build_revoker(settings, api_key="   ")

    assert revoker._api_key == "kh_env"  # noqa: SLF001 - asserting the wiring


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

    revoked = FakeApprovalRevoker().revoke(dangerous, "0xwallet")

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


def test_fake_policy_asks_on_the_sentinel_wallet() -> None:
    # The "ambiguous" trigger above can only be reached from a unit test: a
    # dispatched goal is "scan wallet <address> ...", and no EVM address can
    # spell "ambiguous". The sentinel is valid hex, so the clarification path
    # (ADR-032) is reachable through the real API and the browser.
    goal = f"scan wallet {ASK_SENTINEL} for risky token approvals"
    assert isinstance(FakeAgentPolicy().decide(goal, [], []), AskAction)


def test_the_ask_sentinel_is_a_valid_evm_address() -> None:
    # If this ever stops holding, the console's address validation silently
    # makes the clarification e2e unreachable again.
    assert re.fullmatch(r"0x[0-9a-fA-F]{40}", ASK_SENTINEL)


def test_an_ordinary_wallet_never_pauses() -> None:
    goal = "scan wallet 0x1234567890123456789012345678901234567890 for risky token approvals"
    assert isinstance(FakeAgentPolicy().decide(goal, [], []), ScanAction)
