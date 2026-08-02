"""Model evaluation harness (ADR-045).

A directional way to answer "which model is good enough?" — the first question
anyone hits after wiring the local-LLM backend (ADR-041). It runs a small set
of golden cases against a real backend, scores each of the two LLM
capabilities (threat-intel explanation, scan policy), and prints a comparison
table across the models you name.

This is a **directional signal, not a benchmark**: the case set is tiny and
the scoring is coarse on purpose — enough to tell a model that follows the
task from one that does not, cheaply. Forks extend the case lists below.

The scoring and runner are pure and unit-tested with fakes (ADR-012). Only the
CLI (`main`) touches real, paid providers, so — like the live tests — it is
never run in CI; you invoke it by hand. Note the risk *tier* is never scored
here: it is decided by `classify_risk`, a deterministic function with no LLM
in the loop (ADR-058) — nothing to evaluate.
"""

import argparse
import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from aiagent.config import Settings
from aiagent.domain.models import (
    AgentAction,
    AgentStep,
    AgentStepKind,
    AskAction,
    RawApproval,
    ScanAction,
)
from aiagent.domain.ports import AgentPolicy, ThreatIntel
from aiagent.domain.usage import Pricing, UsageMeter

# ---------------------------------------------------------------- cases


@dataclass(frozen=True)
class ExplanationCase:
    name: str
    approval: RawApproval


@dataclass(frozen=True)
class PolicyCase:
    name: str
    goal: str
    steps: list[AgentStep]
    approvals: list[RawApproval]
    expected_kind: str  # "scan" | "ask" | "finish"


# ---------------------------------------------------------------- scoring


def score_explanation(explanation: str | None) -> tuple[float, str]:
    """One check: a non-empty, plausible-length explanation. The tier itself
    is never scored — `classify_risk` decides it deterministically, an LLM
    is never in that loop (ADR-058)."""
    ok = bool(explanation and explanation.strip() and len(explanation) < 400)
    return (1.0 if ok else 0.0), ("ok" if ok else "empty or implausibly long")


def action_kind(action: AgentAction) -> str:
    if isinstance(action, ScanAction):
        return "scan"
    if isinstance(action, AskAction):
        return "ask"
    return "finish"


def score_policy(case: PolicyCase, action: AgentAction) -> tuple[float, str]:
    """All-or-nothing on the decision kind — the loop's behavior hinges on it
    (a spurious finish ends the run, a spurious ask pauses it)."""
    got = action_kind(action)
    ok = got == case.expected_kind
    return (1.0 if ok else 0.0), f"want {case.expected_kind}, got {got}"


# ---------------------------------------------------------------- runner


@dataclass(frozen=True)
class CaseResult:
    capability: str
    name: str
    score: float
    latency_s: float
    detail: str
    error: str | None = None


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)

    def capability_score(self, capability: str) -> float | None:
        scores = [r.score for r in self.results if r.capability == capability]
        return sum(scores) / len(scores) if scores else None

    def overall(self) -> float:
        caps = [s for c in ("explanation", "policy") if (s := self.capability_score(c)) is not None]
        return sum(caps) / len(caps) if caps else 0.0

    def total_latency(self) -> float:
        return sum(r.latency_s for r in self.results)


def _timed[T](call: Callable[[], T]) -> tuple[T | None, float, str | None]:
    """Runs `call`, returning (result, latency, error). A raised exception
    becomes an error string — a broken call scores 0, never stops the sweep."""
    start = time.perf_counter()
    try:
        return call(), time.perf_counter() - start, None
    except Exception as exc:  # noqa: BLE001 - surfacing the failure is the whole point
        return None, time.perf_counter() - start, f"{type(exc).__name__}: {exc}"


def _run_explanation(threat_intel: ThreatIntel, case: ExplanationCase) -> CaseResult:
    out, latency, error = _timed(lambda: threat_intel.assess_many([case.approval])[0])
    if error is not None or out is None:
        return CaseResult("explanation", case.name, 0.0, latency, error or "no result", error)
    score, detail = score_explanation(out.explanation)
    return CaseResult("explanation", case.name, score, latency, detail)


