"""LangGraph orchestration (ADR-046/058): parity with the hand-rolled loop,
plus the checkpointed interrupt/resume HITL. Driven with scripted ports and
an in-memory checkpointer — deterministic, no I/O, no paid call."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from aiagent.adapters.orchestration.langgraph_agent import run_agent_graph
from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    AgentStepKind,
    ApprovalFinding,
    AskAction,
    FinishAction,
    RawApproval,
    RevocationStatus,
    RiskAssessment,
    RiskTier,
    ScanAction,
)
from aiagent.domain.usage import Pricing, SpendGuard, UsageMeter


class ScriptedPolicy:
    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = list(actions)
        self.seen: list[tuple[int, int]] = []

    def decide(
        self, goal: str, steps: list[AgentStep], approvals: list[RawApproval]
    ) -> AgentAction:
        self.seen.append((len(steps), len(approvals)))
        self.last_goal = goal
        return self._actions.pop(0)


class MappedSource:
    def __init__(self, by_chain: dict[str, list[RawApproval]]) -> None:
        self._by_chain = by_chain
        self.chain_ids: list[str] = []

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
        self.chain_ids.append(chain_id)
        return self._by_chain.get(chain_id, [])


class NeutralThreatIntel:
    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        return [RiskAssessment() for _ in approvals]


class TieredThreatIntel:
    def __init__(self, tiers: dict[str, RiskTier] | None = None) -> None:
        self._tiers = tiers or {}

    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        return [
            RiskAssessment(tier=self._tiers.get(a.spender_address, RiskTier.SAFE))
            for a in approvals
        ]


class RecordingRevoker:
    """Always succeeds with a synthetic tx hash unless `fail_for` names the spender."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self._fail_for = fail_for or set()
        self.revoked: list[str] = []

    def revoke(self, finding: ApprovalFinding) -> ApprovalFinding:
        from dataclasses import replace

        self.revoked.append(finding.spender_address)
        if finding.spender_address in self._fail_for:
            return replace(finding, revocation_status=RevocationStatus.FAILED)
        return replace(
            finding, revocation_status=RevocationStatus.REVOKED, revocation_tx_hash="0xtx"
        )


class RecordingSink:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.delivered: list[tuple[str, int]] = []
        self.failures: list[tuple[str, str]] = []
        self.results: list = []

    def mark_started(self, job_id: str) -> None:
        self.started.append(job_id)

    def deliver(self, job_id: str, results) -> None:  # type: ignore[no-untyped-def]
        self.delivered.append((job_id, len(results)))
        self.results = results

    def report_failure(self, job_id: str, error: str) -> None:
        self.failures.append((job_id, error))


class RecordingReporter:
    def __init__(self) -> None:
        self.steps: list[AgentStep] = []

    def report_step(self, job_id: str, step: AgentStep) -> None:
        self.steps.append(step)


class RecordingClarifier:
    def __init__(self) -> None:
        self.questions: list[tuple[str, str]] = []

    def request_clarification(self, job_id: str, question: str) -> None:
        self.questions.append((job_id, question))


def approval(spender: str, chain_id: str = "1") -> RawApproval:
    return RawApproval(
        chain_id=chain_id,
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address=spender,
        approved_amount="Unlimited",
    )


def run(job_id, goal, source, policy, sink, reporter, checkpointer=None, **kw):  # type: ignore[no-untyped-def]
    threat_intel = kw.pop("threat_intel", NeutralThreatIntel())
    return run_agent_graph(
        job_id,
        goal,
        "0xwallet",
        source,
        threat_intel,
        policy,
        sink,
        reporter,
        checkpointer or InMemorySaver(),
        **kw,
    )


# ---------------------------------------------------------------- parity


def test_scans_multiple_chains_and_finishes() -> None:
    source = MappedSource(
        {
            "1": [approval("a"), approval("b")],
            "8453": [approval("b", "8453"), approval("c", "8453")],
        }
    )
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="1", reason="start"),
            ScanAction(chain_id="8453", reason="refine"),
            FinishAction(reason="coverage sufficient"),
        ]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run("job-1", "goal", source, policy, sink, reporter, max_steps=5)

    assert source.chain_ids == ["1", "8453"]
    # "b" is a distinct approval per chain (dedup keys on chain+token+spender)
    # — the same spender address on two chains is not a duplicate.
    assert len(results) == 4
    assert sink.started == ["job-1"] and sink.delivered == [("job-1", 4)]
    assert [(s.seq, s.kind, s.detail, s.new_hits) for s in reporter.steps] == [
        (1, AgentStepKind.SCAN, "1", 2),
        (2, AgentStepKind.SCAN, "8453", 2),
        (3, AgentStepKind.FINISH, "", 0),
    ]


