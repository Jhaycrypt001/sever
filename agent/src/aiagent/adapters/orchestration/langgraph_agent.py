"""LangGraph orchestration of the agent mode (ADR-046/058).

The default orchestrator for `mode=agent`: the same decision loop as the
hand-rolled `run_agent_scan` (ADR-030/032/058), expressed as a LangGraph
`StateGraph`. It is an **adapter** — it depends only on the domain ports
(`AgentPolicy`, `ApprovalSource`, `StepReporter`, `ClarificationRequester`,
`ResultSink`), so the domain stays framework-free and the loop remains
available behind `AGENT_ORCHESTRATOR=loop`.

Two things the graph buys over the plain loop:
- **Durable checkpointing** (Redis, keyed by `job_id`): the graph state is
  persisted at every super-step, so a resumed run continues instead of redoing
  the work.
- **Native HITL** via `interrupt()`: the clarification pause is a first-class
  graph primitive; the user's answer resumes the graph mid-flight (ADR-032)
  rather than re-dispatching a fresh run.

The graph state holds only JSON-friendly primitives (approvals/steps as
dicts) so checkpoint serialization never depends on pickling domain
dataclasses — the nodes convert to/from domain types at the port boundary.
Risk assessment and auto-revocation (ADR-058) run once, after the graph
finishes — the same place enrichment ran in the example, no branching needed.
"""

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from aiagent.application.execute_revocations import revoke_dangerous
from aiagent.application.run_scan import resolve_approvals
from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    AgentStepKind,
    ApprovalFinding,
    AskAction,
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

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- state + serialization


class GraphState(TypedDict):
    """Checkpointed state — JSON-friendly only (approvals/steps as dicts)."""

    goal: str
    wallet_address: str
    clarification: str | None
    asked: bool
    approvals: list[dict[str, Any]]
    collected: list[str]  # canonical approval keys already seen
    steps: list[dict[str, Any]]
    next: dict[str, Any]  # the decided action: {"kind", "chain_id"/"question", "reason"}


def _approval_to_dict(approval: RawApproval) -> dict[str, Any]:
    return {
        "chain_id": approval.chain_id,
        "token_address": approval.token_address,
        "token_symbol": approval.token_symbol,
        "spender_address": approval.spender_address,
        "approved_amount": approval.approved_amount,
        "spender_name": approval.spender_name,
        "raw": approval.raw,
    }


def _approval_from_dict(data: dict[str, Any]) -> RawApproval:
    return RawApproval(
        chain_id=data["chain_id"],
        token_address=data["token_address"],
        token_symbol=data["token_symbol"],
        spender_address=data["spender_address"],
        approved_amount=data["approved_amount"],
        spender_name=data.get("spender_name"),
        raw=data.get("raw") or {},
    )


def _step_to_dict(step: AgentStep) -> dict[str, Any]:
    return {
        "seq": step.seq,
        "kind": step.kind.value,
        "detail": step.detail,
        "reason": step.reason,
        "new_hits": step.new_hits,
    }


def _step_from_dict(data: dict[str, Any]) -> AgentStep:
    return AgentStep(
        seq=data["seq"],
        kind=AgentStepKind(data["kind"]),
        detail=data["detail"],
        reason=data["reason"],
        new_hits=data["new_hits"],
    )


def _scans_done(steps: list[dict[str, Any]]) -> int:
    return sum(1 for s in steps if s["kind"] == AgentStepKind.SCAN.value)


# ---------------------------------------------------------------- graph construction


