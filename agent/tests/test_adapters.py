import json

import httpx
import respx

from aiagent.adapters.llm import (
    ActionReply,
    ExplanationReply,
    LlmAgentPolicy,
    LlmThreatIntel,
    action_from_reply,
    parse_action,
    parse_explanation,
)
from aiagent.adapters.sink import HttpResultSink, serialize_result
from aiagent.domain.models import (
    AgentStep,
    AgentStepKind,
    ApprovalFinding,
    AskAction,
    FinishAction,
    RawApproval,
    RiskTier,
    ScanAction,
)

# ---------------------------------------------------------------- explanation parsing


def test_parse_explanation_full_payload() -> None:
    assert parse_explanation('{"explanation": "Known drainer contract."}') == (
        "Known drainer contract."
    )


def test_parse_explanation_tolerates_code_fences() -> None:
    fenced = '```json\n{"explanation": "Verified, low risk."}\n```'
    assert parse_explanation(fenced) == "Verified, low risk."


def test_parse_explanation_degrades_gracefully() -> None:
    # Prose, blank explanation: None, never an exception. The tier is
    # decided elsewhere (`classify_risk`) so a bad reply costs nothing but
    # the human-readable line.
    assert parse_explanation("not json at all") is None
    assert parse_explanation('["a", "list"]') is None
    assert parse_explanation('{"explanation": "  "}') is None


# ---------------------------------------------------------------- LlmThreatIntel


