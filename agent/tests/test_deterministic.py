"""Keyless production adapters (ADR-060). These are real adapters, not fakes:
they must classify from the same signals as the LLM path and must never
fabricate an outcome. The tests assert exactly that boundary."""

from aiagent.adapters.deterministic import DeterministicAgentPolicy, DeterministicThreatIntel
from aiagent.domain.models import AgentStep, AgentStepKind, FinishAction, RawApproval, RiskTier
from aiagent.domain.usage import UsageMeter


def approval(spender: str = "0xspender", raw: dict | None = None, amount: str = "Unlimited"):
    return RawApproval(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="USDC",
        spender_address=spender,
        approved_amount=amount,
        raw=raw if raw is not None else {"is_open_source": 1},
    )


def test_tier_comes_from_classify_risk_not_from_the_adapter() -> None:
    approvals = [
        approval("0xbad", {"malicious_address": 1, "is_open_source": 1}),
        approval("0xunverified", {"is_open_source": 0}),
        approval("0xok", {"is_open_source": 1}),
    ]

    tiers = [a.tier for a in DeterministicThreatIntel().assess_many(approvals)]

    assert tiers == [RiskTier.DANGEROUS, RiskTier.WATCH, RiskTier.SAFE]


def test_dangerous_explanation_cites_the_real_reported_behaviour() -> None:
    raw = {
        "malicious_address": 1,
        "is_open_source": 1,
        "malicious_behavior": ["phishing_activities"],
    }

    assessment = DeterministicThreatIntel().assess_many([approval("0xbad", raw)])[0]

    assert "phishing_activities" in (assessment.explanation or "")
    assert assessment.malicious_behavior == ("phishing_activities",)
    assert "unlimited allowance" in (assessment.explanation or "")


def test_watch_explanation_names_the_unverified_source() -> None:
    assessment = DeterministicThreatIntel().assess_many([approval("0xu", {"is_open_source": 0})])[0]

    explanation = assessment.explanation or ""
    assert "not published" in explanation
    assert "revoke manually" in explanation


def test_watch_explanation_calls_out_contradictory_trust_signals() -> None:
    raw = {
        "malicious_address": 1,
        "trust_list": 1,
        "is_open_source": 1,
        "malicious_behavior": ["x"],
    }

    assessment = DeterministicThreatIntel().assess_many([approval("0xrouter", raw)])[0]

    assert assessment.tier is RiskTier.WATCH
    assert "trust list" in (assessment.explanation or "")


def test_a_finite_allowance_is_described_with_its_amount() -> None:
    assessment = DeterministicThreatIntel().assess_many([approval(amount="250")])[0]

    assert "250 USDC" in (assessment.explanation or "")


def test_no_llm_spend_is_reported() -> None:
    # ADR-038: no model was called, so the run's cost must stay honest at zero.
    meter = UsageMeter()
    DeterministicThreatIntel(meter).assess_many([approval(), approval("0xb")])

    assert meter.snapshot().llm_calls == 0


def test_policy_walks_every_configured_chain_then_finishes() -> None:
    policy = DeterministicAgentPolicy(["1", "8453", "42161"])
    steps: list[AgentStep] = []
    scanned = []

    for _ in range(3):
        action = policy.decide("goal", steps, [])
        scanned.append(action.chain_id)  # type: ignore[union-attr]
        steps.append(
            AgentStep(
                seq=len(steps) + 1,
                kind=AgentStepKind.SCAN,
                detail=action.chain_id,  # type: ignore[union-attr]
                reason="r",
            )
        )

    assert scanned == ["1", "8453", "42161"]
    assert isinstance(policy.decide("goal", steps, []), FinishAction)


def test_policy_never_asks_a_clarification() -> None:
    # There is no model to interpret an answer, so asking would deadlock the
    # job in awaiting_input forever (ADR-032/060).
    policy = DeterministicAgentPolicy(["1"])
    steps: list[AgentStep] = []

    for _ in range(5):
        action = policy.decide("an ambiguous goal", steps, [])
        assert not isinstance(action, type(None))
        assert action.__class__.__name__ != "AskAction"
        steps.append(AgentStep(seq=len(steps) + 1, kind=AgentStepKind.SCAN, detail="1", reason="r"))
