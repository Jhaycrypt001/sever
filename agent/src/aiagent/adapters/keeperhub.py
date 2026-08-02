"""KeeperHub adapter (ADR-058): the live `ApprovalRevoker` — the only place
this agent writes to a chain. Calls KeeperHub's direct-execution REST API
(no workflow needed for a single `approve(spender, 0)` call):

    POST {api_url}/api/execute/contract-call
    GET  {api_url}/api/execute/{executionId}/status

Three landmines confirmed against KeeperHub's own issue tracker before
writing this, all worked around here rather than discovered by a flaky demo:

- **#1841**: `chainId`/`functionArgs`/`gasLimitMultiplier` must be strings,
  not numbers — every field below is built as a string.
- **#1840**: a reused `Idempotency-Key` caches a *failure* too, so a naive
  retry can never recover. A fresh UUID is minted per revoke *attempt*
  (Celery's own retry calls `revoke` again, which mints a new key).
- **#1784**: the initial POST response never carries `transactionHash` for
  a completed execution — it only appears on `GET .../status`. This adapter
  always polls status to completion rather than trusting the POST response.

A fourth, undocumented behavior confirmed live against the real API
(Sepolia, 2026-08-02, `simulate=true`): a simulated call returns its result
**synchronously in the POST response** — `{success, status: "simulated",
wouldRevert, ...}` — with **no `executionId`** at all, unlike a real
execution which returns one to poll. Handled explicitly below rather than
falling through to the "no executionId" failure path.
"""

import json
import logging
import time
from dataclasses import replace
from uuid import uuid4

import httpx
from opentelemetry import trace

from aiagent import metrics
from aiagent.domain.models import ApprovalFinding, RevocationStatus
from aiagent.domain.usage import UsageMeter

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"completed", "failed"}

# No-op when telemetry is off (ADR-029 amendment, mirrors adapters/llm.py's
# llm_span): the span/metrics add nothing to the keyless demo/CI and appear
# in Jaeger only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
_tracer = trace.get_tracer("aiagent.keeperhub")


class KeeperHubApprovalRevoker:
    """Live adapter — never exercised in CI (ADR-012); the network call
    itself is the one thing a unit test cannot verify."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        meter: UsageMeter | None = None,
        client: httpx.Client | None = None,
        simulate_only: bool = False,
        poll_interval_seconds: float = 2.0,
        poll_timeout_seconds: float = 90.0,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._meter = meter
        self._client = client or httpx.Client(timeout=30)
        self._simulate_only = simulate_only
        self._poll_interval = poll_interval_seconds
        self._poll_timeout = poll_timeout_seconds
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def revoke(self, finding: ApprovalFinding) -> ApprovalFinding:
        # Counted as a generic external call for the spend-cap accounting
        # (ADR-048); real gas cost is not an LLM/API spend and is tracked
        # separately by the audit trail (tx hash, gas used), not this meter.
        if self._meter is not None:
            self._meter.record_search()

        with _tracer.start_as_current_span("keeperhub revoke") as span:
            span.set_attribute("aiagent.chain_id", finding.chain_id)
            span.set_attribute("aiagent.spender_address", finding.spender_address)
            span.set_attribute("aiagent.tier", finding.tier.value)
            span.set_attribute("aiagent.simulate", self._simulate_only)
            start = time.perf_counter()
            outcome = self._execute(finding)
            duration = time.perf_counter() - start
            span.set_attribute("aiagent.revocation_status", outcome.revocation_status.value)

        metrics.record_revocation(finding.tier.value, outcome.revocation_status.value, duration)
        # The audit trail (ADR-058/018): every attempt, not just failures, as a
        # structured log line — greppable/alertable without a DB query, on top
        # of the durable ApprovalFinding row (revocation_status/tx_hash).
        logger.info(
            "revocation attempt",
            extra={
                "chain_id": finding.chain_id,
                "token_symbol": finding.token_symbol,
                "spender_address": finding.spender_address,
                "tier": finding.tier.value,
                "revocation_status": outcome.revocation_status.value,
                "revocation_tx_hash": outcome.revocation_tx_hash,
                "simulate": self._simulate_only,
            },
        )
        return outcome

    def _execute(self, finding: ApprovalFinding) -> ApprovalFinding:
        body = {
            "contractAddress": finding.token_address,
            "chainId": str(finding.chain_id),
            "functionName": "approve",
            "functionArgs": json.dumps([finding.spender_address, "0"]),
            "gasLimitMultiplier": "1.3",
            "simulate": self._simulate_only,
        }
        # #1840: a fresh key per *attempt* — a Celery retry calling this
        # method again must never replay a cached failure.
        headers = {**self._headers, "Idempotency-Key": str(uuid4())}
        response = self._client.post(
            f"{self._api_url}/api/execute/contract-call", json=body, headers=headers
        )
        response.raise_for_status()
        data = response.json()

        # simulate=true: synchronous result, nothing to poll (see module docstring).
        if data.get("status") == "simulated":
            if data.get("success") and not data.get("wouldRevert", True):
                return replace(finding, revocation_status=RevocationStatus.REVOKED)
            logger.error(
                "KeeperHub simulation would revert",
                extra={"spender": finding.spender_address, "response": data},
            )
            return replace(finding, revocation_status=RevocationStatus.FAILED)

        execution_id = data.get("executionId")
        if not execution_id:
            logger.error(
                "KeeperHub accepted the revoke but returned no executionId",
                extra={"spender": finding.spender_address},
            )
            return replace(finding, revocation_status=RevocationStatus.FAILED)

        status, tx_hash = self._poll_status(execution_id)
        if status == "completed" and tx_hash:
            return replace(
                finding, revocation_status=RevocationStatus.REVOKED, revocation_tx_hash=tx_hash
            )
        logger.error(
            "KeeperHub revocation did not complete",
            extra={
                "spender": finding.spender_address,
                "execution_id": execution_id,
                "status": status,
            },
        )
        return replace(finding, revocation_status=RevocationStatus.FAILED)

    def _poll_status(self, execution_id: str) -> tuple[str, str | None]:
        # #1784: transactionHash never rides the POST response — only the
        # status endpoint, once the execution reaches a terminal state.
        deadline = time.monotonic() + self._poll_timeout
        while time.monotonic() < deadline:
            response = self._client.get(
                f"{self._api_url}/api/execute/{execution_id}/status", headers=self._headers
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status", "")
            if status in _TERMINAL_STATUSES:
                return status, data.get("transactionHash")
            time.sleep(self._poll_interval)
        logger.error(
            "KeeperHub execution status poll timed out", extra={"execution_id": execution_id}
        )
        return "failed", None
