"""Keyless production adapters (ADR-060): the real pipeline without an LLM.

These are **not** fakes. They read real GoPlus data, assign the tier through
the same `classify_risk` the LLM-backed adapters use, and hand real findings
to the real KeeperHub revoker. The only thing they do differently is write the
human-readable explanation from a template instead of asking a model for
prose. Nothing is fabricated, and no result is reported that did not happen —
which is exactly what separates this module from `adapters/fake.py`.

Why it exists: the safety-critical path (fetch approvals -> classify risk ->
revoke the dangerous ones) needs no language model at all. Tying it to a paid
API key would mean a wallet goes unprotected because someone's billing lapsed,
and would make an Anthropic outage a security incident. With these adapters the
product runs on a KeeperHub key alone, and the LLM becomes what it should be:
an upgrade to the writing, not a dependency of the protection.
"""

from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    AgentStepKind,
    FinishAction,
    RawApproval,
    RiskAssessment,
    RiskTier,
    ScanAction,
    classify_risk,
    flag_state,
)
from aiagent.domain.usage import UsageMeter


def _behaviours(approval: RawApproval) -> tuple[str, ...]:
    raw = approval.raw.get("malicious_behavior")
    return tuple(str(item) for item in raw) if isinstance(raw, list) else ()


def explain(approval: RawApproval, tier: RiskTier) -> str:
    """A factual sentence built from the signals that produced the tier. Says
    only what the data supports — no speculation a model might otherwise add."""
    spender = approval.spender_name or approval.spender_address
    unlimited = approval.approved_amount.strip().lower() == "unlimited"
    # Self-contained so the sentences below never repeat the token symbol.
    allowance = (
        f"an unlimited allowance on your {approval.token_symbol}"
        if unlimited
        else f"an allowance of {approval.approved_amount} {approval.token_symbol}"
    )
    behaviours = _behaviours(approval)

    if tier is RiskTier.DANGEROUS:
        flagged = f" Reported behaviour: {', '.join(behaviours)}." if behaviours else ""
        return (
            f"{spender} is flagged as malicious by threat intelligence and holds "
            f"{allowance}.{flagged} Revoking automatically."
        )
    if tier is RiskTier.WATCH:
        if flag_state(approval.raw.get("trust_list")) is True and behaviours:
            reason = (
                "it appears on the provider's trust list but also carries a malicious "
                f"flag ({', '.join(behaviours)}), and contradictory signals are not "
                "grounds to move funds automatically"
            )
        elif flag_state(approval.raw.get("is_open_source")) is False:
            reason = "its source code is not published, so its behaviour cannot be audited"
        else:
            reason = "threat intelligence returned no verification for it"
        return (
            f"{spender} holds {allowance}, and {reason}. "
            f"Left in place for you to review and revoke manually."
        )
    return (
        f"{spender} is a verified contract with no malicious reports and holds "
        f"{allowance}. No action taken."
    )


class DeterministicThreatIntel:
    """Real `ThreatIntel` with no model behind it: the tier comes from
    `classify_risk` (as it always does — ADR-058) and the explanation from
    `explain`. Used automatically when no LLM backend is configured."""

    def __init__(self, meter: UsageMeter | None = None) -> None:
        self._meter = meter

    def assess_many(self, approvals: list[RawApproval]) -> list[RiskAssessment]:
        # No LLM call is made, so nothing is metered: the run's reported spend
        # stays honest at $0 for this stage (ADR-038).
        return [
            RiskAssessment(
                tier=(tier := classify_risk(approval)),
                malicious_behavior=_behaviours(approval),
                explanation=explain(approval, tier),
            )
            for approval in approvals
        ]


class DeterministicAgentPolicy:
    """Real `AgentPolicy` with no model behind it: walks the configured chains
    in order, then finishes. Agent mode without an LLM cannot reason about
    *which* chain to prioritize, so it does the honest thing and covers all of
    them — the journal says so verbatim rather than implying a decision that
    was never made. Never asks a clarification question: there is no model to
    interpret the answer."""

    def __init__(self, chain_ids: list[str], meter: UsageMeter | None = None) -> None:
        self._chain_ids = list(chain_ids)
        self._meter = meter

    def decide(
        self, goal: str, steps: list[AgentStep], approvals: list[RawApproval]
    ) -> AgentAction:
        scanned = [step.detail for step in steps if step.kind is AgentStepKind.SCAN]
        for chain_id in self._chain_ids:
            if chain_id not in scanned:
                return ScanAction(
                    chain_id=chain_id,
                    reason=(
                        f"Scanning chain {chain_id} (no LLM configured: covering every "
                        f"configured chain in order)"
                    ),
                )
        return FinishAction(reason=f"All {len(self._chain_ids)} configured chain(s) scanned")
