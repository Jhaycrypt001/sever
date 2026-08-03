"""Deterministic fake providers (ADR-021): `AGENT_PROVIDERS=fake`.

No network, no API key. Used by the e2e smoke test in CI (the paid-service ban
of ADR-012 applies to CI end to end) and for keyless local development. The
three fixed approvals exercise the whole risk cascade (ADR-058): a malicious
spender (dangerous, auto-revoked), an unverified spender (watch, asked
about), and a verified low-risk spender (safe, left alone).
"""

from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    ApprovalFinding,
    AskAction,
    FinishAction,
    RawApproval,
    RiskAssessment,
    RiskTier,
    ScanAction,
    classify_risk,
)
from aiagent.domain.usage import UsageMeter


class FakeApprovalSource:
    def __init__(self, meter: UsageMeter | None = None) -> None:
        self._meter = meter

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
        # ADR-038: fakes count their calls with zero cost — the keyless demo
        # shows honest call counts and a $0 total.
        if self._meter is not None:
            self._meter.record_search()
        raw = {"provider": "fake", "chain_id": chain_id, "wallet": wallet_address}
        return [
            RawApproval(
                chain_id=chain_id,
                token_address="0xdead00000000000000000000000000000dead0",
                token_symbol="fake-dangerous",
                spender_address="0xbad000000000000000000000000000000bad00",
                approved_amount="Unlimited",
                spender_name="Suspicious Proxy",
                raw={
                    **raw,
                    "malicious_address": 1,
                    "malicious_behavior": ["phishing_activities"],
                    "is_open_source": 1,
                },
            ),
            RawApproval(
                chain_id=chain_id,
                token_address="0xf00d00000000000000000000000000000f00d0",
                token_symbol="fake-watch",
                spender_address="0xca1100000000000000000000000000000ca110",
                approved_amount="1000",
                spender_name="Unverified Contract",
                raw={**raw, "malicious_address": 0, "is_open_source": 0},
            ),
            RawApproval(
                chain_id=chain_id,
                token_address="0xc0de00000000000000000000000000000c0de0",
                token_symbol="fake-safe",
                spender_address="0x5afe00000000000000000000000000000 5afe".replace(" ", ""),
                approved_amount="50",
                spender_name="Well-Known Router",
                raw={**raw, "malicious_address": 0, "is_open_source": 1},
            ),
        ]


#: Wallet address that makes `FakeAgentPolicy` ask for clarification once
#: (ADR-032). Valid hex, so it can be submitted through the console — which is
#: what makes the pause/answer/resume path testable in a browser at all.
ASK_SENTINEL = "0x00000000000000000000000000000000000a5c00"


class _FakeLlm:
    """Shared meter plumbing for the fake LLM-backed adapters (ADR-038)."""

    def __init__(self, meter: UsageMeter | None = None) -> None:
        self._meter = meter

    def _count(self) -> None:
        if self._meter is not None:
            self._meter.record_llm(0, 0)


class FakeThreatIntel(_FakeLlm):
    """Deterministic enrichment: tiers each fake approval from the same raw
    signals a live GoPlus-backed adapter would read, so the fake exercises
    the identical decision path as production (ADR-058)."""

    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        return [self._assess(a) for a in approvals]

    def _assess(self, approval: RawApproval) -> RiskAssessment:
        self._count()
        tier = classify_risk(approval)  # ADR-058: the tier is never the LLM's call
        if tier is RiskTier.DANGEROUS:
            explanation = (
                f"{approval.spender_address} is a known malicious contract "
                f"with an unlimited approval — high-confidence drain risk."
            )
        elif tier is RiskTier.WATCH:
            explanation = (
                f"{approval.spender_address} is unverified (no published "
                f"source) — not confirmed malicious, but unable to audit."
            )
        else:
            explanation = f"{approval.spender_address} is a verified, low-risk contract."
        return RiskAssessment(
            tier=tier,
            malicious_behavior=tuple(approval.raw.get("malicious_behavior", [])),
            explanation=explanation,
        )


class FakeAgentPolicy(_FakeLlm):
    """Deterministic policy (ADR-030/058) for keyless demos and e2e: scan the
    goal's chain, then a second chain (the fake source returns the same
    findings, so the journal shows the dedup at work), then stop."""

    def decide(
        self, goal: str, steps: list[AgentStep], approvals: list[RawApproval]
    ) -> AgentAction:
        self._count()
        # Deterministic HITL trigger (ADR-032): asks for clarification once,
        # then the task appends the user's answer to the goal on resume, which
        # disarms the trigger.
        #
        # Two triggers, because the obvious one is unreachable in practice. A
        # dispatched goal is always "scan wallet <address> for risky token
        # approvals", and no EVM address can contain the word "ambiguous" — it
        # is not hex. So the word-based trigger only ever fires from a unit
        # test, and the clarification path was never exercised through the real
        # product. ASK_SENTINEL is a valid address that a browser test can
        # actually submit.
        if (
            len(steps) == 0
            and "(user clarification:" not in goal
            and (ASK_SENTINEL in goal.lower() or "ambiguous" in goal)
        ):
            return AskAction(
                question="Which chains should I scan — just Ethereum, or every supported chain?",
                reason="The scan scope looks ambiguous; asking before spending calls",
            )
        if len(steps) == 0:
            return ScanAction(chain_id="1", reason="Start with Ethereum mainnet")
        if len(steps) == 1:
            return ScanAction(chain_id="8453", reason="Also check Base for outstanding approvals")
        return FinishAction(reason="All configured chains scanned")


class FakeApprovalRevoker:
    """Deterministic revocation (ADR-058): always succeeds with a fake tx
    hash, so the keyless demo/e2e can show the full auto-revoke path without
    KeeperHub or a real chain."""

    def __init__(self, meter: UsageMeter | None = None) -> None:
        self._meter = meter

    def revoke(self, finding: ApprovalFinding, wallet_address: str) -> ApprovalFinding:
        from dataclasses import replace

        from aiagent.domain.models import RevocationStatus

        # The fake acts as whatever wallet it is handed, so it never trips the
        # delegation guard of ADR-065 — that guard belongs to the adapter that
        # actually has an execution identity.
        del wallet_address

        # Counted as a generic external call for the spend-cap accounting
        # (ADR-048); real gas cost is not an LLM/API spend and is tracked
        # separately by the audit trail (tx hash, gas used), not this meter.
        if self._meter is not None:
            self._meter.record_search()
        fake_hash = f"0xfake{abs(hash(finding.approval_key)) % 10**12:012x}"
        return replace(
            finding, revocation_status=RevocationStatus.REVOKED, revocation_tx_hash=fake_hash
        )
