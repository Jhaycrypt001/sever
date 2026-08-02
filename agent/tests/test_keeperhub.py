"""KeeperHub ApprovalRevoker adapter (ADR-058) — exercises the three
landmines confirmed against KeeperHub's own issue tracker before writing the
adapter: string-typed fields (#1841), a fresh idempotency key per attempt
(#1840), and polling status for the tx hash (#1784)."""

import json
import logging

import httpx
import respx

from aiagent.adapters.keeperhub import KeeperHubApprovalRevoker
from aiagent.domain.models import ApprovalFinding, RevocationStatus, RiskTier

API_URL = "https://app.keeperhub.com"


def a_finding() -> ApprovalFinding:
    return ApprovalFinding(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="USDC",
        spender_address="0xbad",
        approved_amount="Unlimited",
        tier=RiskTier.DANGEROUS,
    )


@respx.mock
def test_revoke_posts_string_typed_fields_and_polls_status_for_the_tx_hash() -> None:
    # #1841: chainId/functionArgs/gasLimitMultiplier must be strings.
    post_route = respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "exec-1", "status": "pending"})
    )
    # #1784: the POST response never carries transactionHash — only status does.
    respx.get(f"{API_URL}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "executionId": "exec-1",
                "status": "completed",
                "transactionHash": "0xabc123",
            },
        )
    )
    revoker = KeeperHubApprovalRevoker(API_URL, "kh_test_key", poll_interval_seconds=0)

    result = revoker.revoke(a_finding())

    body = json.loads(post_route.calls.last.request.content)
    assert body["chainId"] == "1" and isinstance(body["chainId"], str)
    assert body["functionArgs"] == '["0xbad", "0"]'
    assert isinstance(body["gasLimitMultiplier"], str)
    assert body["contractAddress"] == "0xtoken"
    assert body["functionName"] == "approve"
    assert result.revocation_status == RevocationStatus.REVOKED
    assert result.revocation_tx_hash == "0xabc123"


@respx.mock
def test_revoke_sends_a_fresh_idempotency_key_per_attempt() -> None:
    # #1840: a reused Idempotency-Key caches a failure forever. Two separate
    # `revoke` calls (e.g. a Celery retry) must never reuse one.
    post_route = respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "exec-1", "status": "pending"})
    )
    respx.get(f"{API_URL}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(200, json={"status": "completed", "transactionHash": "0xabc"})
    )
    revoker = KeeperHubApprovalRevoker(API_URL, "kh_test_key", poll_interval_seconds=0)

    revoker.revoke(a_finding())
    revoker.revoke(a_finding())

    keys = [c.request.headers["idempotency-key"] for c in post_route.calls]
    assert len(keys) == 2 and keys[0] != keys[1]


@respx.mock
def test_revoke_returns_failed_when_execution_never_completes() -> None:
    respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "exec-1", "status": "pending"})
    )
    respx.get(f"{API_URL}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(200, json={"status": "failed", "error": "insufficient gas"})
    )
    revoker = KeeperHubApprovalRevoker(API_URL, "kh_test_key", poll_interval_seconds=0)

    result = revoker.revoke(a_finding())

    assert result.revocation_status == RevocationStatus.FAILED
    assert result.revocation_tx_hash is None


@respx.mock
def test_revoke_returns_failed_when_no_execution_id_comes_back() -> None:
    respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"status": "completed"})
    )
    revoker = KeeperHubApprovalRevoker(API_URL, "kh_test_key")

    result = revoker.revoke(a_finding())

    assert result.revocation_status == RevocationStatus.FAILED


@respx.mock
def test_simulate_only_flag_is_forwarded() -> None:
    post_route = respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "status": "simulated", "wouldRevert": False},
        )
    )
    revoker = KeeperHubApprovalRevoker(
        API_URL, "kh_test_key", simulate_only=True, poll_interval_seconds=0
    )

    revoker.revoke(a_finding())

    body = json.loads(post_route.calls.last.request.content)
    assert body["simulate"] is True


@respx.mock
def test_a_successful_simulation_is_never_reported_as_revoked() -> None:
    # Confirmed live against the real API (Sepolia, 2026-08-02): simulate=true
    # returns its result synchronously, with no executionId to poll at all —
    # a different response shape from a real execution, not just a stub of it.
    # No transaction was broadcast, so the approval is STILL LIVE: reporting
    # REVOKED here would tell a user a draining approval is gone when it is not.
    respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "status": "simulated",
                "from": "0xe13ed979bc6b23d6d9608939051e9488e9f304bf",
                "to": "0xtoken",
                "gasEstimate": "26206",
                "wouldRevert": False,
            },
        )
    )
    revoker = KeeperHubApprovalRevoker(
        API_URL, "kh_test_key", simulate_only=True, poll_interval_seconds=0
    )

    result = revoker.revoke(a_finding())

    assert result.revocation_status == RevocationStatus.SIMULATED
    assert result.revocation_status != RevocationStatus.REVOKED
    assert result.revocation_tx_hash is None


@respx.mock
def test_a_reverting_simulation_is_reported_as_failed() -> None:
    respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "status": "simulated", "wouldRevert": True},
        )
    )
    revoker = KeeperHubApprovalRevoker(
        API_URL, "kh_test_key", simulate_only=True, poll_interval_seconds=0
    )

    result = revoker.revoke(a_finding())

    assert result.revocation_status == RevocationStatus.FAILED


@respx.mock
def test_authorization_header_carries_the_bearer_key() -> None:
    route = respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "exec-1", "status": "pending"})
    )
    respx.get(f"{API_URL}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(200, json={"status": "completed", "transactionHash": "0x1"})
    )
    KeeperHubApprovalRevoker(API_URL, "kh_secret", poll_interval_seconds=0).revoke(a_finding())

    assert route.calls.last.request.headers["authorization"] == "Bearer kh_secret"


@respx.mock
def test_a_failed_revoke_logs_the_audit_line(caplog) -> None:
    # The audit trail (ADR-058/018): every attempt, success or failure, as a
    # structured log line — greppable/alertable without a DB query.
    respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"status": "completed"})
    )
    revoker = KeeperHubApprovalRevoker(API_URL, "kh_test_key")

    with caplog.at_level(logging.INFO, logger="aiagent.adapters.keeperhub"):
        revoker.revoke(a_finding())

    record = next(r for r in caplog.records if r.message == "revocation attempt")
    assert record.revocation_status == "failed"  # type: ignore[attr-defined]
    assert record.spender_address == "0xbad"  # type: ignore[attr-defined]
    assert record.tier == "dangerous"  # type: ignore[attr-defined]


@respx.mock
def test_meters_one_call_per_revoke_attempt() -> None:
    from aiagent.domain.usage import UsageMeter

    respx.post(f"{API_URL}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "exec-1", "status": "pending"})
    )
    respx.get(f"{API_URL}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(200, json={"status": "completed", "transactionHash": "0x1"})
    )
    meter = UsageMeter()
    KeeperHubApprovalRevoker(API_URL, "kh_key", meter=meter, poll_interval_seconds=0).revoke(
        a_finding()
    )

    assert meter.snapshot().search_calls == 1
