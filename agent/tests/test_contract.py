"""Cross-language contract fixtures (ADR-025): the agent side.

The agent PRODUCES the callback bodies (they must serialize to exactly the
fixtures the Rust backend consumes) and CONSUMES the task request (the fixture
the Rust dispatcher produces must parse).
"""

import json
from pathlib import Path

from aiagent.adapters.api.app import TaskRequest
from aiagent.adapters.sink import serialize_result, serialize_step, serialize_usage
from aiagent.domain.models import (
    AgentStep,
    AgentStepKind,
    ApprovalFinding,
    RevocationStatus,
    RiskTier,
)
from aiagent.domain.usage import Pricing, Usage

CONTRACTS = Path(__file__).parents[2] / "contracts"


def load(name: str) -> dict:
    # Explicit encoding (not the platform default): Path.read_text() without
    # one falls back to locale.getpreferredencoding(), which is cp1252 on a
    # default Windows setup — silently corrupting any non-ASCII fixture byte
    # (e.g. an em dash) instead of reading the UTF-8 the file actually is.
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_agent_produces_the_results_callback_exactly() -> None:
    results = [
        ApprovalFinding(
            chain_id="1",
            token_address="0x1111111111111111111111111111111111111a",
            token_symbol="USDC",
            spender_address="0xbad000000000000000000000000000000bad00",
            spender_name="Suspicious Proxy",
            approved_amount="Unlimited",
            tier=RiskTier.DANGEROUS,
            malicious_behavior=("phishing_activities",),
            explanation="This spender is a known malicious contract with an unlimited approval.",
            revocation_status=RevocationStatus.REVOKED,
            revocation_tx_hash="0xabc123",
            raw={"provider": "fixture"},
        ),
        ApprovalFinding(
            chain_id="8453",
            token_address="0x2222222222222222222222222222222222222b",
            token_symbol="WETH",
            spender_address="0xca1100000000000000000000000000000ca110",
            spender_name=None,
            approved_amount="1000",
            tier=RiskTier.WATCH,
            malicious_behavior=(),
            explanation=(
                "This spender contract is unverified — not confirmed malicious, but "
                "unable to audit."
            ),
            raw={"provider": "fixture"},
        ),
        ApprovalFinding(
            chain_id="1",
            token_address="0x3333333333333333333333333333333333333c",
            token_symbol="DAI",
            spender_address="0x5afe000000000000000000000000000005afe0",
            spender_name="Well-Known Router",
            approved_amount="50",
            tier=RiskTier.SAFE,
            explanation="This spender is a verified, low-risk contract.",
            raw={"provider": "fixture"},
        ),
    ]

    produced = {"results": [serialize_result(r) for r in results]}

    assert produced == load("results-callback.json")


def test_agent_produces_the_failure_callback_shape() -> None:
    # HttpResultSink.report_failure posts {"error": <str>} — same shape.
    fixture = load("failure-callback.json")
    assert set(fixture.keys()) == {"error"}
    assert isinstance(fixture["error"], str)


def test_agent_consumes_the_task_request_produced_by_the_backend() -> None:
    request = TaskRequest(**load("task-request.json"))
    assert request.job_id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert request.wallet_address == "0x1234567890123456789012345678901234567890"


def test_agent_produces_the_step_callback_exactly() -> None:
    step = AgentStep(
        seq=1,
        kind=AgentStepKind.SCAN,
        detail="1",
        reason="Start with Ethereum mainnet",
        new_hits=4,
    )
    assert serialize_step(step) == load("agent-step-callback.json")


def test_agent_consumes_the_task_request_mode() -> None:
    request = TaskRequest(**load("task-request.json"))
    assert request.mode == "agent"


def test_agent_consumes_the_task_request_clarification() -> None:
    request = TaskRequest(**load("task-request.json"))
    assert request.clarification is None  # first dispatch: no answer yet


def test_agent_consumes_the_task_request_recurring_memory() -> None:
    request = TaskRequest(**load("task-request.json"))
    # One-shot dispatch (ADR-033): not a recurring run, no memory.
    assert request.recurring is False
    assert request.seen_approval_keys == []


def test_agent_produces_the_question_callback_shape() -> None:
    # HttpResultSink.request_clarification posts {"question": <str>} (ADR-032).
    fixture = load("question-callback.json")
    assert set(fixture.keys()) == {"question"}
    assert isinstance(fixture["question"], str) and fixture["question"]


def test_agent_produces_the_usage_callback_exactly() -> None:
    # ADR-038: 8500 in-tokens * $5/MTok + 1200 out * $25/MTok + 2 * $0.008.
    usage = Usage(llm_calls=9, llm_input_tokens=8500, llm_output_tokens=1200, search_calls=2)
    pricing = Pricing(llm_input_per_mtok=5.0, llm_output_per_mtok=25.0, search_per_call=0.008)
    assert serialize_usage(usage, pricing) == load("usage-callback.json")
