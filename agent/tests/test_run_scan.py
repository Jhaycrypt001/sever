import pytest

from aiagent.application import run_scan
from aiagent.domain.models import ApprovalFinding, RawApproval, RiskAssessment, RiskTier


class FakeSource:
    def __init__(
        self, by_chain: dict[str, list[RawApproval]] | None = None, error: Exception | None = None
    ):
        self.by_chain = by_chain or {}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
        self.calls.append((wallet_address, chain_id))
        if self.error:
            raise self.error
        return self.by_chain.get(chain_id, [])


class FakeThreatIntel:
    """Assigns a fixed tier per spender address (set via `tiers`)."""

    def __init__(self, tiers: dict[str, RiskTier] | None = None):
        self.tiers = tiers or {}
        self.calls = 0

    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        self.calls += 1
        return [
            RiskAssessment(tier=self.tiers.get(a.spender_address, RiskTier.SAFE)) for a in approvals
        ]


class RecordingSink:
    def __init__(self, failure_error: Exception | None = None) -> None:
        self.started: list[str] = []
        self.delivered: list[tuple[str, list[ApprovalFinding]]] = []
        self.failures: list[tuple[str, str]] = []
        self.failure_error = failure_error

    def mark_started(self, job_id: str) -> None:
        self.started.append(job_id)

    def deliver(self, job_id: str, results: list[ApprovalFinding]) -> None:
        self.delivered.append((job_id, results))

    def report_failure(self, job_id: str, error: str) -> None:
        self.failures.append((job_id, error))
        if self.failure_error:
            raise self.failure_error


def approval(spender: str, chain_id: str = "1") -> RawApproval:
    return RawApproval(
        chain_id=chain_id,
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address=spender,
        approved_amount="Unlimited",
    )


def test_scans_every_configured_chain_and_assesses_once() -> None:
    source = FakeSource({"1": [approval("a")], "8453": [approval("b")]})
    threat_intel = FakeThreatIntel()
    sink = RecordingSink()

    results = run_scan("job-1", "0xwallet", ["1", "8453"], source, threat_intel, sink)

    assert source.calls == [("0xwallet", "1"), ("0xwallet", "8453")]
    assert {r.spender_address for r in results} == {"a", "b"}
    assert threat_intel.calls == 1  # ADR-042: one batched call for the whole set
    assert sink.started == ["job-1"] and sink.delivered[0][0] == "job-1"


def test_sorts_most_dangerous_first() -> None:
    source = FakeSource({"1": [approval("safe"), approval("dangerous"), approval("watch")]})
    threat_intel = FakeThreatIntel(
        {"dangerous": RiskTier.DANGEROUS, "watch": RiskTier.WATCH, "safe": RiskTier.SAFE}
    )
    sink = RecordingSink()

    results = run_scan("job-1", "0xwallet", ["1"], source, threat_intel, sink)

    assert [r.spender_address for r in results] == ["dangerous", "watch", "safe"]


def test_deduplicates_the_same_approval_seen_on_overlapping_scans() -> None:
    dup = approval("a")
    source = FakeSource({"1": [dup], "8453": [dup]})
    sink = RecordingSink()

    results = run_scan("job-1", "0xwallet", ["1", "8453"], source, FakeThreatIntel(), sink)

    assert len(results) == 1


def test_recurring_run_flags_seen_approvals() -> None:
    source = FakeSource({"1": [approval("a"), approval("b")]})
    sink = RecordingSink()

    results = run_scan(
        "job-r",
        "0xwallet",
        ["1"],
        source,
        FakeThreatIntel(),
        sink,
        seen_keys={"1:0xtoken:a"},
    )

    assert {r.spender_address: r.is_new for r in results} == {"a": False, "b": True}


def test_marks_the_job_started_before_scanning() -> None:
    sink = RecordingSink()

    run_scan("job-1", "0xwallet", ["1"], FakeSource(), FakeThreatIntel(), sink)

    assert sink.started == ["job-1"]


def test_reports_failure_and_reraises_when_the_source_breaks() -> None:
    source = FakeSource(error=RuntimeError("GoPlus quota exceeded"))
    sink = RecordingSink()

    with pytest.raises(RuntimeError):
        run_scan("job-1", "0xwallet", ["1"], source, FakeThreatIntel(), sink)

    assert sink.failures == [("job-1", "GoPlus quota exceeded")]
    assert sink.delivered == []


def test_original_error_survives_when_failure_report_also_breaks() -> None:
    """Backend unreachable: the root cause must reach Celery, not the sink error."""
    source = FakeSource(error=RuntimeError("GoPlus quota exceeded"))
    sink = RecordingSink(failure_error=ConnectionError("backend down"))

    with pytest.raises(RuntimeError, match="GoPlus quota exceeded"):
        run_scan("job-1", "0xwallet", ["1"], source, FakeThreatIntel(), sink)


