from fastapi.testclient import TestClient

import aiagent.adapters.api.app as api_module
from aiagent.adapters.api.app import app

client = TestClient(app)


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_enqueue_requires_internal_token(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "right-token")
    response = client.post(
        "/tasks",
        json={"job_id": "j1", "wallet_address": "0xabc"},
        headers={"X-Internal-Token": "wrong-token"},
    )
    assert response.status_code == 401


class RecordingDelay:
    """Stands in for `run_scan_task.delay`, recording every call's kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self, job_id, wallet_address, request_id, mode, clarification, recurring, seen_approval_keys
    ) -> None:
        self.calls.append(
            {
                "job_id": job_id,
                "wallet_address": wallet_address,
                "request_id": request_id,
                "mode": mode,
                "clarification": clarification,
                "recurring": recurring,
                "seen_approval_keys": seen_approval_keys,
            }
        )


def test_enqueue_delegates_to_celery_with_correlation_id(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "right-token")
    recorder = RecordingDelay()
    monkeypatch.setattr(api_module.run_scan_task, "delay", recorder)

    response = client.post(
        "/tasks",
        json={"job_id": "j1", "wallet_address": "0xabc"},
        headers={"X-Internal-Token": "right-token", "X-Request-Id": "corr-42"},
    )

    assert response.status_code == 202
    assert response.json() == {"job_id": "j1", "state": "queued"}
    assert response.headers["x-request-id"] == "corr-42"
    # The mode defaults to the read-only workflow pipeline.
    call = recorder.calls[0]
    assert (call["job_id"], call["wallet_address"], call["request_id"], call["mode"]) == (
        "j1",
        "0xabc",
        "corr-42",
        "workflow",
    )


def test_enqueue_forwards_the_agent_mode(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "right-token")
    recorder = RecordingDelay()
    monkeypatch.setattr(api_module.run_scan_task, "delay", recorder)

    response = client.post(
        "/tasks",
        json={"job_id": "j1", "wallet_address": "0xabc", "mode": "agent"},
        headers={"X-Internal-Token": "right-token"},
    )

    assert response.status_code == 202
    assert recorder.calls[0]["mode"] == "agent"


def test_enqueue_falls_back_to_the_job_id_as_correlation_id(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "right-token")
    recorder = RecordingDelay()
    monkeypatch.setattr(api_module.run_scan_task, "delay", recorder)

    response = client.post(
        "/tasks",
        json={"job_id": "j1", "wallet_address": "0xabc"},
        headers={"X-Internal-Token": "right-token"},
    )

    assert response.status_code == 202
    assert recorder.calls[0]["request_id"] == "j1"


def test_enqueue_forwards_the_clarification(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "right-token")
    recorder = RecordingDelay()
    monkeypatch.setattr(api_module.run_scan_task, "delay", recorder)

    client.post(
        "/tasks",
        json={
            "job_id": "j1",
            "wallet_address": "0xabc",
            "mode": "agent",
            "clarification": "ethereum only",
        },
        headers={"X-Internal-Token": "right-token"},
    )
    client.post(
        "/tasks",
        json={"job_id": "j2", "wallet_address": "0xabc"},
        headers={"X-Internal-Token": "right-token"},
    )

    assert [c["clarification"] for c in recorder.calls] == ["ethereum only", None]


def test_enqueue_forwards_the_seen_approval_keys(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "right-token")
    recorder = RecordingDelay()
    monkeypatch.setattr(api_module.run_scan_task, "delay", recorder)

    client.post(
        "/tasks",
        json={
            "job_id": "j1",
            "wallet_address": "0xabc",
            "seen_approval_keys": ["1:0xtoken:0xspender"],
        },
        headers={"X-Internal-Token": "right-token"},
    )
    client.post(
        "/tasks",
        json={"job_id": "j2", "wallet_address": "0xabc"},
        headers={"X-Internal-Token": "right-token"},
    )

    # Default: empty memory (dispatches from pre-ADR-033 backends included).
    assert [c["seen_approval_keys"] for c in recorder.calls] == [["1:0xtoken:0xspender"], []]
