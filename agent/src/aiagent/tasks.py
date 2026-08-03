"""Celery tasks: thin glue wiring adapters into the use case (no business logic)."""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from aiagent import metrics
from aiagent.application import run_agent_scan, run_scan
from aiagent.celery_app import app
from aiagent.config import Settings
from aiagent.domain.models import ApprovalFinding
from aiagent.domain.ports import AgentPolicy, ApprovalRevoker, ApprovalSource, ThreatIntel
from aiagent.domain.usage import Pricing, SpendGuard, UsageMeter

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


def _pricing_for(settings: Settings) -> Pricing:
    """Indicative USD rates (ADR-038). Fakes are free (ADR-021): $0, so the
    spend cap (ADR-048) never trips in the keyless demo/e2e."""
    if settings.providers == "fake":
        return Pricing(llm_input_per_mtok=0.0, llm_output_per_mtok=0.0, search_per_call=0.0)
    return Pricing(
        llm_input_per_mtok=settings.llm_cost_input_per_mtok,
        llm_output_per_mtok=settings.llm_cost_output_per_mtok,
        search_per_call=settings.search_cost_per_call,
    )


@contextmanager
def _agent_checkpointer(settings: Settings) -> "Iterator[BaseCheckpointSaver[Any]]":
    """Durable checkpoint store for the LangGraph orchestrator (ADR-046),
    keyed by job_id. Redis is the worker's own infrastructure (the Celery
    broker), so this respects ADR-006 — the worker still never touches the
    database. A seam so tests can supply an in-memory saver instead."""
    from langgraph.checkpoint.redis import RedisSaver

    with RedisSaver.from_conn_string(settings.redis_url) as checkpointer:
        checkpointer.setup()  # idempotent: creates the Redis indices once
        yield checkpointer


def llm_is_configured(settings: Settings) -> bool:
    """Whether a language model can actually be reached (ADR-060). Ollama runs
    locally and needs no credential; the hosted backend needs its key. When
    this is False the pipeline still runs end to end — tier classification and
    revocation never depended on a model — with templated explanations."""
    if settings.llm_backend == "ollama":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def build_providers(
    settings: Settings, meter: UsageMeter | None = None
) -> tuple[ApprovalSource, ThreatIntel]:
    """Selects the provider adapters (ADR-021/060): GoPlus plus either the
    LLM explainer or the keyless deterministic one, or the fakes with
    `AGENT_PROVIDERS=fake`. The meter records spend (ADR-038)."""
    if settings.providers == "fake":
        from aiagent.adapters.fake import FakeApprovalSource, FakeThreatIntel

        return FakeApprovalSource(meter), FakeThreatIntel(meter)

    from aiagent.adapters.goplus import GoPlusApprovalSource

    source = GoPlusApprovalSource(meter=meter, api_key=settings.goplus_api_key)

    if not llm_is_configured(settings):
        # ADR-060: real data, real tiers, real revocations, templated prose.
        from aiagent.adapters.deterministic import DeterministicThreatIntel

        logger.info("no LLM configured: using deterministic explanations")
        return source, DeterministicThreatIntel(meter)

    from aiagent.adapters.chat_model import make_chat_model, make_fallback_chat_models
    from aiagent.adapters.llm import LlmThreatIntel

    return (
        source,
        LlmThreatIntel(
            make_chat_model(settings, max_tokens=256),
            meter=meter,
            model=settings.agent_model_id,
            system=settings.llm_backend,
            fallbacks=make_fallback_chat_models(settings, max_tokens=256),
        ),
    )


def build_policy(settings: Settings, meter: UsageMeter | None = None) -> AgentPolicy:
    """Selects the decision-maker of the agentic loop (ADR-030/058/060)."""
    if settings.providers == "fake":
        from aiagent.adapters.fake import FakeAgentPolicy

        return FakeAgentPolicy(meter)

    if not llm_is_configured(settings):
        # Agent mode without a model cannot choose *which* chain to prioritize,
        # so it covers all of them and says so in the journal (ADR-060).
        from aiagent.adapters.deterministic import DeterministicAgentPolicy

        logger.info("no LLM configured: agent mode scans every configured chain in order")
        return DeterministicAgentPolicy(settings.scan_chain_ids, meter)

    from aiagent.adapters.chat_model import make_chat_model, make_fallback_chat_models
    from aiagent.adapters.llm import LlmAgentPolicy

    return LlmAgentPolicy(
        make_chat_model(settings, max_tokens=256),
        meter=meter,
        model=settings.agent_model_id,
        system=settings.llm_backend,
        fallbacks=make_fallback_chat_models(settings, max_tokens=256),
    )


def build_revoker(settings: Settings, meter: UsageMeter | None = None) -> ApprovalRevoker:
    """Selects the onchain executor (ADR-058): KeeperHub live, or a fake that
    always "succeeds" with a synthetic tx hash for the keyless demo/e2e."""
    if settings.providers == "fake":
        from aiagent.adapters.fake import FakeApprovalRevoker

        return FakeApprovalRevoker(meter)

    from aiagent.adapters.keeperhub import KeeperHubApprovalRevoker

    return KeeperHubApprovalRevoker(
        settings.keeperhub_api_url,
        settings.keeperhub_api_key,
        meter=meter,
        simulate_only=settings.keeperhub_simulate_only,
    )


