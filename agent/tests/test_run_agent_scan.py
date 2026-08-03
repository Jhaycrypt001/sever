"""The agentic loop (ADR-030/058), tested with a scripted policy and port fakes."""

import pytest

from aiagent.application.run_agent_scan import run_agent_scan
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
    """Plays a fixed list of decisions, recording what it was shown."""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = list(actions)
        self.seen: list[tuple[list[AgentStep], int]] = []

    def decide(
        self, goal: str, steps: list[AgentStep], approvals: list[RawApproval]
    ) -> AgentAction:
        self.seen.append((list(steps), len(approvals)))
        return self._actions.pop(0)


class MappedSource:
    """Returns canned approvals per chain; unknown chains return nothing."""

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
    """Assigns a tier per spender address (default SAFE)."""

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

    def revoke(self, finding: ApprovalFinding, wallet_address: str) -> ApprovalFinding:
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
        self.results: list[ApprovalFinding] = []

    def mark_started(self, job_id: str) -> None:
        self.started.append(job_id)

    def deliver(self, job_id: str, results: list[ApprovalFinding]) -> None:  # type: ignore[no-untyped-def]
        self.delivered.append((job_id, len(results)))
        self.results = results

    def report_failure(self, job_id: str, error: str) -> None:
        self.failures.append((job_id, error))


class RecordingReporter:
    def __init__(self, fail: bool = False) -> None:
        self.steps: list[AgentStep] = []
        self._fail = fail

    def report_step(self, job_id: str, step: AgentStep) -> None:
        if self._fail:
            raise RuntimeError("journal endpoint down")
        self.steps.append(step)


def approval(spender: str, chain_id: str = "1") -> RawApproval:
    return RawApproval(
        chain_id=chain_id,
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address=spender,
        approved_amount="Unlimited",
    )


def test_loop_scans_multiple_chains_and_finishes() -> None:
    source = MappedSource(
        {
            "1": [approval("a"), approval("b")],
            "8453": [approval("b", "8453"), approval("c", "8453")],
        }
    )
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="1", reason="start with Ethereum"),
            ScanAction(chain_id="8453", reason="also check Base"),
            FinishAction(reason="coverage sufficient"),
        ]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-1",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        max_steps=5,
    )

    assert source.chain_ids == ["1", "8453"]
    # "b" is a distinct approval per chain (dedup keys on chain+token+spender,
    # ADR-034 equivalent) — the same spender address on two chains is not a
    # duplicate, so all four are kept.
    assert results is not None and len(results) == 4
    assert sink.started == ["job-1"] and sink.delivered == [("job-1", 4)]
    kinds = [(s.seq, s.kind, s.detail, s.new_hits) for s in reporter.steps]
    assert kinds == [
        (1, AgentStepKind.SCAN, "1", 2),
        (2, AgentStepKind.SCAN, "8453", 2),
        (3, AgentStepKind.FINISH, "", 0),
    ]
    assert [(len(steps), n) for steps, n in policy.seen] == [(0, 0), (1, 2), (2, 4)]


def test_loop_deduplicates_a_redundant_rescan_of_the_same_chain() -> None:
    source = MappedSource({"1": [approval("a"), approval("b")]})
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="1", reason="start with Ethereum"),
            ScanAction(chain_id="1", reason="rescan Ethereum"),  # redundant
            FinishAction(reason="coverage sufficient"),
        ]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-2",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        max_steps=5,
    )

    assert source.chain_ids == ["1", "1"]
    assert results is not None and {r.spender_address for r in results} == {"a", "b"}
    assert sink.delivered == [("job-2", 2)]
    kinds = [(s.kind, s.new_hits) for s in reporter.steps]
    assert kinds == [
        (AgentStepKind.SCAN, 2),
        (AgentStepKind.SCAN, 0),  # the rescan adds nothing new
        (AgentStepKind.FINISH, 0),
    ]


def test_budget_exhaustion_forces_a_finish_step() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="1"), ScanAction(chain_id="1", reason="2")]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    run_agent_scan(
        "job-2",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        max_steps=2,
    )

    kinds = [s.kind for s in reporter.steps]
    assert kinds == [AgentStepKind.SCAN, AgentStepKind.SCAN, AgentStepKind.FINISH]
    assert "budget" in reporter.steps[-1].reason


# ---------------------------------------------------------------- spend cap (ADR-048)


class BurningSource:
    """A source that also burns a fixed LLM cost into the meter on every call —
    so a cost cap can be exercised without a real, paid provider."""

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


