"""Ports (hexagonal architecture): the use case depends only on these Protocols."""

from typing import Protocol

from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    ApprovalFinding,
    RawApproval,
    RiskAssessment,
)


class ApprovalSource(Protocol):
    """Gathers outstanding approvals for a wallet on one chain (ADR-058) —
    the domain's `SearchProvider` equivalent. One call returns the full set;
    there is no query to refine, unlike free-text web search."""

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]: ...


class AgentPolicy(Protocol):
    """The decision-maker of the agentic loop (ADR-030/058): given the goal
    (which wallet/chains to cover) and everything scanned so far, picks the
    next chain to scan or stops. In production an LLM; in tests a scripted
    fake — the loop itself stays deterministic. Sees the *raw*, not-yet-risk-
    assessed approvals — assessment runs once, after the loop, exactly like
    the example's enrichment (ADR-027)."""

    def decide(
        self, goal: str, steps: list[AgentStep], approvals: list[RawApproval]
    ) -> AgentAction: ...


class ClarificationRequester(Protocol):
    """Pauses the job with a question for the user (ADR-032) — in production
    the `POST /internal/jobs/{id}/question` callback. Used both by the policy
    (ambiguous scan scope) and by the revocation triage (ADR-058, confirming
    watch-tier approvals)."""

    def request_clarification(self, job_id: str, question: str) -> None: ...


class ThreatIntel(Protocol):
    """LLM-backed enrichment (ADR-058): explains and tiers the risk signals a
    structured threat-intel source (GoPlus) already attached to each raw
    approval. Batch-shaped (ADR-042 precedent): both use cases assess a whole
    scan's findings at once. Returns one assessment per approval, same order.
    Never decides whether to revoke — see `plan_revocations` (ADR-058)."""

    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]: ...


class ApprovalRevoker(Protocol):
    """Executes an onchain `approve(spender, 0)` through KeeperHub (ADR-058)
    — the only port through which the agent acts on the world instead of
    merely reporting (contrast ADR-006's worker-never-touches-anything-but-
    callbacks stance: this port touches nothing but KeeperHub's API, still
    never the database or a raw private key). Must not raise on a failed
    execution — a revocation failure is a `RevocationStatus.FAILED` result,
    reported like any other outcome, never a crashed job."""

    def revoke(self, finding: ApprovalFinding) -> ApprovalFinding: ...


class StepReporter(Protocol):
    """Publishes each executed decision for the live journal (ADR-030).
    Best-effort by contract: a failed report never fails the job."""

    def report_step(self, job_id: str, step: AgentStep) -> None: ...


class ResultSink(Protocol):
    """Job lifecycle callbacks — in production, the Rust API (ADR-006/016)."""

    def mark_started(self, job_id: str) -> None: ...

    def deliver(self, job_id: str, results: list[ApprovalFinding]) -> None: ...

    def report_failure(self, job_id: str, error: str) -> None: ...