class FakeChatModel:
    """Stands in for the chat model in structured-output mode (ADR-043):
    records the prompt, replies with `parsed` (the validated schema instance —
    the happy path) or with `parsed=None` and raw `content` (native structured
    output failed → the adapter must fall back to text parsing). Prompt
    building, conversion and fallback parsing run for real (ADR-012)."""

    def __init__(self, content: object = "", parsed: object = None) -> None:
        self.content = content
        self.parsed = parsed
        self.prompts: list[str] = []
        self.batch_configs: list[dict | None] = []
        self.schema: object = None

    def with_structured_output(self, schema: object, include_raw: bool = False) -> "FakeChatModel":
        assert include_raw, "adapters must keep the raw message (usage metering, ADR-038)"
        self.schema = schema
        return self

    def invoke(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        # `raw` quacks like an AIMessage: has .content and no usage_metadata.
        return {"raw": self, "parsed": self.parsed}

    def batch(self, prompts: list[str], config: dict | None = None) -> list[dict]:
        self.batch_configs.append(config)
        return [self.invoke(p) for p in prompts]


def an_approval(symbol: str = "TKN", malicious: bool = False) -> RawApproval:
    # `is_open_source` is always present on the real GoPlus payload; include it
    # so the fixture represents a verified spender rather than one classify_risk
    # must treat as unauditable (which now degrades to WATCH — ADR-058).
    return RawApproval(
        chain_id="1",
        token_address="0xtoken",
        token_symbol=symbol,
        spender_address="0xspender",
        approved_amount="Unlimited",
        raw={"malicious_address": malicious, "is_open_source": 1},
    )


def test_threat_intel_converts_the_structured_reply() -> None:
    llm = FakeChatModel(parsed=ExplanationReply(explanation="Known drainer contract."))
    threat_intel = LlmThreatIntel(llm)  # type: ignore[arg-type]

    assessment = threat_intel.assess_many([an_approval(malicious=True)])[0]

    assert assessment.tier == RiskTier.DANGEROUS  # classify_risk, not the LLM
    assert assessment.explanation == "Known drainer contract."
    assert llm.schema is ExplanationReply


def test_threat_intel_falls_back_to_text_parsing_when_structuring_failed() -> None:
    llm = FakeChatModel(content='{"explanation": "Looks fine."}')
    assessment = LlmThreatIntel(llm).assess_many([an_approval()])[0]  # type: ignore[arg-type]

    assert assessment.explanation == "Looks fine."
    assert assessment.tier == RiskTier.SAFE


def test_threat_intel_never_lets_the_llm_pick_the_tier() -> None:
    # Even if the model's prose "argues" a different risk level, only
    # classify_risk's verified signals decide the tier (ADR-058).
    llm = FakeChatModel(parsed=ExplanationReply(explanation="This looks totally safe to me!"))
    assessment = LlmThreatIntel(llm).assess_many([an_approval(malicious=True)])[0]  # type: ignore[arg-type]

    assert assessment.tier == RiskTier.DANGEROUS


def test_threat_intel_batches_approvals_through_one_bounded_batch_call() -> None:
    # ADR-042: one llm.batch per approval set (concurrent under the hood),
    # bounded so a burst of findings cannot hammer the provider.
    llm = FakeChatModel(parsed=ExplanationReply(explanation="ok"))
    threat_intel = LlmThreatIntel(llm)  # type: ignore[arg-type]

    assessments = threat_intel.assess_many([an_approval("A"), an_approval("B"), an_approval("C")])

    assert len(assessments) == 3
    assert len(llm.batch_configs) == 1  # one batch, not three invokes
    assert llm.batch_configs[0] == {"max_concurrency": 5}


def test_threat_intel_meters_every_call_of_the_batch() -> None:
    from aiagent.domain.usage import UsageMeter

    llm = FakeChatModel(content="{}")
    meter = UsageMeter()
    LlmThreatIntel(llm, meter=meter).assess_many([an_approval(), an_approval()])  # type: ignore[arg-type]

    assert meter.snapshot().llm_calls == 2


def test_threat_intel_returns_nothing_for_no_approvals() -> None:
    llm = FakeChatModel(content="{}")
    assert LlmThreatIntel(llm).assess_many([]) == []  # type: ignore[arg-type]
    assert llm.batch_configs == []  # no pointless provider round-trip


# ---------------------------------------------------------------- http sink


def a_result() -> ApprovalFinding:
    return ApprovalFinding(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address="0xspender",
        approved_amount="Unlimited",
        tier=RiskTier.DANGEROUS,
        explanation="Known drainer contract.",
        raw={"k": "v"},
    )


@respx.mock
def test_sink_delivers_serialized_results_with_internal_token() -> None:
    route = respx.post("http://backend:8000/internal/jobs/job-1/results").mock(
        return_value=httpx.Response(204)
    )
    sink = HttpResultSink("http://backend:8000", "secret-token")

    sink.deliver("job-1", [a_result()])

    request = route.calls.last.request
    assert request.headers["x-internal-token"] == "secret-token"
    payload = json.loads(request.content)
    assert payload["results"] == [
        {
            "chain_id": "1",
            "token_address": "0xtoken",
            "token_symbol": "TKN",
            "spender_address": "0xspender",
            "spender_name": None,
            "approved_amount": "Unlimited",
            "tier": "dangerous",
            "malicious_behavior": [],
            "explanation": "Known drainer contract.",
            "is_new": True,
            "revocation_status": "not_attempted",
            "revocation_tx_hash": None,
            "raw": {"k": "v"},
        }
    ]


@respx.mock
def test_sink_marks_the_job_started() -> None:
    route = respx.post("http://backend:8000/internal/jobs/job-1/started").mock(
        return_value=httpx.Response(204)
    )
    sink = HttpResultSink("http://backend:8000", "secret-token")

    sink.mark_started("job-1")

    assert route.calls.last.request.headers["x-internal-token"] == "secret-token"


@respx.mock
def test_sink_propagates_the_correlation_id() -> None:
    route = respx.post("http://backend:8000/internal/jobs/job-1/started").mock(
        return_value=httpx.Response(204)
    )
    sink = HttpResultSink("http://backend:8000", "secret-token", request_id="corr-42")

    sink.mark_started("job-1")

    assert route.calls.last.request.headers["x-request-id"] == "corr-42"


@respx.mock
def test_sink_reports_failures() -> None:
    route = respx.post("http://backend:8000/internal/jobs/job-1/failure").mock(
        return_value=httpx.Response(204)
    )
    sink = HttpResultSink("http://backend:8000", "secret-token")

    sink.report_failure("job-1", "boom")

    assert json.loads(route.calls.last.request.content) == {"error": "boom"}


def test_serialize_result_revoked() -> None:
    result = ApprovalFinding(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address="0xspender",
        approved_amount="Unlimited",
        tier=RiskTier.DANGEROUS,
    )
    assert serialize_result(result)["revocation_tx_hash"] is None


# ---------------------------------------------------------------- agent policy (ADR-030/058)


def test_parse_action_scan_and_finish() -> None:
    assert parse_action('{"action": "scan", "chain_id": "1", "reason": "start"}') == (
        ScanAction(chain_id="1", reason="start")
    )
    assert parse_action('{"action": "finish", "reason": "coverage ok"}') == (
        FinishAction(reason="coverage ok")
    )


def test_parse_action_degrades_to_finish() -> None:
    # Anything malformed must stop the loop, never crash or burn budget.
    assert isinstance(parse_action("I think I should scan more"), FinishAction)
    assert isinstance(parse_action('{"action": "scan"}'), FinishAction)  # no chain_id
    assert isinstance(parse_action('{"action": "scan", "chain_id": "  "}'), FinishAction)
    assert isinstance(parse_action('["scan"]'), FinishAction)


def test_parse_action_tolerates_code_fences() -> None:
    fenced = '```json\n{"action": "scan", "chain_id": "1", "reason": "r"}\n```'
    assert parse_action(fenced) == ScanAction(chain_id="1", reason="r")


def test_llm_policy_shows_the_transcript_and_converts_the_decision() -> None:
    llm = FakeChatModel(parsed=ActionReply(action="scan", chain_id="8453", reason="start"))
    policy = LlmAgentPolicy(llm)  # type: ignore[arg-type]
    steps = [AgentStep(seq=1, kind=AgentStepKind.SCAN, detail="1", reason="r", new_hits=2)]

    action = policy.decide("scan wallet 0xabc", steps, [an_approval("USDC")])

    assert action == ScanAction(chain_id="8453", reason="start")
    prompt = llm.prompts[0]
    assert "Goal: scan wallet 0xabc" in prompt
    assert '- "1" -> 2 new' in prompt
    assert "- USDC -> 0xspender" in prompt


def test_llm_policy_falls_back_to_text_parsing_when_structuring_failed() -> None:
    llm = FakeChatModel(content='{"action": "scan", "chain_id": "8453", "reason": "start"}')
    action = LlmAgentPolicy(llm).decide("goal", [], [])  # type: ignore[arg-type]
    assert action == ScanAction(chain_id="8453", reason="start")


def test_action_reply_without_its_required_detail_degrades_to_finish() -> None:
    # A scan without a chain_id or an ask without a question must never crash
    # the loop — same guarantee as the text parser (ADR-030).
    assert isinstance(action_from_reply(ActionReply(action="scan", reason="r")), FinishAction)
    assert isinstance(action_from_reply(ActionReply(action="ask", reason="r")), FinishAction)
    ask = action_from_reply(ActionReply(action="ask", question="Which one?", reason="r"))
    assert isinstance(ask, AskAction) and ask.question == "Which one?"


def test_action_reply_is_case_tolerant_on_the_action() -> None:
    # Schema-mode models may capitalize the action ("Scan") — must still run.
    got = action_from_reply(ActionReply(action="Scan", chain_id="1", reason="go"))
    assert isinstance(got, ScanAction) and got.chain_id == "1"


@respx.mock
def test_sink_reports_agent_steps() -> None:
    route = respx.post("http://backend:8000/internal/jobs/job-1/steps").mock(
        return_value=httpx.Response(204)
    )
    sink = HttpResultSink("http://backend:8000", "secret")
    step = AgentStep(seq=1, kind=AgentStepKind.SCAN, detail="1", reason="start", new_hits=4)

    sink.report_step("job-1", step)

    assert json.loads(route.calls.last.request.content) == {
        "seq": 1,
        "kind": "scan",
        "detail": "1",
        "reason": "start",
        "new_hits": 4,
    }


# ---------------------------------------------------------------- clarification (ADR-032)


def test_parse_action_ask() -> None:
    assert parse_action('{"action": "ask", "question": "Which chains?", "reason": "r"}') == (
        AskAction(question="Which chains?", reason="r")
    )
    # A blank question is useless: degrade to finish, never burn the pause.
    assert isinstance(parse_action('{"action": "ask", "question": "  "}'), FinishAction)


@respx.mock
def test_sink_requests_clarification() -> None:
    route = respx.post("http://backend:8000/internal/jobs/job-1/question").mock(
        return_value=httpx.Response(204)
    )
    sink = HttpResultSink("http://backend:8000", "secret")

    sink.request_clarification("job-1", "Which chains?")

    assert json.loads(route.calls.last.request.content) == {"question": "Which chains?"}
