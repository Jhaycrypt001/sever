"""LLM observability spans (ADR-029 amendment): each adapter call opens a span
tagged with the OpenTelemetry GenAI conventions and the decision outcome. Driven
with an in-memory exporter and a fake chat model — no provider, no paid call."""

import httpx
import pytest
import respx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aiagent.adapters.keeperhub import KeeperHubApprovalRevoker
from aiagent.adapters.llm import ActionReply, ExplanationReply, LlmAgentPolicy, LlmThreatIntel
from aiagent.domain.models import ApprovalFinding, RawApproval, RiskTier

#: The wallet the KeeperHub key executes as (ADR-065): revocation is refused
#: for any other, so this test must present a matching one.
WALLET = "0xe13ed979bc6b23d6d9608939051e9488e9f304bf"


class FakeChat:
    """Structured-output fake carrying usage_metadata, so the span records real
    token counts. `raw` quacks like an AIMessage (has .content + usage_metadata)."""

    def __init__(self, parsed: object, input_tokens: int = 11, output_tokens: int = 5) -> None:
        self.parsed = parsed
        self.content = ""
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}

    def with_structured_output(self, schema: object, include_raw: bool = False) -> "FakeChat":
        return self

    def invoke(self, prompt: object) -> dict:
        return {"raw": self, "parsed": self.parsed}

    def batch(self, prompts: list, config: dict | None = None) -> list[dict]:
        return [self.invoke(p) for p in prompts]


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    # First (and only) provider set by the suite; the module tracer's proxy
    # resolves to it lazily, so spans created afterwards are captured.
    trace.set_tracer_provider(provider)
    return exp


def _attrs(exporter: InMemorySpanExporter, name: str) -> dict:
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(spans) == 1, f"expected one {name!r} span, got {len(spans)}"
    return dict(spans[0].attributes or {})


def an_approval() -> RawApproval:
    return RawApproval(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address="0xspender",
        approved_amount="Unlimited",
    )


def test_decide_span_carries_genai_usage_and_the_action(exporter: InMemorySpanExporter) -> None:
    exporter.clear()
    policy = LlmAgentPolicy(
        FakeChat(ActionReply(action="scan", chain_id="1", reason="r")),  # type: ignore[arg-type]
        model="claude-opus-4-8",
        system="anthropic",
    )
    policy.decide("goal", [], [])

    attrs = _attrs(exporter, "llm decide")
    assert attrs["gen_ai.operation.name"] == "decide"
    assert attrs["gen_ai.system"] == "anthropic"
    assert attrs["gen_ai.request.model"] == "claude-opus-4-8"
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert attrs["gen_ai.usage.output_tokens"] == 5
    assert attrs["aiagent.agent.action"] == "scan"


def test_assess_span_sums_batch_usage(exporter: InMemorySpanExporter) -> None:
    exporter.clear()
    threat_intel = LlmThreatIntel(
        FakeChat(  # type: ignore[arg-type]
            ExplanationReply(explanation="looks fine"), input_tokens=4, output_tokens=2
        ),
    )
    threat_intel.assess_many([an_approval(), an_approval(), an_approval()])

    attrs = _attrs(exporter, "llm assess")
    assert attrs["aiagent.llm.batch_size"] == 3
    assert attrs["gen_ai.usage.input_tokens"] == 12  # 3 approvals * 4
    assert attrs["gen_ai.usage.output_tokens"] == 6  # 3 approvals * 2


@respx.mock
def test_keeperhub_revoke_span_carries_the_finding_and_outcome(
    exporter: InMemorySpanExporter,
) -> None:
    exporter.clear()
    # ADR-065: the adapter reads its delegated wallet before executing.
    respx.get("https://app.keeperhub.com/api/user").mock(
        return_value=httpx.Response(200, json={"walletAddress": WALLET})
    )
    respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "exec-1", "status": "pending"})
    )
    respx.get("https://app.keeperhub.com/api/execute/exec-1/status").mock(
        return_value=httpx.Response(
            200, json={"status": "completed", "transactionHash": "0xabc123"}
        )
    )
    revoker = KeeperHubApprovalRevoker(
        "https://app.keeperhub.com", "kh_test_key", poll_interval_seconds=0
    )

    revoker.revoke(
        ApprovalFinding(
            chain_id="1",
            token_address="0xtoken",
            token_symbol="USDC",
            spender_address="0xbad",
            approved_amount="Unlimited",
            tier=RiskTier.DANGEROUS,
        ),
        WALLET,
    )

    attrs = _attrs(exporter, "keeperhub revoke")
    assert attrs["aiagent.chain_id"] == "1"
    assert attrs["aiagent.spender_address"] == "0xbad"
    assert attrs["aiagent.tier"] == "dangerous"
    assert attrs["aiagent.revocation_status"] == "revoked"
