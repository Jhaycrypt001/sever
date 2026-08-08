"""Model evaluation harness (ADR-045/058): the scoring and runner are pure and
tested here with fakes — no paid call. The CLI (`main`) touches real providers
and is exercised by hand, like the live tests."""

import pytest

from aiagent.config import Settings
from aiagent.domain.models import FinishAction, RawApproval, RiskAssessment, ScanAction
from aiagent.evaluation import (
    ExplanationCase,
    PolicyCase,
    Report,
    _settings_for_spec,
    evaluate,
    format_table,
    score_explanation,
    score_policy,
)


def an_approval(spender: str = "0xspender") -> RawApproval:
    return RawApproval(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address=spender,
        approved_amount="Unlimited",
    )


# ---------------------------------------------------------------- scoring


def test_score_explanation_rewards_a_real_explanation() -> None:
    assert score_explanation("A known drainer contract with an unlimited approval.")[0] == 1.0


def test_score_explanation_penalizes_empty_or_absent() -> None:
    assert score_explanation(None)[0] == 0.0
    assert score_explanation("   ")[0] == 0.0


def test_score_explanation_penalizes_implausibly_long_output() -> None:
    assert score_explanation("x" * 500)[0] == 0.0


def test_score_policy_is_all_or_nothing() -> None:
    case = PolicyCase("c", "goal", [], [], expected_kind="scan")
    assert score_policy(case, ScanAction(chain_id="1", reason="r"))[0] == 1.0
    assert score_policy(case, FinishAction(reason="r"))[0] == 0.0


# ---------------------------------------------------------------- report


def test_report_aggregates_by_capability_and_overall() -> None:
    from aiagent.evaluation import CaseResult

    report = Report()
    report.results = [
        CaseResult("explanation", "a", 1.0, 0.1, ""),
        CaseResult("explanation", "b", 0.0, 0.2, ""),
        CaseResult("policy", "c", 1.0, 0.3, ""),
    ]
    assert report.capability_score("explanation") == 0.5
    assert report.capability_score("policy") == 1.0
    # Overall is the mean of the capabilities that ran (0.5, 1.0) = 0.75.
    assert report.overall() == 0.75
    assert report.total_latency() == pytest.approx(0.6)


# ---------------------------------------------------------------- runner


class FakeThreatIntel:
    def __init__(self, assessment: RiskAssessment) -> None:
        self._a = assessment

    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        return [self._a for _ in approvals]


class RaisingThreatIntel:
    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        raise RuntimeError("model exploded")


class FakePolicy:
    def decide(self, goal, steps, approvals):  # noqa: ANN001, ANN201
        return ScanAction(chain_id="1", reason="r")


def test_evaluate_runs_all_capabilities() -> None:
    threat_intel = FakeThreatIntel(RiskAssessment(explanation="Looks fine."))
    report = evaluate(threat_intel, FakePolicy())  # type: ignore[arg-type]
    caps = {r.capability for r in report.results}
    assert caps == {"explanation", "policy"}
    assert all(r.error is None for r in report.results)


def test_evaluate_turns_a_raised_error_into_a_zero_scored_result() -> None:
    report = evaluate(
        RaisingThreatIntel(),  # type: ignore[arg-type]
        FakePolicy(),  # type: ignore[arg-type]
        explanation_cases=[ExplanationCase("boom", an_approval())],
    )
    explanation_results = [r for r in report.results if r.capability == "explanation"]
    assert len(explanation_results) == 1
    assert explanation_results[0].score == 0.0
    assert "model exploded" in (explanation_results[0].error or "")


# ---------------------------------------------------------------- CLI helpers


def _base_settings() -> Settings:
    return Settings(
        redis_url="r",
        backend_internal_url="b",
        internal_api_token="t",
        agent_model_id="claude-opus-4-8",
        providers="live",
        scan_chain_ids=["1"],
        goplus_api_key="",
        goplus_app_secret="",
        keeperhub_api_url="https://app.keeperhub.com",
        keeperhub_api_key="kh_key",
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


def test_settings_for_spec_splits_on_the_first_colon() -> None:
    # Ollama tags contain a colon — the model id must keep it.
    s = _settings_for_spec("ollama:gemma4:latest", _base_settings())
    assert s.llm_backend == "ollama"
    assert s.agent_model_id == "gemma4:latest"


def test_settings_for_spec_rejects_a_spec_without_a_model() -> None:
    with pytest.raises(SystemExit):
        _settings_for_spec("ollama", _base_settings())


def test_format_table_lists_every_model_and_the_headers() -> None:
    from aiagent.evaluation import CaseResult

    report = Report(results=[CaseResult("explanation", "a", 1.0, 0.5, "")])
    table = format_table([("ollama:gemma4:latest", report, 0.0)])
    assert "MODEL" in table and "overall" in table
    assert "ollama:gemma4:latest" in table


# ---------------------------------------------------------------- gate


def _report_scoring(overall_pairs: list[tuple[str, float]]) -> Report:
    from aiagent.evaluation import CaseResult

    return Report(results=[CaseResult(cap, cap, score, 0.0, "") for cap, score in overall_pairs])


def test_failures_below_returns_models_under_the_bar() -> None:
    from aiagent.evaluation import failures_below

    passing = ("anthropic:good", _report_scoring([("explanation", 1.0), ("policy", 1.0)]), 0.0)
    failing = ("ollama:weak", _report_scoring([("explanation", 0.4), ("policy", 0.6)]), 0.0)
    msgs = failures_below([passing, failing], 0.8)
    assert len(msgs) == 1
    assert "ollama:weak" in msgs[0]
    assert "50%" in msgs[0]  # overall (0.4 + 0.6) / 2


def test_failures_below_is_empty_when_every_model_clears_the_bar() -> None:
    from aiagent.evaluation import failures_below

    rows = [("anthropic:good", _report_scoring([("explanation", 0.9), ("policy", 1.0)]), 0.0)]
    assert failures_below(rows, 0.8) == []
