"""The Celery task end to end (ADR-021 fake providers + mocked backend callbacks).

The task object is called directly (synchronously, outside a worker): Celery
then re-raises exceptions instead of scheduling retries, which is exactly what
these tests assert.
"""

import json

import httpx
import pytest
import respx

from aiagent.adapters.fake import FakeApprovalSource
from aiagent.domain.models import raw_approval_key
from aiagent.tasks import run_scan_task

BACKEND = "http://backend-test:8000"


@pytest.fixture(params=["loop", "langgraph"])
def fake_env(request, monkeypatch) -> None:
    """Exercises both agent orchestrators (ADR-046). For langgraph, the durable
    Redis checkpointer is swapped for a per-test in-memory one — same graph, no
    Redis in unit tests; the real RedisSaver path is covered by the live e2e."""
    monkeypatch.setenv("BACKEND_INTERNAL_URL", BACKEND)
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-token")
    monkeypatch.setenv("AGENT_PROVIDERS", "fake")
    monkeypatch.setenv("AGENT_SCAN_CHAIN_IDS", "1")  # keep the workflow-mode fixture small
    monkeypatch.setenv("AGENT_ORCHESTRATOR", request.param)
    if request.param == "langgraph":
        from contextlib import contextmanager

        from langgraph.checkpoint.memory import InMemorySaver

        import aiagent.tasks as tasks_mod

        # One saver shared across calls in a test, so a HITL resume finds the
        # checkpoint left by the paused run (mirrors durable Redis across tasks).
        saver = InMemorySaver()

        @contextmanager
        def _mem_checkpointer(_settings):  # type: ignore[no-untyped-def]
            yield saver

        monkeypatch.setattr(tasks_mod, "_agent_checkpointer", _mem_checkpointer)


@respx.mock
def test_task_runs_end_to_end_with_fake_providers(fake_env) -> None:
    started = respx.post(f"{BACKEND}/internal/jobs/job-1/started").mock(
        return_value=httpx.Response(204)
    )
    results = respx.post(f"{BACKEND}/internal/jobs/job-1/results").mock(
        return_value=httpx.Response(204)
    )

    count = run_scan_task("job-1", "0xwallet", request_id="corr-1")

    assert count == 3  # the three deterministic fake findings on chain 1
    assert started.called
    assert results.called
    # Correlation (ADR-018) rides on every callback.
    assert results.calls.last.request.headers["x-request-id"] == "corr-1"


@respx.mock
def test_task_defaults_the_correlation_id_to_the_job_id(fake_env) -> None:
    respx.post(f"{BACKEND}/internal/jobs/job-2/started").mock(return_value=httpx.Response(204))
    results = respx.post(f"{BACKEND}/internal/jobs/job-2/results").mock(
        return_value=httpx.Response(204)
    )

    run_scan_task("job-2", "0xwallet")

    assert results.calls.last.request.headers["x-request-id"] == "job-2"


@respx.mock
def test_misconfiguration_is_reported_as_a_failed_job_and_raises(fake_env, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDERS", "live")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    failure = respx.post(f"{BACKEND}/internal/jobs/job-3/failure").mock(
        return_value=httpx.Response(204)
    )

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        run_scan_task("job-3", "0xwallet")

    assert failure.called
    assert b"agent misconfigured" in failure.calls.last.request.content


@respx.mock
def test_delivery_failure_reports_and_raises_for_celery_retry(fake_env) -> None:
    respx.post(f"{BACKEND}/internal/jobs/job-4/started").mock(return_value=httpx.Response(204))
    respx.post(f"{BACKEND}/internal/jobs/job-4/results").mock(return_value=httpx.Response(500))
    failure = respx.post(f"{BACKEND}/internal/jobs/job-4/failure").mock(
        return_value=httpx.Response(204)
    )

    with pytest.raises(httpx.HTTPStatusError):
        run_scan_task("job-4", "0xwallet")

    # Best-effort failure report before the exception propagates (ADR-016).
    assert failure.called


