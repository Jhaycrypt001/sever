"""Shared execution tail (ADR-058) for both agent orchestrators (the
hand-rolled loop and the LangGraph graph): auto-revokes every DANGEROUS-tier
finding and journals each attempt. Pulled out of `run_agent_scan` rather than
duplicated per orchestrator, the way `resolve_approvals` (`run_scan.py`) is
already shared between workflow and agent mode."""

import logging
from dataclasses import replace

from aiagent.domain.models import (
    AgentStep,
    AgentStepKind,
    ApprovalFinding,
    RevocationStatus,
    approvals_to_auto_revoke,
)
from aiagent.domain.ports import ApprovalRevoker, StepReporter

logger = logging.getLogger(__name__)


def revoke_dangerous(
    job_id: str,
    findings: list[ApprovalFinding],
    revoker: ApprovalRevoker,
    reporter: StepReporter,
    steps: list[AgentStep],
) -> list[ApprovalFinding]:
    """Auto-revokes every DANGEROUS-tier finding, journals each attempt, and
    folds the outcome back into the findings. Never raises: a failed
    revocation is reported as `RevocationStatus.FAILED`, like any other
    outcome, never a crashed job. Mutates `steps` in place (appends the
    REVOKE steps) so callers can keep numbering subsequent steps from it."""
    to_revoke = approvals_to_auto_revoke(findings)
    if not to_revoke:
        return findings

    outcome_by_key: dict[str, ApprovalFinding] = {}
    for finding in to_revoke:
        try:
            outcome = revoker.revoke(finding)
        except Exception:  # noqa: BLE001 - a revoke failure is a result, not a crash
            logger.error("revocation raised", extra={"job_id": job_id}, exc_info=True)
            outcome = replace(finding, revocation_status=RevocationStatus.FAILED)
        outcome_by_key[outcome.approval_key] = outcome
        succeeded = outcome.revocation_status is RevocationStatus.REVOKED
        step = AgentStep(
            seq=steps[-1].seq + 1 if steps else 1,
            kind=AgentStepKind.REVOKE,
            detail=outcome.spender_address,
            reason=(
                f"auto-revoked: tx {outcome.revocation_tx_hash}"
                if succeeded
                else "revocation attempt failed"
            ),
            new_hits=0,
        )
        steps.append(step)
        try:
            reporter.report_step(job_id, step)
        except Exception:  # noqa: BLE001 - best effort by contract
            logger.warning("failed to report agent step", extra={"job_id": job_id}, exc_info=True)
    return [outcome_by_key.get(f.approval_key, f) for f in findings]
