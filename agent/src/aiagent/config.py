"""Environment-driven configuration (12-factor, ADR-014) + startup checks (ADR-020)."""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Placeholder values that must never survive into production.
_PLACEHOLDERS = frozenset({"change-me"})


def require_env(component: str, *names: str) -> None:
    """Fail-fast startup check (ADR-020): every listed variable must be set and
    non-empty, otherwise the process logs the exact gap and refuses to start —
    instead of failing later on the first task/request."""
    missing = sorted(name for name in names if not os.environ.get(name))
    if missing:
        logger.error(
            "%s cannot start: missing required environment variable(s): %s (see .env.example)",
            component,
            ", ".join(missing),
        )
        raise SystemExit(1)


def forbid_placeholders(component: str, *names: str) -> None:
    """Production-only guard (APP_ENV=production): a secret left at its
    development placeholder is treated as missing."""
    if os.environ.get("APP_ENV") != "production":
        return
    bad = sorted(name for name in names if os.environ.get(name, "") in _PLACEHOLDERS)
    if bad:
        logger.error(
            "%s cannot start in production: environment variable(s) still set "
            "to a development placeholder: %s",
            component,
            ", ".join(bad),
        )
        raise SystemExit(1)


def forbid_non_executing_modes(component: str) -> None:
    """Production-only guard (ADR-058): refuse to start in any mode that would
    report a revocation the worker never actually performed.

    Two settings make the agent *look* like it is protecting a wallet while no
    transaction is ever broadcast — `AGENT_PROVIDERS=fake` (a fabricated tx
    hash) and `KEEPERHUB_SIMULATE_ONLY=true` (a dry run). Both are legitimate
    for demos, CI and rehearsals, and both are catastrophic if they reach a
    real deployment: the user is told a draining approval is gone while it is
    still live. A security tool that lies in that direction is worse than no
    tool, so production refuses to boot rather than degrade silently."""
    if os.environ.get("APP_ENV") != "production":
        return
    unsafe = []
    if os.environ.get("AGENT_PROVIDERS", "live") == "fake":
        unsafe.append("AGENT_PROVIDERS=fake (revocations are fabricated, never broadcast)")
    if os.environ.get("KEEPERHUB_SIMULATE_ONLY", "").lower() in ("1", "true", "yes"):
        unsafe.append("KEEPERHUB_SIMULATE_ONLY=true (dry run, no transaction is broadcast)")
    if unsafe:
        logger.error(
            "%s cannot start in production: %s. These modes report protection "
            "that was never applied; unset them or leave APP_ENV unset.",
            component,
            "; ".join(unsafe),
        )
        raise SystemExit(1)