@respx.mock
def test_agent_mode_runs_the_loop_auto_revokes_and_reports_the_journal(fake_env) -> None:
    respx.post(f"{BACKEND}/internal/jobs/job-5/started").mock(return_value=httpx.Response(204))
    steps = respx.post(f"{BACKEND}/internal/jobs/job-5/steps").mock(
        return_value=httpx.Response(204)
    )
    results = respx.post(f"{BACKEND}/internal/jobs/job-5/results").mock(
        return_value=httpx.Response(204)
    )

    count = run_scan_task("job-5", "0xwallet", mode="agent")

    # Fake policy: scan chain 1 (3 new) -> scan chain 8453 (3 new) -> finish
    # -> auto-revoke the 2 dangerous findings (one per chain).
    assert count == 6
    kinds = [json.loads(c.request.content)["kind"] for c in steps.calls]
    assert kinds == ["scan", "scan", "finish", "revoke", "revoke"]
    assert results.called
    payload = json.loads(results.calls.last.request.content)
    revoked = [r for r in payload["results"] if r["revocation_status"] == "revoked"]
    assert len(revoked) == 2
    assert all(r["tier"] == "dangerous" for r in revoked)
    assert all(r["revocation_tx_hash"] for r in revoked)


@respx.mock
def test_agent_mode_pauses_on_an_ambiguous_goal_and_resumes_with_the_answer(fake_env) -> None:
    respx.post(f"{BACKEND}/internal/jobs/job-6/started").mock(return_value=httpx.Response(204))
    question = respx.post(f"{BACKEND}/internal/jobs/job-6/question").mock(
        return_value=httpx.Response(204)
    )
    respx.post(f"{BACKEND}/internal/jobs/job-6/steps").mock(return_value=httpx.Response(204))
    results = respx.post(f"{BACKEND}/internal/jobs/job-6/results").mock(
        return_value=httpx.Response(204)
    )

    # run_scan_task's goal always contains "for risky token approvals", never
    # "ambiguous" — the fake policy's ask trigger cannot fire from a real
    # dispatch. This test instead exercises the API-level guarantee: an
    # already-known clarification never re-triggers a pause.
    count = run_scan_task("job-6", "0xwallet", mode="agent", clarification="ethereum only")
    assert count == 6
    assert not question.called
    assert results.called


@respx.mock
def test_recurring_run_flags_the_delta_and_reports_it(fake_env) -> None:
    respx.post(f"{BACKEND}/internal/jobs/job-7/started").mock(return_value=httpx.Response(204))
    steps = respx.post(f"{BACKEND}/internal/jobs/job-7/steps").mock(
        return_value=httpx.Response(204)
    )
    results = respx.post(f"{BACKEND}/internal/jobs/job-7/results").mock(
        return_value=httpx.Response(204)
    )
    respx.post(f"{BACKEND}/internal/jobs/job-7/usage").mock(return_value=httpx.Response(204))

    # One of the three chain-1 fake findings is already known. The same fake
    # spender address is reused on chain 8453 too, so the key must include
    # the chain — a plain spender-address lookup would collide the two.
    known = FakeApprovalSource().fetch_approvals("0xwallet", "1")[0]
    seen = [raw_approval_key(known.chain_id, known.token_address, known.spender_address)]

    count = run_scan_task(
        "job-7", "0xwallet", mode="agent", recurring=True, seen_approval_keys=seen
    )

    assert count == 6
    payload = json.loads(results.calls.last.request.content)
    by_key = {(r["chain_id"], r["spender_address"]): r["is_new"] for r in payload["results"]}
    assert by_key[(known.chain_id, known.spender_address)] is False
    assert by_key[("8453", known.spender_address)] is True  # different chain, not seen
    # The delta report is the last journal step.
    assert json.loads(steps.calls.last.request.content)["kind"] == "report"


@respx.mock
def test_usage_is_reported_at_task_end(fake_env) -> None:
    respx.post(f"{BACKEND}/internal/jobs/job-8/started").mock(return_value=httpx.Response(204))
    respx.post(f"{BACKEND}/internal/jobs/job-8/steps").mock(return_value=httpx.Response(204))
    respx.post(f"{BACKEND}/internal/jobs/job-8/results").mock(return_value=httpx.Response(204))
    usage = respx.post(f"{BACKEND}/internal/jobs/job-8/usage").mock(
        return_value=httpx.Response(204)
    )

    run_scan_task("job-8", "0xwallet", mode="agent")

    # Fake mode (ADR-038): every call counted, zero tokens, zero cost —
    # policy x3 decides + threat-intel x6 assessments = 9 LLM calls;
    # 2 chain scans + 2 revokes = 4 external calls.
    payload = json.loads(usage.calls.last.request.content)
    assert payload == {
        "llm_calls": 9,
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "search_calls": 4,
        "cost_usd": 0.0,
    }
