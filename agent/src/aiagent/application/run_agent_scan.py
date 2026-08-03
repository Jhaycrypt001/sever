"""The agentic scan loop (ADR-030/058): the policy decides which chains to
scan, the loop executes; once scanning stops, DANGEROUS-tier approvals are
auto-revoked — the agent's one and only unattended write action (ADR-058).

Unlike the fixed `run_scan` workflow, the LLM-backed policy drives the scan
control flow here — it picks which chains to cover and judges when it has
scanned enough. The loop only enforces the mechanics: dedup, the step budget
(cost guard), the live journal, and the shared assess/sort/revoke/deliver
tail with the workflow mode.
"""

import logging

from aiagent.application.execute_revocations import revoke_dangerous
from aiagent.application.run_scan import resolve_approvals
from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    AgentStepKind,
    ApprovalFinding,
    AskAction,
    FinishAction,
    RawApproval,
    ScanAction,
    flag_new,
    raw_approval_key,
    sort_by_risk,
)
from aiagent.domain.ports import (
    AgentPolicy,
    ApprovalRevoker,
    ApprovalSource,
    ClarificationRequester,
    ResultSink,
    StepReporter,
    ThreatIntel,
)
from aiagent.domain.usage import SpendGuard

logger = logging.getLogger(__name__)


def _report(reporter: StepReporter, job_id: str, step: AgentStep) -> None:
    """The journal is cosmetic: losing a step must never fail the job."""
    try:
        reporter.report_step(job_id, step)
    except Exception:  # noqa: BLE001 - best effort by contract
        logger.warning("failed to report agent step", extra={"job_id": job_id}, exc_info=True)


def _ask_guard(
    action: AgentAction,
    clarifier: ClarificationRequester | None,
    clarification: str | None,
) -> AgentAction:
    """One clarification per job (ADR-032): once answered (or without a
    clarifier wired), a repeated ask degrades to a finish — no ping-pong."""
    if isinstance(action, AskAction) and (clarifier is None or clarification is not None):
        return FinishAction(
            reason="the policy asked for clarification again; finishing with what was found"
        )
    return action


