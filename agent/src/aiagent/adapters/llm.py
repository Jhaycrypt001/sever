"""LLM adapters: the ThreatIntel explainer (ADR-058) and the AgentPolicy
driving the agentic loop (ADR-030/058). Prompts, reply handling and usage
metering are provider-agnostic: the chat model itself (Anthropic API or a
local Ollama — ADR-041) is injected, built by `chat_model.make_chat_model`.

Replies use **native structured output** (ADR-043): each adapter binds a
pydantic reply schema via `with_structured_output` — tool calling on
Anthropic, grammar-constrained `json_schema` on Ollama — and converts the
validated reply into the domain type. When the native path yields nothing
(a model that ignored the tool, a validation failure), the raw text goes
through the legacy defensive parsers: the reply degrades, the job never
crashes.
"""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.trace import Span
from pydantic import BaseModel, Field

from aiagent import metrics
from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    AskAction,
    FinishAction,
    RawApproval,
    RiskAssessment,
    ScanAction,
    classify_risk,
)
from aiagent.domain.usage import UsageMeter

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel, LanguageModelInput


def usage_tokens(response: object) -> tuple[int, int]:
    """(input, output) token counts from langchain's usage_metadata; (0, 0)
    when absent (fake replies, older providers)."""
    usage = getattr(response, "usage_metadata", None) or {}
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def record_llm_usage(meter: "UsageMeter | None", response: object) -> None:
    """Reads langchain's usage_metadata (ADR-038); absent metadata still
    counts the call so fake replies and older providers stay visible."""
    if meter is None:
        return
    input_tokens, output_tokens = usage_tokens(response)
    meter.record_llm(input_tokens, output_tokens)


# ---------------------------------------------------------------- tracing (ADR-029 amendment)

# No-op when telemetry is off (the global provider is a proxy) — so the spans
# add nothing to the keyless demo/CI, and appear in Jaeger only when
# OTEL_EXPORTER_OTLP_ENDPOINT is set, as children of the worker's job span.
_tracer = trace.get_tracer("aiagent.llm")


@contextmanager
def llm_span(operation: str, model: str, system: str) -> Iterator[Span]:
    """One span per LLM call, tagged with the OpenTelemetry GenAI conventions
    (`gen_ai.*`). Latency is the span duration; callers add usage and the
    outcome. Model/system are best-effort labels; empty ones are skipped."""
    with _tracer.start_as_current_span(f"llm {operation}") as span:
        span.set_attribute("gen_ai.operation.name", operation)
        if system:
            span.set_attribute("gen_ai.system", system)
        if model:
            span.set_attribute("gen_ai.request.model", model)
        yield span


def record_span_usage(span: Span, input_tokens: int, output_tokens: int) -> None:
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)


def structured_with_fallbacks(models: list["BaseChatModel"], schema: type[BaseModel]) -> Any:
    """Binds structured output (ADR-043) on each model and chains them with
    LangChain fallbacks (ADR-052): the primary runs first; if it errors (provider
    down/quota, not a transient blip — those are the ADR-044 retries), the next
    model is tried, in order. A single model means no fallback wrapper, i.e. the
    exact previous behavior."""
    runnables = [model.with_structured_output(schema, include_raw=True) for model in models]
    head, *tail = runnables
    return head.with_fallbacks(tail) if tail else head


def split_structured(result: object) -> tuple[object, object]:
    """Splits an `include_raw=True` structured result into (raw message,
    parsed schema or None). Anything unexpected counts as unparsed."""
    if isinstance(result, dict):
        return result.get("raw"), result.get("parsed")
    return result, None


def raw_text(raw: object) -> str:
    """The raw message's text content, for the fallback parsers."""
    content = getattr(raw, "content", "")
    return content if isinstance(content, str) else str(content)


# ---------------------------------------------------------------- threat-intel explanation


EXPLANATION_PROMPT = """\
You explain, in one plain-English sentence, why an outstanding token approval
was classified as {tier} risk. You do NOT decide the risk tier — it is given,
already determined by verified threat-intel signals. Through the reply
schema, report:

- explanation: one factual sentence (max 30 words) a non-technical wallet
  owner would understand, grounded only in the facts given below — never
  invent details.

Token: {token_symbol} ({token_address})
Spender: {spender_address} ({spender_name})
Approved amount: {approved_amount}
Risk tier (already decided, do not second-guess it): {tier}
Known malicious behavior tags: {malicious_behavior}
Spender contract verified/open-source: {is_open_source}
"""