# ---------------------------------------------------------------- chain resilience (ADR-064)


class PerChainSource:
    """Fails on the named chains, returns approvals for the rest."""

    def __init__(
        self, by_chain: dict[str, list[RawApproval]], failing: dict[str, Exception]
    ) -> None:
        self.by_chain = by_chain
        self.failing = failing
        self.calls: list[str] = []

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
        self.calls.append(chain_id)
        if chain_id in self.failing:
            raise self.failing[chain_id]
        return self.by_chain.get(chain_id, [])


class RecordingReporter:
    def __init__(self) -> None:
        self.steps: list[tuple[str, object]] = []

    def report_step(self, job_id: str, step: object) -> None:
        self.steps.append((job_id, step))


def test_one_unreachable_chain_does_not_discard_the_others() -> None:
    # ADR-064: a GoPlus outage on one chain used to abort the whole scan and
    # throw away everything already collected, so a Base blip meant no
    # Ethereum findings either.
    source = PerChainSource(
        {"1": [approval("a", "1")]},
        {"8453": RuntimeError("GoPlus error (code 4029): rate limited")},
    )
    sink = RecordingSink()
    reporter = RecordingReporter()

    results = run_scan(
        "job-1",
        "0xwallet",
        ["1", "8453"],
        source,
        FakeThreatIntel(),
        sink,
        reporter=reporter,
    )

    assert source.calls == ["1", "8453"], "the failure must not stop the loop"
    assert [f.spender_address for f in results] == ["a"]
    assert sink.delivered, "what was found is still delivered"
    assert not sink.failures


def test_an_unreachable_chain_is_recorded_in_the_journal() -> None:
    # Silence here would be the dangerous outcome: a partial scan that reads
    # as a clean sweep is the ADR-059 lie in a different costume.
    source = PerChainSource(
        {"1": [approval("a", "1")]},
        {"8453": RuntimeError("GoPlus error (code 4029): rate limited")},
    )
    reporter = RecordingReporter()

    run_scan(
        "job-1",
        "0xwallet",
        ["1", "8453"],
        source,
        FakeThreatIntel(),
        RecordingSink(),
        reporter=reporter,
    )

    # ADR-067: both halves of the coverage story are journalled — the chain
    # that worked and the chain that did not. Recording only failures leaves
    # "0 dangerous" meaning "clean" instead of "clean where we looked".
    kinds = {step.kind: step for _, step in reporter.steps}
    assert set(kinds) == {"scan", "degraded"}
    assert kinds["scan"].detail == "1"
    assert kinds["degraded"].detail == "8453"
    assert "rate limited" in kinds["degraded"].reason
    seqs = [step.seq for _, step in reporter.steps]
    assert len(seqs) == len(set(seqs)), f"journal seqs must be unique: {seqs}"


def test_every_chain_failing_fails_the_job_rather_than_reporting_clean() -> None:
    # An empty delivery renders as "no dangerous approvals". Saying that about
    # a wallet nobody could look at is worse than failing loudly.
    source = PerChainSource(
        {},
        {
            "1": RuntimeError("GoPlus down"),
            "8453": RuntimeError("GoPlus down"),
        },
    )
    sink = RecordingSink()

    with pytest.raises(RuntimeError, match="no chain could be scanned"):
        run_scan(
            "job-1",
            "0xwallet",
            ["1", "8453"],
            source,
            FakeThreatIntel(),
            sink,
            reporter=RecordingReporter(),
        )

    assert not sink.delivered
    assert sink.failures, "the sink is told, so the job shows as failed"


def test_a_broken_reporter_never_fails_the_scan() -> None:
    class BrokenReporter:
        def report_step(self, job_id: str, step: object) -> None:
            raise ConnectionError("backend down")

    source = PerChainSource({"1": [approval("a", "1")]}, {"8453": RuntimeError("boom")})

    results = run_scan(
        "job-1",
        "0xwallet",
        ["1", "8453"],
        source,
        FakeThreatIntel(),
        RecordingSink(),
        reporter=BrokenReporter(),
    )

    assert [f.spender_address for f in results] == ["a"]


def test_scanning_still_works_without_a_reporter() -> None:
    # Workflow mode ran without one before ADR-064; it must stay optional.
    source = PerChainSource({"1": [approval("a", "1")]}, {"8453": RuntimeError("boom")})

    results = run_scan(
        "job-1", "0xwallet", ["1", "8453"], source, FakeThreatIntel(), RecordingSink()
    )

    assert [f.spender_address for f in results] == ["a"]
