"""The workflow-mode use case: pure orchestration over the ports, no
framework, no I/O. Fixed pipeline (ADR-030): one scan per configured chain,
assess, sort, deliver — read-only, never revokes (contrast `run_agent_scan`,
which triages and executes)."""

from aiagent.domain.models import (
    ApprovalFinding,
    RawApproval,
    dedupe_approvals,
    flag_new,
    sort_by_risk,
)
from aiagent.domain.ports import ApprovalSource, ResultSink, ThreatIntel


def resolve_approvals(
    approvals: list[RawApproval], threat_intel: ThreatIntel
) -> list[ApprovalFinding]:
    """Assesses a whole approval set through one batched port call (ADR-042 —
    the adapter parallelizes the per-approval LLM calls)."""
    assessments = threat_intel.assess_many(approvals)
    return [
        ApprovalFinding(
            chain_id=approval.chain_id,
            token_address=approval.token_address,
            token_symbol=approval.token_symbol,
            spender_address=approval.spender_address,
            spender_name=approval.spender_name,
            approved_amount=approval.approved_amount,
            tier=assessment.tier,
            malicious_behavior=assessment.malicious_behavior,
            explanation=assessment.explanation,
            raw=approval.raw,
        )
        for approval, assessment in zip(approvals, assessments, strict=True)
    ]


def run_scan(
    job_id: str,
    wallet_address: str,
    chain_ids: list[str],
    source: ApprovalSource,
    threat_intel: ThreatIntel,
    sink: ResultSink,
    seen_keys: set[str] | None = None,
) -> list[ApprovalFinding]:
    """Marks the job running, scans every configured chain, assesses risk,
    sorts most-dangerous-first, delivers. Read-only: never calls a revoker.

    On failure the sink is notified (best effort) and the exception propagates
    so Celery retries the task; the whole flow is idempotent (`mark_started` is
    a no-op on a non-pending job, delivery replaces previous results).
    """
    try:
        sink.mark_started(job_id)
        approvals: list[RawApproval] = []
        for chain_id in chain_ids:
            approvals.extend(source.fetch_approvals(wallet_address, chain_id))
        # Dedup (ADR-034 equivalent): the same live approval reported twice
        # by overlapping chain scans counts once.
        approvals = dedupe_approvals(approvals)
        results = sort_by_risk(resolve_approvals(approvals, threat_intel))
        if seen_keys is not None:
            # Recurring run (ADR-033): flag what previous runs already saw.
            results = flag_new(results, seen_keys)
        sink.deliver(job_id, results)
        return results
    except Exception as exc:
        try:
            sink.report_failure(job_id, str(exc))
        except Exception:  # noqa: BLE001 - keep the original error as the cause
            pass
        raise
