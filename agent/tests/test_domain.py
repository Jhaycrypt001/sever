from aiagent.domain.models import (
    ApprovalFinding,
    RawApproval,
    RiskTier,
    approvals_to_auto_revoke,
    classify_risk,
    dedupe_approvals,
    flag_new,
    raw_approval_key,
    sort_by_risk,
)


def approval(spender: str, raw: dict | None = None) -> RawApproval:
    return RawApproval(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address=spender,
        approved_amount="Unlimited",
        raw=raw or {},
    )


def finding(spender: str, tier: RiskTier) -> ApprovalFinding:
    return ApprovalFinding(
        chain_id="1",
        token_address="0xtoken",
        token_symbol="TKN",
        spender_address=spender,
        approved_amount="Unlimited",
        tier=tier,
    )


def test_classify_risk_malicious_spender_is_dangerous() -> None:
    assert classify_risk(approval("0xa", {"malicious_address": True})) == RiskTier.DANGEROUS


def test_classify_risk_unverified_spender_is_watch() -> None:
    assert classify_risk(approval("0xa", {"is_open_source": 0})) == RiskTier.WATCH


def test_classify_risk_verified_non_malicious_spender_is_safe() -> None:
    assert classify_risk(approval("0xa", {"is_open_source": 1})) == RiskTier.SAFE
    assert classify_risk(approval("0xa", {})) == RiskTier.SAFE  # defaults to open-source


def test_classify_risk_malicious_wins_over_unverified() -> None:
    raw = {"malicious_address": True, "is_open_source": 0}
    assert classify_risk(approval("0xa", raw)) == RiskTier.DANGEROUS


def test_sort_by_risk_dangerous_first_then_watch_then_safe() -> None:
    findings = [
        finding("safe", RiskTier.SAFE),
        finding("dangerous", RiskTier.DANGEROUS),
        finding("watch", RiskTier.WATCH),
    ]

    ordered = sort_by_risk(findings)

    assert [f.spender_address for f in ordered] == ["dangerous", "watch", "safe"]


def test_flag_new_marks_unseen_approval_keys() -> None:
    findings = [finding("a", RiskTier.SAFE), finding("b", RiskTier.SAFE)]
    seen = {raw_approval_key("1", "0xtoken", "a")}

    flagged = flag_new(findings, seen)

    assert {f.spender_address: f.is_new for f in flagged} == {"a": False, "b": True}


def test_dedupe_approvals_drops_repeats_of_the_same_chain_token_spender() -> None:
    approvals = [approval("a"), approval("a"), approval("b")]

    kept = dedupe_approvals(approvals)

    assert [a.spender_address for a in kept] == ["a", "b"]


def test_dedupe_approvals_is_case_insensitive() -> None:
    approvals = [approval("0xAbC"), approval("0xabc")]

    assert len(dedupe_approvals(approvals)) == 1


def test_approval_key_is_stable_and_case_insensitive() -> None:
    a = finding("0xAbC", RiskTier.SAFE)
    b = finding("0xabc", RiskTier.SAFE)

    assert a.approval_key == b.approval_key


def test_approvals_to_auto_revoke_is_dangerous_tier_only() -> None:
    findings = [
        finding("safe", RiskTier.SAFE),
        finding("watch", RiskTier.WATCH),
        finding("dangerous", RiskTier.DANGEROUS),
    ]

    to_revoke = approvals_to_auto_revoke(findings)

    assert [f.spender_address for f in to_revoke] == ["dangerous"]