def _build_graph(
    job_id: str,
    source: ApprovalSource,
    policy: AgentPolicy,
    reporter: StepReporter,
    has_clarifier: bool,
    max_steps: int,
    budget: SpendGuard | None,
) -> "StateGraph[GraphState]":
    """Builds the StateGraph; nodes close over the injected ports. The graph is
    rebuilt per task (fresh ports), while the checkpointer restores the state —
    exactly the split we want across a HITL pause spanning two Celery tasks."""

    def _report(step: AgentStep) -> None:
        """The journal is cosmetic (ADR-030): a failed report never fails the job."""
        try:
            reporter.report_step(job_id, step)
        except Exception:  # noqa: BLE001 - best effort by contract
            logger.warning("failed to report agent step", extra={"job_id": job_id}, exc_info=True)

    def _goal_with_clarification(state: GraphState) -> str:
        if state["clarification"]:
            return f'{state["goal"]} (user clarification: "{state["clarification"]}")'
        return state["goal"]

    def decide(state: GraphState) -> dict[str, Any]:
        # Step budget (ADR-030): once the scan budget is spent, finish
        # without asking the policy again.
        if _scans_done(state["steps"]) >= max_steps:
            return {"next": {"kind": "finish", "reason": f"step budget of {max_steps} exhausted"}}
        # Spend cap (ADR-048): money can stop the run before the step budget.
        if budget is not None and budget.exceeded():
            return {
                "next": {
                    "kind": "finish",
                    "reason": f"cost budget of ${budget.cap_usd:.2f} exhausted",
                }
            }
        action = policy.decide(
            _goal_with_clarification(state),
            [_step_from_dict(s) for s in state["steps"]],
            [_approval_from_dict(a) for a in state["approvals"]],
        )
        action = _apply_ask_guard(action, state, has_clarifier)
        return {"next": _action_to_dict(action)}

    def route(state: GraphState) -> str:
        return str(state["next"]["kind"])

    def do_scan(state: GraphState) -> dict[str, Any]:
        chain_id, reason = state["next"]["chain_id"], state["next"]["reason"]
        try:
            found = source.fetch_approvals(state["wallet_address"], chain_id)
        except Exception as exc:  # noqa: BLE001 - one chain must not sink the run
            # ADR-064: record the gap and keep going. The graph advances with
            # no new approvals, and the `degraded` step keeps the run from
            # reading as a clean sweep of a chain nobody reached.
            logger.warning("chain %s could not be scanned: %s", chain_id, exc)
            step = AgentStep(
                seq=len(state["steps"]) + 1,
                kind=AgentStepKind.DEGRADED,
                detail=chain_id,
                reason=f"chain not scanned: {exc}",
                new_hits=0,
            )
            _report(step)
            return {"steps": state["steps"] + [_step_to_dict(step)]}
        seen = set(state["collected"])
        new = [
            a
            for a in found
            if raw_approval_key(a.chain_id, a.token_address, a.spender_address) not in seen
        ]
        step = AgentStep(
            seq=len(state["steps"]) + 1,
            kind=AgentStepKind.SCAN,
            detail=chain_id,
            reason=reason,
            new_hits=len(new),
        )
        _report(step)
        return {
            "approvals": state["approvals"] + [_approval_to_dict(a) for a in new],
            "collected": state["collected"]
            + [raw_approval_key(a.chain_id, a.token_address, a.spender_address) for a in new],
            "steps": state["steps"] + [_step_to_dict(step)],
        }

    def do_ask(state: GraphState) -> dict[str, Any]:
        # HITL (ADR-032): pause the graph until the user answers. On resume the
        # answer flows back here; the backend callback is issued by the caller
        # when it sees the interrupt (so it fires once, not again on resume).
        answer = interrupt(state["next"]["question"])
        return {"clarification": answer, "asked": True}

    def finalize(state: GraphState) -> dict[str, Any]:
        step = AgentStep(
            seq=len(state["steps"]) + 1,
            kind=AgentStepKind.FINISH,
            detail="",
            reason=state["next"]["reason"],
        )
        _report(step)
        return {"steps": state["steps"] + [_step_to_dict(step)]}

    graph: StateGraph[GraphState] = StateGraph(GraphState)
    graph.add_node("decide", decide)
    graph.add_node("scan", do_scan)
    graph.add_node("ask", do_ask)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "decide")
    graph.add_conditional_edges(
        "decide", route, {"scan": "scan", "ask": "ask", "finish": "finalize"}
    )
    graph.add_edge("scan", "decide")
    graph.add_edge("ask", "decide")
    graph.add_edge("finalize", END)
    return graph


def _action_to_dict(action: AgentAction) -> dict[str, Any]:
    if isinstance(action, ScanAction):
        return {"kind": "scan", "chain_id": action.chain_id, "reason": action.reason}
    if isinstance(action, AskAction):
        return {"kind": "ask", "question": action.question, "reason": action.reason}
    return {"kind": "finish", "reason": action.reason}


def _apply_ask_guard(action: AgentAction, state: GraphState, has_clarifier: bool) -> AgentAction:
    """One clarification per job (ADR-032): a repeat ask — or an ask with no
    clarifier wired — degrades to a finish, so the loop never ping-pongs."""
    if isinstance(action, AskAction) and (
        not has_clarifier or state["asked"] or state["clarification"] is not None
    ):
        from aiagent.domain.models import FinishAction

        return FinishAction(
            reason="the policy asked for clarification again; finishing with what was found"
        )
    return action


# ---------------------------------------------------------------- entry point