def test_deduplicates_a_redundant_rescan_of_the_same_chain() -> None:
    source = MappedSource({"1": [approval("a"), approval("b")]})
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="1", reason="start"),
            ScanAction(chain_id="1", reason="rescan"),  # redundant
            FinishAction(reason="coverage sufficient"),
        ]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run("job-1b", "goal", source, policy, sink, reporter, max_steps=5)

    assert source.chain_ids == ["1", "1"]
    assert {r.spender_address for r in results} == {"a", "b"}
    assert sink.delivered == [("job-1b", 2)]
    assert [(s.kind, s.new_hits) for s in reporter.steps] == [
        (AgentStepKind.SCAN, 2),
        (AgentStepKind.SCAN, 0),
        (AgentStepKind.FINISH, 0),
    ]


def test_budget_exhaustion_forces_a_finish_step() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="1"), ScanAction(chain_id="1", reason="2")]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    run("job-2", "goal", source, policy, sink, reporter, max_steps=2)

    assert [s.kind for s in reporter.steps] == [
        AgentStepKind.SCAN,
        AgentStepKind.SCAN,
        AgentStepKind.FINISH,
    ]
    assert "budget" in reporter.steps[-1].reason
    assert sink.delivered == [("job-2", 1)]


def test_dangerous_findings_are_auto_revoked_after_the_scan() -> None:
    source = MappedSource({"1": [approval("safe"), approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    revoker = RecordingRevoker()
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run(
        "job-5",
        "goal",
        source,
        policy,
        sink,
        reporter,
        threat_intel=TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        revoker=revoker,
        max_steps=5,
    )

    assert revoker.revoked == ["dangerous"]
    by_spender = {r.spender_address: r for r in results}
    assert by_spender["dangerous"].revocation_status == RevocationStatus.REVOKED
    assert by_spender["safe"].revocation_status == RevocationStatus.NOT_ATTEMPTED
    assert reporter.steps[-1].kind is AgentStepKind.REVOKE


def test_revocation_failure_is_recorded_not_raised() -> None:
    source = MappedSource({"1": [approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    revoker = RecordingRevoker(fail_for={"dangerous"})
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run(
        "job-6",
        "goal",
        source,
        policy,
        sink,
        reporter,
        threat_intel=TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        revoker=revoker,
        max_steps=5,
    )

    assert results is not None and results[0].revocation_status == RevocationStatus.FAILED
    assert "failed" in reporter.steps[-1].reason


def test_no_revoker_means_no_execution() -> None:
    source = MappedSource({"1": [approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run(
        "job-7",
        "goal",
        source,
        policy,
        sink,
        reporter,
        threat_intel=TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        revoker=None,
        max_steps=5,
    )

    assert results is not None and results[0].revocation_status == RevocationStatus.NOT_ATTEMPTED
    assert all(s.kind is not AgentStepKind.REVOKE for s in reporter.steps)


# ---------------------------------------------------------------- spend cap (ADR-048)


class _BurningSource:
    """Burns a fixed LLM cost into the meter per call, so a cost cap can be
    exercised with no paid provider — parity with the loop's BurningSource."""

    def __init__(self, approvals: list[RawApproval], meter: UsageMeter, tokens: int) -> None:
        self._approvals = approvals
        self._meter = meter
        self._tokens = tokens
        self.chain_ids: list[str] = []

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
        self.chain_ids.append(chain_id)
        self._meter.record_llm(self._tokens, 0)
        return list(self._approvals)


_BURN_PRICING = Pricing(llm_input_per_mtok=25.0, llm_output_per_mtok=0.0, search_per_call=0.0)


def test_cost_cap_forces_a_finish_step() -> None:
    meter = UsageMeter()
    guard = SpendGuard(meter, _BURN_PRICING, cap_usd=0.03)  # trips after 2 scans
    source = _BurningSource([approval("a")], meter, tokens=1_000)  # $0.025 per scan
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason=str(i)) for i in range(5)])
    sink, reporter = RecordingSink(), RecordingReporter()

    run("job-c1", "goal", source, policy, sink, reporter, budget=guard, max_steps=5)

    assert source.chain_ids == ["1", "1"]  # step budget 5 untouched; money stops it
    assert [s.kind for s in reporter.steps] == [
        AgentStepKind.SCAN,
        AgentStepKind.SCAN,
        AgentStepKind.FINISH,
    ]
    assert "cost" in reporter.steps[-1].reason
    assert sink.delivered == [("job-c1", 1)]


def test_recurring_run_flags_the_delta_and_journals_a_report() -> None:
    source = MappedSource({"1": [approval("old"), approval("fresh")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run(
        "job-12", "goal", source, policy, sink, reporter, seen_keys={"1:0xtoken:old"}, max_steps=5
    )

    assert {r.spender_address: r.is_new for r in results} == {"old": False, "fresh": True}
    report = reporter.steps[-1]
    assert report.kind is AgentStepKind.REPORT and report.new_hits == 1


def test_the_delta_report_does_not_reuse_a_revocation_seq() -> None:
    """The `/steps` callback is idempotent on seq (ADR-030), so a report that
    reuses a revoke step's seq is silently dropped by the backend. The report
    numbered itself from the graph state, which never sees the revoke steps —
    so a recurring run that revoked anything lost its delta report entirely.
    Only reproducible with a revoker present, which the test above has not."""
    source = MappedSource({"1": [approval("old"), approval("bad")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    run(
        "job-13",
        "goal",
        source,
        policy,
        sink,
        reporter,
        threat_intel=TieredThreatIntel({"bad": RiskTier.DANGEROUS}),
        revoker=RecordingRevoker(),
        seen_keys={"1:0xtoken:old"},
        max_steps=5,
    )

    seqs = [s.seq for s in reporter.steps]
    assert len(seqs) == len(set(seqs)), f"duplicate step seq: {seqs}"
    assert reporter.steps[-1].kind is AgentStepKind.REPORT
    assert any(s.kind is AgentStepKind.REVOKE for s in reporter.steps)


def test_scan_failure_reports_and_propagates() -> None:
    class ExplodingSource:
        def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
            raise RuntimeError("GoPlus down")

    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r")])
    sink = RecordingSink()

    with pytest.raises(Exception, match="GoPlus down"):
        run("job-4", "goal", ExplodingSource(), policy, sink, RecordingReporter(), max_steps=5)
    assert sink.failures == [("job-4", "GoPlus down")] or sink.failures[-1][0] == "job-4"


# ---------------------------------------------------------------- HITL (ADR-032/046)


def test_ask_pauses_the_job_without_delivering() -> None:
    source = MappedSource({})
    policy = ScriptedPolicy([AskAction(question="Which chains?", reason="ambiguous")])
    sink, reporter, clarifier = RecordingSink(), RecordingReporter(), RecordingClarifier()

    outcome = run("job-9", "goal", source, policy, sink, reporter, clarifier=clarifier, max_steps=5)

    assert outcome is None
    assert clarifier.questions == [("job-9", "Which chains?")]
    assert sink.delivered == [] and sink.failures == []


def test_answer_resumes_the_graph_from_its_checkpoint() -> None:
    # The heart of ADR-046: the run pauses on a question, and the user's answer
    # resumes the SAME graph — the scan done before the pause is preserved,
    # not redone.
    cp = InMemorySaver()
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy(
        [
            AskAction(question="Which chains?", reason="ambiguous"),
            ScanAction(chain_id="1", reason="ethereum, per the user"),
            FinishAction(reason="done"),
        ]
    )
    sink, reporter, clarifier = RecordingSink(), RecordingReporter(), RecordingClarifier()

    paused = run(
        "job-r", "goal", source, policy, sink, reporter, checkpointer=cp, clarifier=clarifier
    )
    assert paused is None and clarifier.questions == [("job-r", "Which chains?")]

    # Same job_id + same checkpointer = resume; the answer flows into the graph.
    results = run(
        "job-r",
        "goal",
        source,
        policy,
        sink,
        reporter,
        checkpointer=cp,
        clarifier=clarifier,
        resume_answer="ethereum only",
    )

    assert results is not None and [r.spender_address for r in results] == ["a"]
    assert sink.delivered == [("job-r", 1)]
    assert "ethereum only" in policy.last_goal


def test_ask_without_a_clarifier_degrades_to_finish() -> None:
    # No `clarifier` wired at all (e.g. workflow-mode-style plumbing): an ask
    # must not hang the graph waiting on an interrupt nobody can answer.
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="r"), AskAction(question="hm?", reason="r")]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    outcome = run("job-11", "goal", source, policy, sink, reporter, max_steps=5)

    assert outcome is not None and sink.delivered == [("job-11", 1)]


def test_ask_after_an_answer_degrades_to_finish() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="r"), AskAction(question="again?", reason="r")]
    )
    sink, reporter, clarifier = RecordingSink(), RecordingReporter(), RecordingClarifier()

    outcome = run(
        "job-10",
        "goal",
        source,
        policy,
        sink,
        reporter,
        clarifier=clarifier,
        clarification="ethereum only",
        max_steps=5,
    )

    assert outcome is not None and len(outcome) == 1
    assert clarifier.questions == []
    assert reporter.steps[-1].kind is AgentStepKind.FINISH
