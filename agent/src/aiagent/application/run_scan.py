"""The workflow-mode use case: pure orchestration over the ports, no
framework, no I/O. Fixed pipeline (ADR-030): one scan per configured chain,
assess, sort, deliver — read-only, never revokes (contrast `run_agent_scan`,
which triages and executes)."""

import logging

from aiagent.domain.models import (
    AgentStep,
    AgentStepKind,
    ApprovalFinding,
    RawApproval,
    dedupe_approvals,
    flag_new,
    sort_by_risk,
)
from aiagent.domain.ports import ApprovalSource, ResultSink, StepReporter, ThreatIntel

logger = logging.getLogger(__name__)


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


def _report_degraded(
    reporter: StepReporter | None,
    job_id: str,
    seq: int,
    chain_id: str,
    exc: Exception,
) -> None:
    """Records an unscannable chain in the journal (ADR-064), best effort.

    Workflow mode has no agent loop, so these are the only steps it ever
    writes — which is deliberate: a run with a `degraded` step is visibly not
    a clean sweep, and a run without one has nothing to explain.
    """
    if reporter is None:
        return
    try:
        reporter.report_step(
            job_id,
            AgentStep(
                seq=seq,
                kind=AgentStepKind.DEGRADED,
                detail=chain_id,
                reason=f"chain not scanned: {exc}",
                new_hits=0,
            ),
        )
    except Exception:  # noqa: BLE001 - a failed report never fails the job
        logger.warning("could not report the degraded step for chain %s", chain_id)


def _report_scanned(
    reporter: StepReporter | None,
    job_id: str,
    seq: int,
    chain_id: str,
    found: int,
) -> None:
    """Records a chain that *was* covered (ADR-067), best effort."""
    if reporter is None:
        return
    try:
        reporter.report_step(
            job_id,
            AgentStep(
                seq=seq,
                kind=AgentStepKind.SCAN,
                detail=chain_id,
                reason="chain scanned",
                new_hits=found,
            ),
        )
    except Exception:  # noqa: BLE001 - a failed report never fails the job
        logger.warning("could not report the scan step for chain %s", chain_id)


def run_scan(
    job_id: str,
    wallet_address: str,
    chain_ids: list[str],
    source: ApprovalSource,
    threat_intel: ThreatIntel,
    sink: ResultSink,
    seen_keys: set[str] | None = None,
    reporter: StepReporter | None = None,
) -> list[ApprovalFinding]:
    """Marks the job running, scans every configured chain, assesses risk,
    sorts most-dangerous-first, delivers. Read-only: never calls a revoker.

    One chain failing does not lose the others (ADR-064): the failure is
    recorded as a `degraded` step and the run continues. If *every* chain
    fails the exception propagates instead — delivering "no approvals found"
    when nothing was successfully scanned would be a clean bill of health for
    a wallet nobody looked at.

    On failure the sink is notified (best effort) and the exception propagates
    so Celery retries the task; the whole flow is idempotent (`mark_started` is
    a no-op on a non-pending job, delivery replaces previous results).
    """
    try:
        sink.mark_started(job_id)
        approvals: list[RawApproval] = []
        failures: list[str] = []
        last_error: Exception | None = None
        seq = 0
        for chain_id in chain_ids:
            seq += 1
            try:
                found = source.fetch_approvals(wallet_address, chain_id)
            except Exception as exc:  # noqa: BLE001 - one chain must not sink the run
                logger.warning("chain %s could not be scanned: %s", chain_id, exc)
                failures.append(f"{chain_id}: {exc}")
                last_error = exc
                _report_degraded(reporter, job_id, seq, chain_id, exc)
                continue
            approvals.extend(found)
            # ADR-067: record what *was* covered, not only what failed. Without
            # this a workflow run has an empty journal, and "0 dangerous
            # approvals" reads as "this wallet is clean" when it means "clean
            # on the chains we happened to look at" — and the approval source
            # covers three of the ~30 chains a wallet can hold approvals on.
            _report_scanned(reporter, job_id, seq, chain_id, len(found))
        if failures and len(failures) == len(chain_ids):
            # The causes travel with it: "no chain could be scanned" alone
            # tells an operator nothing they can act on, and this string is
            # what lands in the job's error field and in Celery's logs.
            message = (
                f"no chain could be scanned ({'; '.join(failures)}) — "
                "refusing to report a wallet as clean"
                if len(failures) > 1
                else str(last_error)
            )
            raise RuntimeError(message) from last_error
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