def test_cost_cap_forces_a_finish_and_delivers_what_was_found() -> None:
    meter = UsageMeter()
    guard = SpendGuard(meter, _BURN_PRICING, cap_usd=0.03)  # trips after 2 scans ($0.05)
    source = BurningSource([approval("a")], meter, tokens=1_000)
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason=str(i)) for i in range(5)]  # would never stop on its own
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    run_agent_scan(
        "job-c1",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        budget=guard,
        max_steps=5,
    )

    assert source.chain_ids == ["1", "1"]
    assert [s.kind for s in reporter.steps] == [
        AgentStepKind.SCAN,
        AgentStepKind.SCAN,
        AgentStepKind.FINISH,
    ]
    assert "cost" in reporter.steps[-1].reason
    assert sink.delivered == [("job-c1", 1)]


def test_cost_cap_skips_revocation_when_already_over_budget() -> None:
    meter = UsageMeter()
    guard = SpendGuard(meter, _BURN_PRICING, cap_usd=0.02)  # trips after 1 scan ($0.025)
    source = BurningSource([approval("a")], meter, tokens=1_000)
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="1"), ScanAction(chain_id="1", reason="2")]
    )
    revoker = RecordingRevoker()
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-c2",
        "goal",
        "0xwallet",
        source,
        TieredThreatIntel({"a": RiskTier.DANGEROUS}),
        policy,
        sink,
        reporter,
        revoker=revoker,
        budget=guard,
        max_steps=5,
    )

    assert revoker.revoked == []  # the extra revoke call is skipped over budget
    assert results is not None and results[0].revocation_status == RevocationStatus.NOT_ATTEMPTED


def test_no_cap_when_the_budget_is_absent() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-c3",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        max_steps=5,
    )

    assert results is not None and len(results) == 1
    assert reporter.steps[-1].kind is AgentStepKind.FINISH


def test_a_failing_journal_never_fails_the_job() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink = RecordingSink()

    results = run_agent_scan(
        "job-3",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        RecordingReporter(fail=True),
        max_steps=5,
    )

    assert results is not None and len(results) == 1 and sink.delivered == [("job-3", 1)]
    assert sink.failures == []


def test_scan_failure_reports_and_propagates() -> None:
    """Every chain unreachable still fails the job (ADR-064 kept this).

    What changed is *when*: a failing chain no longer aborts the loop on the
    spot, so the policy is allowed to try the others first. The run only fails
    once it turns out nothing was scanned at all — and the cause still travels
    with it, because "no chain could be scanned" alone is unactionable.
    """

    class ExplodingSource:
        def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
            raise RuntimeError("GoPlus down")

    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="r"), FinishAction(reason="nothing worked")]
    )
    sink = RecordingSink()

    with pytest.raises(RuntimeError, match="GoPlus down"):
        run_agent_scan(
            "job-4",
            "goal",
            "0xwallet",
            ExplodingSource(),
            NeutralThreatIntel(),
            policy,
            sink,
            RecordingReporter(),
            max_steps=5,
        )
    assert sink.failures and "GoPlus down" in sink.failures[0][1]
    assert sink.delivered == []


# ---------------------------------------------------------------- auto-revocation (ADR-058)