def run_agent_scan(
    job_id: str,
    goal: str,
    wallet_address: str,
    source: ApprovalSource,
    threat_intel: ThreatIntel,
    policy: AgentPolicy,
    sink: ResultSink,
    reporter: StepReporter,
    revoker: ApprovalRevoker | None = None,
    clarifier: ClarificationRequester | None = None,
    clarification: str | None = None,
    seen_keys: set[str] | None = None,
    max_steps: int = 5,
    budget: SpendGuard | None = None,
) -> list[ApprovalFinding] | None:
    """Runs the scan decision loop, then assesses, sorts, auto-revokes and
    delivers. Failure semantics are identical to `run_scan`.

    With a revoker (ADR-058), every DANGEROUS-tier finding is auto-revoked
    after the scan — the agent's one and only unattended write action.

    With a clarifier (ADR-032), the policy may ask the user one question
    about scan scope: the job pauses (returns None — nothing delivered,
    nothing revoked), and the user's answer re-dispatches it with
    `clarification` set."""
    try:
        sink.mark_started(job_id)
        approvals: list[RawApproval] = []
        collected_keys: set[str] = set()
        steps: list[AgentStep] = []
        # Coverage bookkeeping (ADR-064): a run where every attempted chain
        # failed has looked at nothing, and must not deliver an empty result
        # set that reads as "no dangerous approvals".
        scanned_chains: set[str] = set()
        degraded_chains: list[str] = []

        for seq in range(1, max_steps + 1):
            # Spend cap (ADR-048): money stops the run before the step budget
            # if the indicative cost crosses AGENT_MAX_COST_USD — a clean
            # forced finish, never a crash, like the step budget below.
            if budget is not None and budget.exceeded():
                step = AgentStep(
                    seq=seq,
                    kind=AgentStepKind.FINISH,
                    detail="",
                    reason=f"cost budget of ${budget.cap_usd:.2f} exhausted",
                )
                steps.append(step)
                _report(reporter, job_id, step)
                break
            action = _ask_guard(policy.decide(goal, steps, approvals), clarifier, clarification)
            if isinstance(action, AskAction):
                # Pause (ADR-032): the backend flips the job to awaiting_input;
                # the answer restarts the loop from scratch (fresh journal).
                assert clarifier is not None  # enforced by _ask_guard
                clarifier.request_clarification(job_id, action.question)
                return None
            if isinstance(action, ScanAction):
                try:
                    found = source.fetch_approvals(wallet_address, action.chain_id)
                except Exception as exc:  # noqa: BLE001 - one chain must not sink the run
                    # ADR-064: record the gap and let the loop carry on to the
                    # other chains. The step stays in the journal, so a run
                    # that covered less than it was asked to says so rather
                    # than reading as a clean sweep.
                    logger.warning("chain %s could not be scanned: %s", action.chain_id, exc)
                    degraded_chains.append(f"{action.chain_id}: {exc}")
                    step = AgentStep(
                        seq=seq,
                        kind=AgentStepKind.DEGRADED,
                        detail=action.chain_id,
                        reason=f"chain not scanned: {exc}",
                        new_hits=0,
                    )
                    steps.append(step)
                    _report(reporter, job_id, step)
                    continue
                # Dedup by (chain, token, spender) across scans (ADR-034 equiv).
                new = [
                    a
                    for a in found
                    if raw_approval_key(a.chain_id, a.token_address, a.spender_address)
                    not in collected_keys
                ]
                collected_keys.update(
                    raw_approval_key(a.chain_id, a.token_address, a.spender_address) for a in new
                )
                approvals.extend(new)
                scanned_chains.add(action.chain_id)
                step = AgentStep(
                    seq=seq,
                    kind=AgentStepKind.SCAN,
                    detail=action.chain_id,
                    reason=action.reason,
                    new_hits=len(new),
                )
            else:
                step = AgentStep(
                    seq=seq, kind=AgentStepKind.FINISH, detail="", reason=action.reason
                )
            steps.append(step)
            _report(reporter, job_id, step)
            if step.kind is AgentStepKind.FINISH:
                break
        else:
            # The policy never said stop: the budget does (cost guard).
            step = AgentStep(
                seq=max_steps + 1,
                kind=AgentStepKind.FINISH,
                detail="",
                reason=f"step budget of {max_steps} exhausted",
            )
            steps.append(step)
            _report(reporter, job_id, step)

        # ADR-064: every chain the agent tried was unreachable, so nothing was
        # actually inspected. Failing is the only honest outcome — an empty
        # delivery here renders as "no dangerous approvals found", which is a
        # clean bill of health for a wallet nobody looked at.
        if degraded_chains and not scanned_chains:
            # The causes travel with it: this string becomes the job's error
            # field, and "no chain could be scanned" alone is unactionable.
            raise RuntimeError(
                f"no chain could be scanned ({'; '.join(degraded_chains)}) — "
                "refusing to report a wallet as clean"
            )

        findings = sort_by_risk(resolve_approvals(approvals, threat_intel))

        # Skip execution (an extra call, real gas) once over budget (ADR-048).
        if revoker is not None and not (budget is not None and budget.exceeded()):
            findings = revoke_dangerous(job_id, wallet_address, findings, revoker, reporter, steps)

        if seen_keys is not None:
            # Recurring run (ADR-033): flag the delta against previous runs
            # and journal the verdict.
            findings = flag_new(findings, seen_keys)
            new_count = sum(1 for f in findings if f.is_new)
            reason = (
                f"{new_count} new finding(s) since the last scan"
                if new_count
                else "Nothing new since the last scan"
            )
            report = AgentStep(
                seq=steps[-1].seq + 1 if steps else 1,
                kind=AgentStepKind.REPORT,
                detail="",
                reason=reason,
                new_hits=new_count,
            )
            steps.append(report)
            _report(reporter, job_id, report)
        sink.deliver(job_id, findings)
        return findings
    except Exception as exc:
        try:
            sink.report_failure(job_id, str(exc))
        except Exception:  # noqa: BLE001 - keep the original error as the cause
            pass
        raise