class ExplanationReply(BaseModel):
    """Structured explanation of one classified approval (ADR-043/058)."""

    explanation: str | None = Field(
        default=None,
        description="One factual sentence (max 30 words) explaining the risk tier",
    )


def parse_explanation(text: str) -> str | None:
    """Fallback parser (ADR-043): a malformed reply degrades to no
    explanation — the tier (already decided) is unaffected either way."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    try:
        payload = json.loads(cleaned)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    explanation = payload.get("explanation")
    return explanation.strip() if isinstance(explanation, str) and explanation.strip() else None


class LlmThreatIntel:
    """Live adapter — the model call itself is never exercised in CI
    (ADR-012). `llm` is injectable so the prompt/convert/fallback logic
    around it stays unit-testable with a fake chat model. The risk tier is
    always `classify_risk` (ADR-058); the LLM only ever fills `explanation`."""

    def __init__(
        self,
        llm: "BaseChatModel",
        meter: UsageMeter | None = None,
        concurrency: int = 5,
        model: str = "",
        system: str = "",
        fallbacks: "list[BaseChatModel] | None" = None,
    ) -> None:
        self._meter = meter
        self._structured = structured_with_fallbacks([llm, *(fallbacks or [])], ExplanationReply)
        # Bounds the parallel per-approval calls (ADR-042): fast, without
        # letting a burst of findings hammer the provider.
        self._concurrency = concurrency
        self._model = model
        self._system = system

    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        if not approvals:
            return []
        tiers = [classify_risk(a) for a in approvals]
        prompts: list[Any] = [
            EXPLANATION_PROMPT.format(
                token_symbol=a.token_symbol,
                token_address=a.token_address,
                spender_address=a.spender_address,
                spender_name=a.spender_name or "unnamed",
                approved_amount=a.approved_amount,
                tier=tier.value,
                malicious_behavior=", ".join(a.raw.get("malicious_behavior", [])) or "none",
                is_open_source=bool(a.raw.get("is_open_source", 1)),
            )
            for a, tier in zip(approvals, tiers, strict=True)
        ]
        with llm_span("assess", self._model, self._system) as span:
            span.set_attribute("aiagent.llm.batch_size", len(approvals))
            start = time.perf_counter()
            results = self._structured.batch(prompts, config={"max_concurrency": self._concurrency})
            duration = time.perf_counter() - start
            assessments = []
            input_tokens = output_tokens = 0
            for approval, tier, result in zip(approvals, tiers, results, strict=True):
                raw, parsed = split_structured(result)
                record_llm_usage(self._meter, raw)
                got_in, got_out = usage_tokens(raw)
                input_tokens += got_in
                output_tokens += got_out
                explanation = (
                    parsed.explanation
                    if isinstance(parsed, ExplanationReply) and parsed.explanation
                    else parse_explanation(raw_text(raw))
                )
                assessments.append(
                    RiskAssessment(
                        tier=tier,
                        malicious_behavior=tuple(approval.raw.get("malicious_behavior", [])),
                        explanation=explanation,
                    )
                )
            record_span_usage(span, input_tokens, output_tokens)
            metrics.record_llm_call("assess", self._system, duration, input_tokens, output_tokens)
            return assessments


# ---------------------------------------------------------------- policy


POLICY_PROMPT = """\
You are a wallet-security agent scanning a wallet's outstanding token
approvals across chains, one chain at a time. Decide your next action
through the reply schema: scan (another chain), finish, or — only if the
scan scope is genuinely ambiguous AND no clarification is present below —
ask the user ONE short question before scanning.

Rules: scan a chain not already covered; stop once every configured chain
has been scanned or a scan stops adding new findings. Never ask once a
clarification is present. The reason is one short sentence, shown to the
user as your journal.

Goal: {goal}

Chains scanned so far (chain_id -> new findings):
{transcript}