def test_dangerous_findings_are_auto_revoked_after_the_scan() -> None:
    source = MappedSource({"1": [approval("safe"), approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    revoker = RecordingRevoker()
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-5",
        "goal",
        "0xwallet",
        source,
        TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        policy,
        sink,
        reporter,
        revoker=revoker,
        max_steps=5,
    )

    assert revoker.revoked == ["dangerous"]  # only the dangerous one is touched
    assert results is not None
    by_spender = {r.spender_address: r for r in results}
    assert by_spender["dangerous"].revocation_status == RevocationStatus.REVOKED
    assert by_spender["dangerous"].revocation_tx_hash == "0xtx"
    assert by_spender["safe"].revocation_status == RevocationStatus.NOT_ATTEMPTED
    revoke_step = reporter.steps[-1]
    assert revoke_step.kind is AgentStepKind.REVOKE and "0xtx" in revoke_step.reason


def test_revocation_failure_is_recorded_not_raised() -> None:
    source = MappedSource({"1": [approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    revoker = RecordingRevoker(fail_for={"dangerous"})
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-6",
        "goal",
        "0xwallet",
        source,
        TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        policy,
        sink,
        reporter,
        revoker=revoker,
        max_steps=5,
    )

    assert results is not None and results[0].revocation_status == RevocationStatus.FAILED
    assert "failed" in reporter.steps[-1].reason


def test_no_revoker_means_no_execution() -> None:
    source = MappedSource({"1": [approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-7",
        "goal",
        "0xwallet",
        source,
        TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        policy,
        sink,
        reporter,
        revoker=None,
        max_steps=5,
    )

    assert results is not None and results[0].revocation_status == RevocationStatus.NOT_ATTEMPTED
    assert all(s.kind is not AgentStepKind.REVOKE for s in reporter.steps)


# ---------------------------------------------------------------- clarification (ADR-032)


class RecordingClarifier:
    def __init__(self) -> None:
        self.questions: list[tuple[str, str]] = []

    def request_clarification(self, job_id: str, question: str) -> None:
        self.questions.append((job_id, question))


def test_ask_pauses_the_job_without_delivering() -> None:
    source = MappedSource({})
    policy = ScriptedPolicy([AskAction(question="Which chains?", reason="ambiguous")])
    sink, reporter, clarifier = RecordingSink(), RecordingReporter(), RecordingClarifier()

    outcome = run_agent_scan(
        "job-9",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        clarifier=clarifier,
        max_steps=5,
    )

    assert outcome is None  # paused: nothing delivered, no failure
    assert clarifier.questions == [("job-9", "Which chains?")]
    assert sink.delivered == [] and sink.failures == []
    assert source.chain_ids == []


def test_ask_after_an_answer_degrades_to_finish() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="r"), AskAction(question="again?", reason="r")]
    )
    sink, reporter, clarifier = RecordingSink(), RecordingReporter(), RecordingClarifier()

    outcome = run_agent_scan(
        "job-10",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        clarifier=clarifier,
        clarification="ethereum only",
        max_steps=5,
    )

    assert outcome is not None and len(outcome) == 1  # delivered normally
    assert clarifier.questions == []
    assert reporter.steps[-1].kind is AgentStepKind.FINISH


def test_ask_without_a_clarifier_degrades_to_finish() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy(
        [ScanAction(chain_id="1", reason="r"), AskAction(question="hm?", reason="r")]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    outcome = run_agent_scan(
        "job-11",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        max_steps=5,
    )

    assert outcome is not None and sink.delivered == [("job-11", 1)]


# ---------------------------------------------------------------- recurring memory (ADR-033)


def test_recurring_run_flags_the_delta_and_journals_a_report() -> None:
    source = MappedSource({"1": [approval("old"), approval("fresh")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-12",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        seen_keys={"1:0xtoken:old"},
        max_steps=5,
    )

    assert results is not None
    assert {r.spender_address: r.is_new for r in results} == {"old": False, "fresh": True}
    report = reporter.steps[-1]
    assert report.kind is AgentStepKind.REPORT
    assert report.reason == "1 new finding(s) since the last scan"


def test_one_shot_runs_have_no_report_step_and_stay_new() -> None:
    source = MappedSource({"1": [approval("a")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-14",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        sink,
        reporter,
        max_steps=5,
    )

    assert results is not None and all(r.is_new for r in results)
    assert all(s.kind is not AgentStepKind.REPORT for s in reporter.steps)


# ---------------------------------------------------------------- chain resilience (ADR-064)


class FlakySource:
    """Fails on the named chains, returns canned approvals for the rest."""

    def __init__(
        self, by_chain: dict[str, list[RawApproval]], failing: dict[str, Exception]
    ) -> None:
        self._by_chain = by_chain
        self._failing = failing
        self.chain_ids: list[str] = []

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
        self.chain_ids.append(chain_id)
        if chain_id in self._failing:
            raise self._failing[chain_id]
        return self._by_chain.get(chain_id, [])


def test_a_failing_chain_becomes_a_degraded_step_and_the_loop_continues() -> None:
    # ADR-064: the loop used to die on the first provider error, throwing away
    # every finding collected before it.
    source = FlakySource(
        {"1": [approval("a", "1")]},
        {"8453": RuntimeError("GoPlus error (code 4029): rate limited")},
    )
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="8453", reason="base first"),
            ScanAction(chain_id="1", reason="then ethereum"),
            FinishAction(reason="done"),
        ]
    )
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-1", "goal", "0xwallet", source, NeutralThreatIntel(), policy, sink, reporter
    )

    assert results is not None
    assert [f.spender_address for f in results] == ["a"]
    kinds = [s.kind for s in reporter.steps]
    assert AgentStepKind.DEGRADED in kinds
    assert AgentStepKind.SCAN in kinds
    degraded = next(s for s in reporter.steps if s.kind is AgentStepKind.DEGRADED)
    assert degraded.detail == "8453"
    assert "rate limited" in degraded.reason
    assert sink.delivered == [("job-1", 1)]


def test_a_degraded_step_does_not_consume_the_scan_budget_silently() -> None:
    # The step is still journalled with its own seq, so the run's coverage is
    # readable after the fact rather than inferred from a gap.
    source = FlakySource({"1": [approval("a", "1")]}, {"8453": RuntimeError("boom")})
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="8453", reason="base"),
            ScanAction(chain_id="1", reason="ethereum"),
            FinishAction(reason="done"),
        ]
    )
    reporter = RecordingReporter()

    run_agent_scan(
        "job-1", "goal", "0xwallet", source, NeutralThreatIntel(), policy, RecordingSink(), reporter
    )

    seqs = [s.seq for s in reporter.steps]
    assert seqs == sorted(seqs), f"journal seqs must stay ordered: {seqs}"
    assert len(seqs) == len(set(seqs)), f"journal seqs must be unique: {seqs}"


def test_every_chain_failing_fails_the_run_rather_than_delivering_nothing() -> None:
    # An empty delivery renders as "no dangerous approvals" — a clean bill of
    # health for a wallet the agent never managed to look at.
    source = FlakySource({}, {"1": RuntimeError("down"), "8453": RuntimeError("down")})
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="1", reason="ethereum"),
            ScanAction(chain_id="8453", reason="base"),
            FinishAction(reason="done"),
        ]
    )
    sink = RecordingSink()

    with pytest.raises(RuntimeError, match="no chain could be scanned"):
        run_agent_scan(
            "job-1",
            "goal",
            "0xwallet",
            source,
            NeutralThreatIntel(),
            policy,
            sink,
            RecordingReporter(),
        )

    assert sink.delivered == []
    assert sink.failures, "the job is marked failed, not quietly empty"


