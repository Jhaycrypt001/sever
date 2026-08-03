"""Agent metrics (ADR-050): the LLM adapters and the job path emit OTel
counters/histograms. Driven with an in-memory metric reader and a fake chat
model — no provider push, no paid call. No-op instruments (telemetry off) are
covered by every other test simply not crashing."""

import httpx
import respx
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from aiagent import metrics
from aiagent.adapters.keeperhub import KeeperHubApprovalRevoker
from aiagent.adapters.llm import ActionReply, LlmAgentPolicy
from aiagent.domain.models import ApprovalFinding, RiskTier

#: The wallet the KeeperHub key executes as (ADR-065): revocation is refused
#: for any other, so this test must present a matching one.
WALLET = "0xe13ed979bc6b23d6d9608939051e9488e9f304bf"


class FakeChat:
    def __init__(self, parsed: object, input_tokens: int = 11, output_tokens: int = 5) -> None:
        self.parsed = parsed
        self.content = ""
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}

    def with_structured_output(self, schema: object, include_raw: bool = False) -> "FakeChat":
        return self

    def invoke(self, prompt: object) -> dict:
        return {"raw": self, "parsed": self.parsed}


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    return [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


@respx.mock
def test_agent_metrics_are_recorded() -> None:
    reader = InMemoryMetricReader()
    # First (and only) meter provider the suite sets; the proxy instruments in
    # aiagent.metrics resolve to it lazily.
    otel_metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))

    LlmAgentPolicy(
        FakeChat(ActionReply(action="finish", reason="done")),  # type: ignore[arg-type]
        system="anthropic",
    ).decide("goal", [], [])
    metrics.record_job("completed", 0.25)

    # The LLM call latency histogram fired once, tagged with the operation.
    durations = _points(reader, "aiagent.llm.call.duration")
    assert any(p.attributes.get("operation") == "decide" for p in durations)

    # Token counter split by input/output, tagged with the model backend.
    tokens = _points(reader, "aiagent.llm.tokens")
    by_type = {p.attributes.get("type"): p.value for p in tokens}
    assert by_type["input"] == 11
    assert by_type["output"] == 5
    assert all(p.attributes.get("gen_ai.system") == "anthropic" for p in tokens)

    # The job outcome + attributed cost.
    assert any(p.attributes.get("outcome") == "completed" for p in _points(reader, "aiagent.jobs"))
    cost = _points(reader, "aiagent.job.cost")
    assert any(p.value == 0.25 and p.attributes.get("outcome") == "completed" for p in cost)

    # KeeperHub revocations (ADR-058 amendment): count + call-latency histogram.
    # ADR-065: the adapter reads its delegated wallet before executing.
    respx.get("https://app.keeperhub.com/api/user").mock(
        return_value=httpx.Response(200, json={"walletAddress": WALLET})
    )
    respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "exec-1", "status": "pending"})
    )
    respx.get("https://app.keeperhub.com/api/execute/exec-1/status").mock(
        return_value=httpx.Response(200, json={"status": "completed", "transactionHash": "0x1"})
    )
    KeeperHubApprovalRevoker(
        "https://app.keeperhub.com", "kh_test_key", poll_interval_seconds=0
    ).revoke(
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
    revocations = _points(reader, "aiagent.revocations")
    assert any(
        p.attributes.get("tier") == "dangerous" and p.attributes.get("outcome") == "revoked"
        for p in revocations
    )
    revocation_durations = _points(reader, "aiagent.revocation.call.duration")
    assert any(p.attributes.get("outcome") == "revoked" for p in revocation_durations)
