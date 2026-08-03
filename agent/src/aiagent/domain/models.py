"""Domain model: pure data + pure logic, no I/O (hexagonal core, ADR-004)."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RiskTier(StrEnum):
    """How dangerous an outstanding approval is judged to be (ADR-058).

    Deliberately a closed, deterministic vocabulary rather than a free-form
    LLM score: `classify_risk` (not an LLM) assigns the tier from the
    threat-intel signals, and the tier alone decides whether an approval is
    auto-revoked or left for the user (`approvals_to_auto_revoke`). See
    ADR-058 for why the model never authorizes a fund-moving transaction on
    its own judgment."""

    SAFE = "safe"
    WATCH = "watch"
    DANGEROUS = "dangerous"


class RevocationStatus(StrEnum):
    """What happened when a dangerous approval was sent for revocation
    (ADR-058).

    `REVOKED` means one thing only: a transaction was broadcast and confirmed
    onchain, and `revocation_tx_hash` proves it. A dry run gets its own
    `SIMULATED` status rather than borrowing `REVOKED`, because telling a user
    a draining approval is gone when no transaction exists is the single worst
    lie this product could tell.
    """

    NOT_ATTEMPTED = "not_attempted"
    PENDING = "pending"
    SIMULATED = "simulated"
    REVOKED = "revoked"
    FAILED = "failed"


@dataclass(frozen=True)
class RawApproval:
    """One outstanding ERC-20 approval as reported by the threat-intel source,
    before risk assessment (the domain's "a dated web result" equivalent)."""

    chain_id: str
    token_address: str
    token_symbol: str
    spender_address: str
    approved_amount: str  # "Unlimited" or a decimal string, exactly as reported
    approved_at: datetime | None = None
    approval_tx_hash: str | None = None
    spender_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def raw_approval_key(chain_id: str, token_address: str, spender_address: str) -> str:
    """Identity for dedup/memory (ADR-033/034 equivalent): one wallet can only
    hold one live approval per (chain, token, spender). Shared by every call
    site so the key format lives in exactly one place."""
    return f"{chain_id}:{token_address}:{spender_address}".lower()


#: Values a threat-intel provider may use for a true/false flag. Bare
#: truthiness is unsafe here: `bool("0")` is True in Python, so a provider
#: that switches an int 0/1 field to a string "0"/"1" would silently invert
#: every risk decision — and this tier is what fires real transactions.
_TRUE_FLAGS = frozenset({"1", "true", "yes"})
_FALSE_FLAGS = frozenset({"0", "false", "no", ""})


def flag_state(value: Any) -> bool | None:
    """Strict tri-state read of a provider flag: True, False, or None when the
    value is missing or in a shape this code does not recognize. Callers decide
    which way an unknown resolves — never silently, because the two directions
    have very different consequences (ADR-058)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):  # GoPlus returns 0/1 ints on this endpoint today
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_FLAGS:
            return True
        if normalized in _FALSE_FLAGS:
            return False
    return None


def classify_risk(approval: RawApproval) -> RiskTier:
    """The one place the risk tier is decided (ADR-058) — a deterministic
    rule over GoPlus's own verified signals, called identically by every
    `ThreatIntel` adapter (fake and live). An LLM never sets the tier: it may
    only explain one already classified here, so a hallucinated judgment can
    never upgrade or downgrade what gets auto-revoked.

    Unknown signals resolve in the direction that is safe for the *action*
    the tier authorizes, which is not the same direction for both flags:

    - `malicious_address` unknown -> **not** dangerous. DANGEROUS is the only
      tier that spends real gas on a real transaction, so it requires a
      positive, recognized malicious signal. Never revoke on a shrug.
    - `is_open_source` unknown -> **not** open source, i.e. WATCH. That tier
      means "unable to audit", which is exactly what a missing or unreadable
      verification flag tells us. WATCH only surfaces the finding to the user,
      so erring toward it costs nothing but noise.

    A spender on the provider's own **trust list** is never auto-revoked. When
    a provider both trusts and flags the same contract the signals contradict
    each other, and the safe response to a contradiction is to show a human
    rather than to broadcast a transaction — so it degrades to WATCH instead
    of DANGEROUS. Without this, one bad upstream flag on a router every wallet
    approves (Uniswap, Permit2...) would revoke a live position automatically.
    """
    raw = approval.raw
    trusted = flag_state(raw.get("trust_list")) is True
    if flag_state(raw.get("malicious_address")) is True:
        return RiskTier.WATCH if trusted else RiskTier.DANGEROUS
    if flag_state(raw.get("is_open_source")) is not True:
        return RiskTier.WATCH
    return RiskTier.SAFE


@dataclass(frozen=True)
class RiskAssessment:
    """What the threat-intel enrichment adds to a raw approval (ADR-058): the
    tier `classify_risk` decided, and an LLM-authored explanation for the
    human/journal."""

    tier: RiskTier = RiskTier.SAFE
    malicious_behavior: tuple[str, ...] = ()
    explanation: str | None = None


@dataclass(frozen=True)
class ApprovalFinding:
    """An approval with its resolved risk and (if acted on) its revocation
    outcome — the delivered unit, analogous to the example's `ResearchResult`."""

    chain_id: str
    token_address: str
    token_symbol: str
    spender_address: str
    approved_amount: str
    tier: RiskTier
    spender_name: str | None = None
    malicious_behavior: tuple[str, ...] = ()
    explanation: str | None = None
    is_new: bool = True
    revocation_status: RevocationStatus = RevocationStatus.NOT_ATTEMPTED
    revocation_tx_hash: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def approval_key(self) -> str:
        return raw_approval_key(self.chain_id, self.token_address, self.spender_address)


class AgentStepKind(StrEnum):
    """What the agent decided at one step of the loop (ADR-030/058)."""

    SCAN = "scan"
    FINISH = "finish"
    REVOKE = "revoke"
    REPORT = "report"
    #: A chain that could not be scanned (ADR-064). Recorded rather than
    #: raised, so one provider outage does not throw away everything the other
    #: chains found — but recorded *loudly*, because "no dangerous approvals"
    #: over a chain nobody actually looked at is the same class of lie as a
    #: simulated revocation presented as real (ADR-059).
    DEGRADED = "degraded"


@dataclass(frozen=True)
class ScanAction:
    """The policy wants to scan (another) chain for outstanding approvals —
    the domain's equivalent of the example's `SearchAction`."""

    chain_id: str
    reason: str


@dataclass(frozen=True)
class FinishAction:
    """The policy judges every configured chain scanned (or unreachable) and
    stops."""

    reason: str


@dataclass(frozen=True)
class AskAction:
    """The policy finds the scan scope ambiguous and asks the user one
    clarification question (ADR-032), e.g. which chains to cover. The job
    pauses until the answer re-dispatches it. This is the only clarification
    a scan job ever raises — the revocation decision itself (ADR-058) never
    blocks on a question; see `approvals_to_auto_revoke`."""

    question: str
    reason: str


AgentAction = ScanAction | FinishAction | AskAction


@dataclass(frozen=True)
class AgentStep:
    """One executed decision, recorded for the live journal (ADR-030)."""

    seq: int
    kind: AgentStepKind
    detail: str  # the chain_id for SCAN, the spender address for REVOKE, else empty
    reason: str  # the policy's own explanation, shown verbatim in the UI
    new_hits: int = 0  # findings added by this step after dedup


def approvals_to_auto_revoke(findings: list[ApprovalFinding]) -> tuple[ApprovalFinding, ...]:
    """The deterministic execution gate (ADR-058) — the only place that
    decides what gets acted on without asking first. Only DANGEROUS-tier
    approvals (a known-malicious spender, per `classify_risk`'s verified
    signals) are auto-revoked; WATCH and SAFE are left for the user to
    revoke by hand from the dashboard. This sidesteps stacking a second
    blocking question onto ADR-032's one-clarification-per-job scan-scope
    ask — a synchronous "confirm these N revokes?" prompt would need its own
    slot on the job, which the current callback contract does not have."""
    return tuple(f for f in findings if f.tier is RiskTier.DANGEROUS)


def _sort_key(finding: ApprovalFinding) -> int:
    # Dangerous first, then watch, then safe; unknown tiers (future-proofing)
    # sort last rather than raising.
    order = {RiskTier.DANGEROUS: 0, RiskTier.WATCH: 1, RiskTier.SAFE: 2}
    return order.get(finding.tier, 3)


def sort_by_risk(findings: list[ApprovalFinding]) -> list[ApprovalFinding]:
    """Most dangerous first — the ADR-011 sort-by-date equivalent."""
    return sorted(findings, key=_sort_key)


def flag_new(findings: list[ApprovalFinding], seen_keys: set[str]) -> list[ApprovalFinding]:
    """Marks findings already reported by previous runs of a recurring scan
    (ADR-033 equivalent). Comparison uses `approval_key` (chain+token+spender),
    so the same live approval never re-alerts on every tick."""
    return [replace(f, is_new=f.approval_key not in seen_keys) for f in findings]


def dedupe_approvals(approvals: list[RawApproval]) -> list[RawApproval]:
    """Drops approvals already seen this run (ADR-034 equivalent): the same
    (chain, token, spender) reported twice by overlapping scans counts once."""
    seen: set[str] = set()
    kept: list[RawApproval] = []
    for approval in approvals:
        key = raw_approval_key(approval.chain_id, approval.token_address, approval.spender_address)
        if key not in seen:
            seen.add(key)
            kept.append(approval)
    return kept


def as_utc(value: datetime) -> datetime:
    """Normalizes naive datetimes to UTC so sorting/comparison never mixes
    aware/naive."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