def test_a_failing_chain_never_triggers_a_revocation() -> None:
    # Nothing was read from that chain, so nothing from it may be acted on.
    source = FlakySource({}, {"8453": RuntimeError("down")})
    policy = ScriptedPolicy(
        [
            ScanAction(chain_id="8453", reason="base"),
            ScanAction(chain_id="1", reason="ethereum"),
            FinishAction(reason="done"),
        ]
    )
    revoker = RecordingRevoker()

    run_agent_scan(
        "job-1",
        "goal",
        "0xwallet",
        source,
        NeutralThreatIntel(),
        policy,
        RecordingSink(),
        RecordingReporter(),
        revoker=revoker,
    )

    assert revoker.revoked == []


# ---------------------------------------------------------------- delegation (ADR-065)


def test_a_refused_revocation_is_journalled_as_not_attempted_not_revoked() -> None:
    """The guard's outcome has to reach the UI honestly.

    A wallet that is not delegated to KeeperHub cannot have its approvals
    revoked by anyone but its owner. The row must read "not attempted" with
    the reason, never "revoked" — a receipt for a revocation that did not
    happen is the most expensive lie this product could tell (ADR-059/065).
    """

    class RefusingRevoker:
        """Stands in for the KeeperHub adapter refusing a foreign wallet."""

        def revoke(self, finding: ApprovalFinding, wallet_address: str) -> ApprovalFinding:
            from dataclasses import replace

            return replace(finding, revocation_status=RevocationStatus.NOT_ATTEMPTED)

    source = MappedSource({"1": [approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])
    sink, reporter = RecordingSink(), RecordingReporter()

    results = run_agent_scan(
        "job-guard",
        "goal",
        "0xwallet",
        source,
        TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        policy,
        sink,
        reporter,
        revoker=RefusingRevoker(),
    )

    assert results is not None
    assert results[0].revocation_status is RevocationStatus.NOT_ATTEMPTED
    assert results[0].revocation_tx_hash is None
    revoke_step = next(s for s in reporter.steps if s.kind is AgentStepKind.REVOKE)
    assert "not attempted" in revoke_step.reason
    assert "not delegated" in revoke_step.reason


def test_the_scanned_wallet_is_what_reaches_the_revoker() -> None:
    # The guard is only as good as the wallet it is handed: if the use case
    # passed the wrong one, the adapter would happily execute for a wallet the
    # scan was never about.
    seen: list[str] = []

    class WalletRecordingRevoker:
        def revoke(self, finding: ApprovalFinding, wallet_address: str) -> ApprovalFinding:
            from dataclasses import replace

            seen.append(wallet_address)
            return replace(finding, revocation_status=RevocationStatus.REVOKED)

    source = MappedSource({"1": [approval("dangerous")]})
    policy = ScriptedPolicy([ScanAction(chain_id="1", reason="r"), FinishAction(reason="done")])

    run_agent_scan(
        "job-wallet",
        "goal",
        "0xTHE-SCANNED-WALLET",
        source,
        TieredThreatIntel({"dangerous": RiskTier.DANGEROUS}),
        policy,
        RecordingSink(),
        RecordingReporter(),
        revoker=WalletRecordingRevoker(),
    )

    assert seen == ["0xTHE-SCANNED-WALLET"]