def _run_policy(policy: AgentPolicy, case: PolicyCase) -> CaseResult:
    out, latency, error = _timed(lambda: policy.decide(case.goal, case.steps, case.approvals))
    if error is not None or out is None:
        return CaseResult("policy", case.name, 0.0, latency, error or "no result", error)
    score, detail = score_policy(case, out)
    return CaseResult("policy", case.name, score, latency, detail)


def evaluate(
    threat_intel: ThreatIntel,
    policy: AgentPolicy,
    *,
    explanation_cases: list[ExplanationCase] | None = None,
    policy_cases: list[PolicyCase] | None = None,
) -> Report:
    """Runs every golden case against the two adapters, timing each and
    turning any raised error into a zero-scored result — a model that crashes
    on a case is exactly what the harness is meant to surface."""
    report = Report()
    for ec in explanation_cases if explanation_cases is not None else EXPLANATION_CASES:
        report.results.append(_run_explanation(threat_intel, ec))
    for pc in policy_cases if policy_cases is not None else POLICY_CASES:
        report.results.append(_run_policy(policy, pc))
    return report


# ---------------------------------------------------------------- golden cases

EXPLANATION_CASES: list[ExplanationCase] = [
    ExplanationCase(
        name="known-malicious-spender",
        approval=RawApproval(
            chain_id="1",
            token_address="0x1111111111111111111111111111111111111a",
            token_symbol="USDC",
            spender_address="0xbad000000000000000000000000000000bad00",
            approved_amount="Unlimited",
            raw={"malicious_address": True, "malicious_behavior": ["phishing_activities"]},
        ),
    ),
    ExplanationCase(
        name="unverified-spender",
        approval=RawApproval(
            chain_id="8453",
            token_address="0x2222222222222222222222222222222222222b",
            token_symbol="WETH",
            spender_address="0xca1100000000000000000000000000000ca110",
            approved_amount="1000",
            raw={"malicious_address": False, "is_open_source": 0},
        ),
    ),
    ExplanationCase(
        name="verified-low-risk-spender",
        approval=RawApproval(
            chain_id="1",
            token_address="0x3333333333333333333333333333333333333c",
            token_symbol="DAI",
            spender_address="0x5afe00000000000000000000000000000005afe",
            approved_amount="50",
            raw={"malicious_address": False, "is_open_source": 1},
        ),
    ),
]

POLICY_CASES: list[PolicyCase] = [
    PolicyCase(
        name="fresh-scan-scans-first-chain",
        goal="scan wallet 0xabc for risky token approvals",
        steps=[],
        approvals=[],
        expected_kind="scan",  # nothing scanned yet -> scan
    ),
    PolicyCase(
        name="all-chains-scanned-finishes",
        goal="scan wallet 0xabc for risky token approvals (chains: 1,8453)",
        steps=[
            AgentStep(seq=1, kind=AgentStepKind.SCAN, detail="1", reason="start", new_hits=2),
            AgentStep(
                seq=2, kind=AgentStepKind.SCAN, detail="8453", reason="also base", new_hits=0
            ),
        ],
        approvals=[
            RawApproval(
                chain_id="1",
                token_address="0x1",
                token_symbol="USDC",
                spender_address="0xspender1",
                approved_amount="100",
            ),
        ],
        expected_kind="finish",  # every configured chain scanned
    ),
]


# ---------------------------------------------------------------- CLI


def _pricing_for(settings: Settings) -> Pricing:
    """Local models are free; the hosted backend uses the env cost rates."""
    if settings.llm_backend == "ollama":
        return Pricing(0.0, 0.0, 0.0)
    return Pricing(
        llm_input_per_mtok=settings.llm_cost_input_per_mtok,
        llm_output_per_mtok=settings.llm_cost_output_per_mtok,
        search_per_call=settings.search_cost_per_call,
    )


def _settings_for_spec(spec: str, base: Settings) -> Settings:
    """A model spec is `backend:model_id` — split on the first colon only, so
    Ollama tags like `gemma4:latest` survive."""
    backend, _, model_id = spec.partition(":")
    if not model_id:
        raise SystemExit(f"bad model spec {spec!r} — expected 'backend:model_id'")
    return dataclasses.replace(base, llm_backend=backend, agent_model_id=model_id)