@dataclass(frozen=True)
class Settings:
    redis_url: str
    backend_internal_url: str
    internal_api_token: str
    agent_model_id: str
    # "live" (GoPlus + Claude + KeeperHub) or "fake" (deterministic in-process
    # adapters, no API key needed — e2e tests and keyless local development,
    # ADR-021).
    providers: str
    # Default chains scanned when a job does not name its own scope (ADR-058):
    # Ethereum, Base, Arbitrum, Polygon — matches KeeperHub's supported chains
    # (Sepolia excluded by default; add "11155111" for testnet demos).
    scan_chain_ids: list[str]
    # GoPlus Security API key (ADR-058): optional — the approval-list and
    # malicious-address endpoints work keyless at a lower rate limit; set for
    # sustained/production use. https://gopluslabs.io
    goplus_api_key: str
    # KeeperHub execution layer (ADR-058): the org's API base URL and `kh_`
    # key used for direct on-chain execution (execute_contract_call).
    keeperhub_api_url: str
    keeperhub_api_key: str
    # Safety valve (ADR-058): when true, revocations are submitted with
    # KeeperHub's `simulate=true` (dry-run, never broadcast) regardless of
    # tier — for demos/rehearsals without spending real gas.
    keeperhub_simulate_only: bool
    # LLM backend behind the live adapters (ADR-041): "anthropic" (hosted,
    # needs ANTHROPIC_API_KEY) or "ollama" (local server, no key —
    # AGENT_MODEL_ID then names a local model, e.g. "qwen3:14b").
    llm_backend: str
    # Ollama server URL; from a compose container use host.docker.internal.
    llm_base_url: str
    # Per-call hardening (ADR-044): a hung or flaky LLM call must not stall the
    # worker until Celery's coarse retry. Timeout bounds each call; max_retries
    # absorbs transient network blips before that (Anthropic only — Ollama has
    # no built-in retry and relies on the Celery task retry).
    llm_timeout_seconds: float
    llm_max_retries: int
    # LLM fallback chain (ADR-052): `backend:model_id` specs tried in order when
    # the primary model errors (provider down/quota) — retries handle blips, this
    # handles an outage. Empty by default. E.g. "anthropic:claude-haiku-4-5,ollama:qwen3:14b".
    model_fallbacks: list[str]
    # Step budget of the agentic loop (ADR-030) — each step is at most one
    # policy LLM call plus one provider search.
    agent_max_steps: int
    # Spend cap per job (ADR-048), USD: the agent degrades to a clean finish
    # once the run's indicative cost crosses this — the money analog of the
    # step budget, independent of the model's per-token price. 0 disables it.
    # Priced from the rates below; with the keyless fakes cost is $0, so it
    # never trips in the demo/e2e.
    agent_max_cost_usd: float
    # Orchestration of the agent mode (ADR-046): "langgraph" (default, a
    # StateGraph with durable Redis checkpointing and native interrupt-based
    # HITL) or "loop" (the framework-free hand-rolled loop of ADR-030). Both
    # drive the same domain ports; the workflow mode is unaffected.
    agent_orchestrator: str
    # Indicative pricing (ADR-038), USD; set the rates matching your model and
    # search plan. Defaults documented in .env.example.
    llm_cost_input_per_mtok: float
    llm_cost_output_per_mtok: float
    search_cost_per_call: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            backend_internal_url=os.environ.get("BACKEND_INTERNAL_URL", "http://localhost:8000"),
            internal_api_token=os.environ.get("INTERNAL_API_TOKEN", "change-me"),
            agent_model_id=os.environ.get("AGENT_MODEL_ID", "claude-opus-4-8"),
            providers=os.environ.get("AGENT_PROVIDERS", "live"),
            scan_chain_ids=[
                chain_id.strip()
                for chain_id in os.environ.get("AGENT_SCAN_CHAIN_IDS", "1,8453,42161,137").split(
                    ","
                )
                if chain_id.strip()
            ],
            goplus_api_key=os.environ.get("GOPLUS_API_KEY", ""),
            keeperhub_api_url=os.environ.get("KEEPERHUB_API_URL", "https://app.keeperhub.com"),
            keeperhub_api_key=os.environ.get("KEEPERHUB_API_KEY", ""),
            keeperhub_simulate_only=os.environ.get("KEEPERHUB_SIMULATE_ONLY", "").lower()
            in ("1", "true", "yes"),
            llm_backend=os.environ.get("AGENT_LLM_BACKEND", "anthropic"),
            llm_base_url=os.environ.get("AGENT_LLM_BASE_URL", "http://localhost:11434"),
            llm_timeout_seconds=float(os.environ.get("AGENT_LLM_TIMEOUT_SECONDS", "60")),
            llm_max_retries=int(os.environ.get("AGENT_LLM_MAX_RETRIES", "2")),
            model_fallbacks=[
                spec.strip()
                for spec in os.environ.get("AGENT_MODEL_FALLBACKS", "").split(",")
                if spec.strip()
            ],
            agent_max_steps=int(os.environ.get("AGENT_MAX_STEPS", "5")),
            agent_max_cost_usd=float(os.environ.get("AGENT_MAX_COST_USD", "2.0")),
            agent_orchestrator=os.environ.get("AGENT_ORCHESTRATOR", "langgraph"),
            llm_cost_input_per_mtok=float(os.environ.get("LLM_COST_INPUT_PER_MTOK", "5.0")),
            llm_cost_output_per_mtok=float(os.environ.get("LLM_COST_OUTPUT_PER_MTOK", "25.0")),
            search_cost_per_call=float(os.environ.get("SEARCH_COST_PER_CALL", "0.008")),
        )
