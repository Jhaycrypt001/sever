"""Guards the reliability configuration of ADR-016 against regressions."""

from aiagent.celery_app import app
from aiagent.tasks import run_scan_task


def test_tasks_are_acked_late_and_requeued_on_worker_loss() -> None:
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1


def test_scan_task_retries_with_backoff() -> None:
    assert run_scan_task.max_retries == 3
    assert run_scan_task.retry_backoff is True
    assert run_scan_task.retry_backoff_max == 600
    assert run_scan_task.retry_jitter is True


# ---------------------------------------------------------------- ADR-041/058


def test_ollama_backend_does_not_require_the_anthropic_key(monkeypatch) -> None:
    """Fail-fast check (ADR-020) adjusted by ADR-041: a local backend needs no
    hosted-LLM key — only KeeperHub's key stays required in live mode."""
    from aiagent.celery_app import _check_required_env

    monkeypatch.setenv("AGENT_PROVIDERS", "live")
    monkeypatch.setenv("AGENT_LLM_BACKEND", "ollama")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_key")
    _check_required_env()  # must not raise


def test_a_missing_llm_key_warns_but_still_starts(monkeypatch, caplog) -> None:
    """ADR-060: the LLM only authors explanation prose. Scanning, risk
    classification and revocation are deterministic, so a missing key must
    degrade the writing — never stop the worker from protecting a wallet."""
    import logging

    from aiagent.celery_app import _check_required_env

    monkeypatch.setenv("AGENT_PROVIDERS", "live")
    monkeypatch.setenv("AGENT_LLM_BACKEND", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_key")

    with caplog.at_level(logging.WARNING):
        _check_required_env()  # must not raise

    assert "ANTHROPIC_API_KEY is not set" in caplog.text


def test_missing_keeperhub_key_fails_fast_even_with_ollama(monkeypatch) -> None:
    """ADR-058: a worker that cannot execute a revocation must not start,
    regardless of which LLM backend is configured."""
    import pytest

    from aiagent.celery_app import _check_required_env

    monkeypatch.setenv("AGENT_PROVIDERS", "live")
    monkeypatch.setenv("AGENT_LLM_BACKEND", "ollama")
    monkeypatch.delenv("KEEPERHUB_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        _check_required_env()
