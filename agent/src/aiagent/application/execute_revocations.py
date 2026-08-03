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


def _revoke_reason(outcome: ApprovalFinding) -> str:
    """The journal line shown verbatim in the UI. A dry run says so plainly —
    it must never read like a completed revocation (ADR-058)."""
    if outcome.revocation_status is RevocationStatus.REVOKED:
        return f"auto-revoked: tx {outcome.revocation_tx_hash}"
    if outcome.revocation_status is RevocationStatus.SIMULATED:
        return "simulated only (KEEPERHUB_SIMULATE_ONLY): approval is still live"
    if outcome.revocation_status is RevocationStatus.NOT_ATTEMPTED:
        # ADR-065: refused before the network, because this wallet is not the
        # one KeeperHub can execute as. Naming the reason matters — otherwise
        # it reads as an unexplained omission on the most dangerous row.
        return (
            "not attempted: this wallet is not delegated to KeeperHub, "
            "so the approval can only be revoked by its own owner"
        )
    return "revocation attempt failed"


def revoke_dangerous(
    job_id: str,
    wallet_address: str,
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
            outcome = revoker.revoke(finding, wallet_address)
        except Exception:  # noqa: BLE001 - a revoke failure is a result, not a crash
            logger.error("revocation raised", extra={"job_id": job_id}, exc_info=True)
            outcome = replace(finding, revocation_status=RevocationStatus.FAILED)
        outcome_by_key[outcome.approval_key] = outcome
        step = AgentStep(
            seq=steps[-1].seq + 1 if steps else 1,
            kind=AgentStepKind.REVOKE,
            detail=outcome.spender_address,
            reason=_revoke_reason(outcome),
            new_hits=0,
        )
        steps.append(step)
        try:
            reporter.report_step(job_id, step)
        except Exception:  # noqa: BLE001 - best effort by contract
            logger.warning("failed to report agent step", extra={"job_id": job_id}, exc_info=True)
    return [outcome_by_key.get(f.approval_key, f) for f in findings]