def evaluate_spec(spec: str, base: Settings) -> tuple[Report, float]:
    """Builds the two real adapters for one model spec (sharing a meter) and
    runs the sweep. Returns the report and the run's indicative USD cost."""
    from aiagent.adapters.chat_model import make_chat_model
    from aiagent.adapters.llm import LlmAgentPolicy, LlmThreatIntel

    settings = _settings_for_spec(spec, base)
    meter = UsageMeter()
    model, system = settings.agent_model_id, settings.llm_backend
    threat_intel = LlmThreatIntel(
        make_chat_model(settings, max_tokens=256), meter=meter, model=model, system=system
    )
    policy = LlmAgentPolicy(
        make_chat_model(settings, max_tokens=256), meter=meter, model=model, system=system
    )
    report = evaluate(threat_intel, policy)
    cost = meter.snapshot().cost_usd(_pricing_for(settings))
    return report, cost


def _fmt_pct(value: float | None) -> str:
    return "  -  " if value is None else f"{value * 100:4.0f}%"


def format_table(rows: list[tuple[str, Report, float]]) -> str:
    """A plain aligned comparison table — no dependency, copy-pasteable."""
    header = f"{'MODEL':<28} {'explain':>7} {'policy':>7} {'overall':>8} {'lat/s':>7} {'cost$':>8}"
    lines = [header, "-" * len(header)]
    for label, report, cost in rows:
        lines.append(
            f"{label:<28} "
            f"{_fmt_pct(report.capability_score('explanation')):>7} "
            f"{_fmt_pct(report.capability_score('policy')):>7} "
            f"{_fmt_pct(report.overall()):>8} "
            f"{report.total_latency():>7.1f} "
            f"{cost:>8.4f}"
        )
    return "\n".join(lines)


def failures_below(rows: list[tuple[str, Report, float]], threshold: float) -> list[str]:
    """Model specs whose overall score is under `threshold` (0..1), each with a
    human-readable reason (the per-capability breakdown, so a single collapsed
    capability is visible). An empty list means every model cleared the bar —
    this is what the `--fail-under` pre-release gate keys its exit code on."""
    messages: list[str] = []
    for label, report, _cost in rows:
        overall = report.overall()
        if overall < threshold:
            caps = " ".join(
                f"{c}={_fmt_pct(report.capability_score(c)).strip()}"
                for c in ("explanation", "policy")
            )
            messages.append(
                f"{label}: overall {overall * 100:.0f}% < {threshold * 100:.0f}% ({caps})"
            )
    return messages


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m aiagent.evaluation",
        description="Score LLM models on the agent's two capabilities (ADR-045/058).",
    )
    parser.add_argument(
        "models",
        nargs="*",
        help="model specs 'backend:model_id' (e.g. ollama:gemma4:latest "
        "anthropic:claude-opus-4-8); defaults to the env-configured backend",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="print every case's score and detail"
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        metavar="PCT",
        help="exit non-zero if any model's overall score is below PCT (0..1) — the "
        "pre-release quality gate; run it by hand with a real backend before shipping "
        "a prompt or model change (deliberately not in CI: no API keys there, ADR-045)",
    )
    args = parser.parse_args(argv)
    if args.fail_under is not None and not 0.0 <= args.fail_under <= 1.0:
        raise SystemExit(f"--fail-under expects a fraction in 0..1, got {args.fail_under}")

    base = Settings.from_env()
    specs = args.models or [f"{base.llm_backend}:{base.agent_model_id}"]

    rows: list[tuple[str, Report, float]] = []
    for spec in specs:
        print(f"evaluating {spec} ...", flush=True)
        report, cost = evaluate_spec(spec, base)
        rows.append((spec, report, cost))
        if args.verbose:
            for r in report.results:
                print(f"  [{r.capability:<10}] {r.name:<28} {r.score * 100:3.0f}%  {r.detail}")

    print()
    print(format_table(rows))

    if args.fail_under is not None:
        failures = failures_below(rows, args.fail_under)
        if failures:
            print()
            for message in failures:
                print(f"FAIL {message}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