Findings collected so far ({count}):
{summary}
"""


class ActionReply(BaseModel):
    """The policy's next action (ADR-030/043/058)."""

    action: str = Field(
        description='"scan" to scan another chain, "finish" to stop, '
        '"ask" to ask the user one clarifying question'
    )
    reason: str | None = Field(
        default=None,
        description="One short sentence explaining the decision, shown to the user",
    )
    chain_id: str | None = Field(
        default=None,
        description='The chain id to scan next — required when action is "scan"',
    )
    question: str | None = Field(
        default=None,
        description='The clarifying question — required when action is "ask"',
    )


def action_from_reply(reply: ActionReply) -> AgentAction:
    """Converts the validated reply; a scan without a chain id (or an ask
    without a question) degrades to FINISH — never a crash, never a burned
    budget (ADR-030)."""
    reason = reply.reason.strip() if reply.reason and reply.reason.strip() else "no reason given"
    action = reply.action.strip().lower()
    if action == "scan" and reply.chain_id and reply.chain_id.strip():
        return ScanAction(chain_id=reply.chain_id.strip(), reason=reason)
    if action == "ask" and reply.question and reply.question.strip():
        return AskAction(question=reply.question.strip(), reason=reason)
    return FinishAction(reason=reason)


def _action_label(action: AgentAction) -> str:
    if isinstance(action, ScanAction):
        return "scan"
    if isinstance(action, AskAction):
        return "ask"
    return "finish"


def parse_action(text: str) -> AgentAction:
    """Fallback parser (ADR-043): anything malformed means FINISH — a
    confused model must never burn the step budget."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    try:
        payload = json.loads(cleaned)
    except ValueError:
        return FinishAction(reason="policy reply was not valid JSON")
    if not isinstance(payload, dict):
        return FinishAction(reason="policy reply was not a JSON object")

    reason = payload.get("reason")
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else "no reason given"
    chain_id = payload.get("chain_id")
    if payload.get("action") == "scan" and isinstance(chain_id, str) and chain_id.strip():
        return ScanAction(chain_id=chain_id.strip(), reason=reason)
    question = payload.get("question")
    if payload.get("action") == "ask" and isinstance(question, str) and question.strip():
        return AskAction(question=question.strip(), reason=reason)
    return FinishAction(reason=reason)


class LlmAgentPolicy:
    """Live AgentPolicy (ADR-030/058) — the LLM sees the goal, the transcript
    of its own past decisions and the findings collected (token-frugal), and
    picks the next chain to scan. Same injectable-`llm` pattern as
    LlmThreatIntel. Never decides whether to revoke (ADR-058) — that is
    `plan_revocations`, downstream of this loop."""

    def __init__(
        self,
        llm: "BaseChatModel",
        meter: UsageMeter | None = None,
        model: str = "",
        system: str = "",
        fallbacks: "list[BaseChatModel] | None" = None,
    ) -> None:
        self._meter = meter
        self._structured = structured_with_fallbacks([llm, *(fallbacks or [])], ActionReply)
        self._model = model
        self._system = system

    def decide(
        self, goal: str, steps: list[AgentStep], approvals: list[RawApproval]
    ) -> AgentAction:
        transcript = "\n".join(f'- "{s.detail}" -> {s.new_hits} new' for s in steps) or "- none yet"
        summary = (
            "\n".join(f"- {a.token_symbol} -> {a.spender_address}" for a in approvals[:30])
            or "- none yet"
        )
        prompt: LanguageModelInput = POLICY_PROMPT.format(
            goal=goal, transcript=transcript, count=len(approvals), summary=summary
        )
        with llm_span("decide", self._model, self._system) as span:
            start = time.perf_counter()
            raw, parsed = split_structured(self._structured.invoke(prompt))
            duration = time.perf_counter() - start
            record_llm_usage(self._meter, raw)
            input_tokens, output_tokens = usage_tokens(raw)
            record_span_usage(span, input_tokens, output_tokens)
            metrics.record_llm_call("decide", self._system, duration, input_tokens, output_tokens)
            action = (
                action_from_reply(parsed)
                if isinstance(parsed, ActionReply)
                else parse_action(raw_text(raw))
            )
            # The decision is the single most useful attribute for reading a run.
            span.set_attribute("aiagent.agent.action", _action_label(action))
            return action