def _run_agent(
    settings: Settings,
    job_id: str,
    wallet_address: str,
    clarification: str | None,
    source: ApprovalSource,
    threat_intel: ThreatIntel,
    policy: AgentPolicy,
    revoker: ApprovalRevoker,
    # HttpResultSink structurally satisfies ResultSink + StepReporter +
    # ClarificationRequester; typed Any to pass it in all three roles.
    sink: Any,
    memory: set[str] | None,
    budget: SpendGuard,
) -> list[ApprovalFinding] | None:
    """Dispatches the agent mode to the configured orchestrator (ADR-046).
    Both drive the same ports; `sink` also acts as StepReporter and
    ClarificationRequester. Returns the findings, or None when paused."""
    if settings.agent_orchestrator == "loop":
        goal = f"scan wallet {wallet_address} for risky token approvals"
        if clarification:
            goal = f'{goal} (user clarification: "{clarification}")'
        return run_agent_scan(
            job_id,
            goal,
            wallet_address,
            source,
            threat_intel,
            policy,
            sink,
            sink,
            revoker=revoker,
            clarifier=sink,
            clarification=clarification,
            seen_keys=memory,
            max_steps=settings.agent_max_steps,
            budget=budget,
        )
    from aiagent.adapters.orchestration.langgraph_agent import run_agent_graph

    goal = f"scan wallet {wallet_address} for risky token approvals"
    with _agent_checkpointer(settings) as checkpointer:
        return run_agent_graph(
            job_id,
            goal,
            wallet_address,
            source,
            threat_intel,
            policy,
            sink,
            sink,
            checkpointer,
            revoker=revoker,
            clarifier=sink,
            seen_keys=memory,
            max_steps=settings.agent_max_steps,
            budget=budget,
            # The re-dispatch after an answer carries it as `clarification`;
            # for the graph that means: resume from the checkpoint (ADR-046).
            resume_answer=clarification,
        )


@app.task(
    name="aiagent.run_scan",
    bind=False,
    # Transient failures (network, provider hiccup) are retried with exponential
    # backoff; idempotence makes re-runs safe (ADR-016). After the last retry the
    # job stays failed via report_failure / the backend reaper.
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_scan_task(
    job_id: str,
    wallet_address: str,
    request_id: str | None = None,
    mode: str = "workflow",
    clarification: str | None = None,
    recurring: bool = False,
    seen_approval_keys: list[str] | None = None,
) -> int:
    settings = Settings.from_env()
    request_id = request_id or job_id
    # A one-shot scan carries no memory; a recurring run flags its findings
    # against the (possibly empty, on the first run) memory (ADR-033).
    memory = set(seen_approval_keys or []) if recurring else None
    log_ctx = {"request_id": request_id, "job_id": job_id, "mode": mode}
    logger.info("scan task started", extra=log_ctx)

    from aiagent.adapters.sink import HttpResultSink

    sink = HttpResultSink(
        settings.backend_internal_url,
        settings.internal_api_token,
        request_id=request_id,
    )
    meter = UsageMeter()
    # Spend cap (ADR-048): checked live against this same meter; 0 disables it,
    # and the fakes price at $0 so it never trips in the keyless demo.
    budget = SpendGuard(meter, _pricing_for(settings), settings.agent_max_cost_usd)
    try:
        source, threat_intel = build_providers(settings, meter)
        policy = build_policy(settings, meter) if mode == "agent" else None
        revoker = build_revoker(settings, meter) if mode == "agent" else None
    except Exception as exc:
        # Misconfiguration (missing API key...) must surface to the user as a failed job.
        logger.error("agent misconfigured", extra=log_ctx, exc_info=True)
        sink.report_failure(job_id, f"agent misconfigured: {exc}")
        raise

    # Outcome for the job metric (ADR-050); the exit paths below refine it.
    job_outcome = "failed"

    def report_usage() -> None:
        """Spend tracking (ADR-038): sent at every task end (success, pause,
        failure) so retries and resumed runs accumulate their real cost.
        Also feeds the job/cost metrics (ADR-050). Best-effort — losing a
        metric never fails the job."""
        usage = meter.snapshot()
        pricing = _pricing_for(settings)
        metrics.record_job(job_outcome, usage.cost_usd(pricing))
        if usage.llm_calls == 0 and usage.search_calls == 0:
            return
        try:
            sink.report_usage(job_id, usage, pricing)
        except Exception:  # noqa: BLE001 - best effort by contract
            logger.warning("failed to report usage", extra=log_ctx, exc_info=True)

    try:
        if policy is not None:
            # Agent mode (ADR-030/058): the policy drives the scan, and every
            # DANGEROUS-tier finding is auto-revoked through KeeperHub after
            # it. The sink also implements StepReporter + ClarificationRequester.
            # Two orchestrators (ADR-046) share these ports: the LangGraph
            # StateGraph (default, durable checkpointing + native interrupt
            # HITL) or the hand-rolled loop (AGENT_ORCHESTRATOR=loop).
            assert revoker is not None
            outcome = _run_agent(
                settings,
                job_id,
                wallet_address,
                clarification,
                source,
                threat_intel,
                policy,
                revoker,
                sink,
                memory,
                budget,
            )
            if outcome is None:
                # Paused (ADR-032): the job awaits the user's answer; a fresh
                # task will be dispatched when it arrives.
                job_outcome = "paused"
                logger.info("scan task paused awaiting user input", extra=log_ctx)
                return 0
            results = outcome
        else:
            results = run_scan(
                job_id,
                wallet_address,
                settings.scan_chain_ids,
                source,
                threat_intel,
                sink,
                seen_keys=memory,
                # The sink also implements StepReporter, which is how a
                # workflow run records an unscannable chain (ADR-064).
                reporter=sink,
            )
        job_outcome = "completed"
    except Exception:
        logger.error("scan task failed", extra=log_ctx, exc_info=True)
        raise
    finally:
        report_usage()
    logger.info("scan task completed", extra={**log_ctx, "results": len(results)})
    return len(results)
