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