def run_agent_graph(
    job_id: str,
    goal: str,
    wallet_address: str,
    source: ApprovalSource,
    threat_intel: ThreatIntel,
    policy: AgentPolicy,
    sink: ResultSink,
    reporter: StepReporter,
    checkpointer: "BaseCheckpointSaver[Any]",
    revoker: ApprovalRevoker | None = None,
    clarifier: ClarificationRequester | None = None,
    clarification: str | None = None,
    seen_keys: set[str] | None = None,
    page_dates: None = None,  # unused (no date cascade in this domain); kept for call-site parity
    max_steps: int = 5,
    resume_answer: str | None = None,
    budget: SpendGuard | None = None,
) -> list[ApprovalFinding] | None:
    """Runs the agent mode on a LangGraph StateGraph (ADR-046/058), then
    assesses, sorts, auto-revokes and delivers — same contract as
    `run_agent_scan`. Returns the findings, or None when the graph paused on
    a clarification (ADR-032).

    `resume_answer` set means the user answered a pending clarification: the
    graph resumes from its Redis checkpoint instead of starting fresh.
    """
    config: dict[str, Any] = {"configurable": {"thread_id": job_id}}
    # The compiled Pregel graph's invoke/get_state have intricate overloads;
    # typed Any here since this adapter drives it as plain glue.
    compiled: Any = _build_graph(
        job_id, source, policy, reporter, clarifier is not None, max_steps, budget
    ).compile(checkpointer=checkpointer)

    try:
        sink.mark_started(job_id)

        resuming = resume_answer is not None and _has_checkpoint(compiled, config)
        if resuming:
            outcome = compiled.invoke(Command(resume=resume_answer), config)
        else:
            # Fresh run. An already-known clarification (the loop's model, or a
            # resume with no checkpoint to restore) starts the run informed and
            # marks the question as spent.
            informed = clarification if clarification is not None else resume_answer
            outcome = compiled.invoke(
                {
                    "goal": goal,
                    "wallet_address": wallet_address,
                    "clarification": informed,
                    "asked": informed is not None,
                    "approvals": [],
                    "collected": [],
                    "steps": [],
                    "next": {},
                },
                config,
            )

        # An interrupt (ADR-032 pause) surfaces in the invoke return itself —
        # the reliable in-process signal. Reading it back from get_state()
        # instead raced against the Redis checkpoint write and could miss the
        # pause, delivering the (empty) partial state as a completed job.
        question = _interrupt_question(outcome)
        if question is not None:
            assert clarifier is not None
            clarifier.request_clarification(job_id, question)
            return None

        return _deliver(job_id, outcome, threat_intel, revoker, sink, seen_keys, reporter)
    except Exception as exc:
        try:
            sink.report_failure(job_id, str(exc))
        except Exception:  # noqa: BLE001 - keep the original error as the cause
            pass
        raise


def _has_checkpoint(compiled: Any, config: dict[str, Any]) -> bool:
    # Resume path only: read a checkpoint written by an earlier, fully finished
    # task (the paused run) — safely flushed, unlike a same-invoke read-back.
    return compiled.get_state(config).created_at is not None


def _interrupt_question(outcome: Any) -> str | None:
    """The pending clarification question if the graph paused, from the invoke
    return value (`__interrupt__`), else None."""
    if isinstance(outcome, dict):
        interrupts = outcome.get("__interrupt__")
        if interrupts:
            return str(interrupts[0].value)
    return None


def _deliver(
    job_id: str,
    state: dict[str, Any],
    threat_intel: ThreatIntel,
    revoker: ApprovalRevoker | None,
    sink: ResultSink,
    seen_keys: set[str] | None,
    reporter: StepReporter,
) -> list[ApprovalFinding]:
    """Shared tail with the loop: assess, sort, auto-revoke, flag the
    recurring delta, and deliver (ADR-033/058)."""
    # ADR-064: if every chain the graph attempted was unreachable, nothing was
    # inspected. Delivering an empty result set would render as "no dangerous
    # approvals" — a clean bill of health for a wallet nobody looked at.
    kinds = [step.get("kind") for step in state["steps"]]
    if AgentStepKind.DEGRADED in kinds and AgentStepKind.SCAN not in kinds:
        # The reason carries the provider's own error; without it the job's
        # error field says only "no chain could be scanned", which tells an
        # operator nothing they can act on.
        degraded = [
            f"{step.get('detail')}: {step.get('reason')}"
            for step in state["steps"]
            if step.get("kind") == AgentStepKind.DEGRADED
        ]
        raise RuntimeError(
            f"no chain could be scanned ({'; '.join(degraded)}) — "
            "refusing to report a wallet as clean"
        )

    approvals = [_approval_from_dict(a) for a in state["approvals"]]
    findings = sort_by_risk(resolve_approvals(approvals, threat_intel))

    # `revoke_dangerous` appends one step per revocation to this list, so it has
    # to be the thing the report step numbers itself against. Deriving the seq
    # from `state["steps"]` instead looked equivalent and was not: the graph
    # state does not see the revoke steps, so the report reused the first
    # revocation's seq, and the callback is idempotent on seq (ADR-030) — the
    # delta report was silently dropped on every recurring run that revoked
    # anything.
    steps = [_step_from_dict(s) for s in state["steps"]]

    if revoker is not None:
        findings = revoke_dangerous(
            job_id, str(state["wallet_address"]), findings, revoker, reporter, steps
        )

    if seen_keys is not None:
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
        try:
            reporter.report_step(job_id, report)
        except Exception:  # noqa: BLE001 - best effort by contract
            logger.warning("failed to report delta step", extra={"job_id": job_id}, exc_info=True)
    sink.deliver(job_id, findings)
    return findings
