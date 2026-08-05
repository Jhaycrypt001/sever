# Architecture Decisions — AI Agent Boilerplate

> Reference document for the project's technical decisions.
> Each decision is dated and motivated; if a decision is revisited, add a new
> entry rather than rewriting history.
>
> **Maintenance rule — this file is the single source of truth.** Any change
> that affects the architecture (new adapter, new dependency, changed contract,
> changed infrastructure, revisited decision) MUST update this document in the
> same commit. When reality and this document disagree, fixing the discrepancy
> is part of the change. Implementation gaps are marked *(planned)* here and
> tracked in `ROADMAP.md`.

Last updated: 2026-08-02

**Language convention**: all documentation, code, comments, commit messages, and
identifiers in this project are written in **English only**.

---

## 1. Project goal

"Approval Firewall": an agent that finds and revokes dangerous ERC-20 token
approvals for real, onchain, through KeeperHub (ADR-058 — this fork's pivot
of an original web-research boilerplate; see that ADR for the full rationale
and what changed).

- A user creates an account and submits a **wallet address**.
- The agent scans that wallet's outstanding token approvals across chains
  (GoPlus Security) and classifies each spender **Safe / Watch / Dangerous**
  with a deterministic function — never an LLM judgment call.
- Every **Dangerous** approval is **auto-revoked**: a real transaction
  executed through KeeperHub's direct-execution API. Watch/Safe findings are
  only surfaced for the user to act on manually.
- Findings are **ranked most-dangerous-first**.

## 2. Overview

Three components in a monorepo:

```
aiagent_boilerplate/
├── backend/     # Rust / Axum — web API, accounts, job orchestration
├── agent/       # Python — FastAPI micro-API + Celery workers (LangChain)
├── web/         # Next.js 16 — public page (/) + operator console (/console)
├── docs/        # This document + future ADRs
└── docker-compose.yml   # PostgreSQL, Redis, services
```

### Nominal flow

```
Next.js console
  │  POST /api/searches {wallet_address}   (JWT)
  ▼
Axum (Rust) ── persists the job (PostgreSQL, status=pending)
  │  POST /tasks {job_id, wallet_address}  (internal HTTP)
  ▼
FastAPI (Python) ── enqueues via Celery
  │
  ▼
Redis (broker) ──▶ Celery worker ── LangChain agent
                        │   0. POST /internal/jobs/{id}/started (→ running)
                        │   1. fetch approvals + threat intel (GoPlus)
                        │   2. classify risk tier (deterministic, ADR-058)
                        │   3. auto-revoke DANGEROUS findings (KeeperHub)
                        │   4. sort most-dangerous-first
                        ▼
                   POST /internal/jobs/{id}/results  ──▶ Axum ── persists
                                                              ▲
Vue (polling GET /api/searches/{id}) ─────────────────────────┘
```

---

## 3. Decisions

### ADR-001 — Three-component monorepo

**Decision**: a single repository, three directories (`backend/`, `agent/`, `frontend/`).

**Why**: this is a boilerplate — its value is demonstrating end-to-end integration.
A monorepo simplifies cloning, consistent versioning of API contracts between
components, and a single docker-compose file.

### ADR-002 — Web backend: Rust / Axum, hexagonal architecture

**Decision**: Axum + Tokio. Strict hexagonal architecture:

```
backend/src/
├── domain/          # Entities (User, ScanJob, ApprovalFinding — ADR-058) + pure business logic
│   └── ports.rs     # Traits: UserRepository, JobRepository, JobDispatcher,
│                    #         PasswordHasher, TokenService
├── application/     # Use cases: RegisterUser, LoginUser, LaunchSearch,
│                    #            IngestResults, SearchQueries (read side)
└── adapters/
    ├── http/        # Axum handlers, extractors, DTOs (inbound adapter)
    ├── persistence/ # postgres (sqlx) + in_memory fallback (outbound adapter)
    ├── auth/        # argon2id hasher + JWT token service (outbound adapter)
    └── dispatch/    # HttpJobDispatcher → Python micro-API + noop fallback (outbound)
```

**Rules**:
- `domain` depends on no infrastructure crate (no axum, no sqlx, no reqwest).
- Ports are traits; adapters implement them.
- Use cases are unit-tested with fakes/mocks of the ports.

### ADR-003 — Frontend: Vue 3 + Vite (simple)

**Decision**: Vue 3 (Composition API, `<script setup>`), Vite, Pinia for state,
Vue Router. No heavy UI framework for now — plain CSS.

**Pages**: sign-up, login, keyword form, list of searches, search detail
(results sorted by date; sources without a date shown separately).

**Refresh**: live updates over SSE (ADR-026), with plain 2.5 s polling kept as
an automatic fallback when the stream fails.

### ADR-004 — AI agent: Python, LangChain + Celery + Redis, hexagonal too

**Decision**: Celery workers run the LangChain agent. The agent core is isolated
from Celery and from external providers:

```
agent/src/aiagent/
├── domain/
│   ├── models.py    # RawSearchHit, ResearchResult, date normalization, sorting
│   └── ports.py     # Protocols: SearchProvider, DateExtractor(LLM), ResultSink
├── application/
│   └── run_research.py  # pure orchestration (date cascade, sort, deliver)
├── adapters/
│   ├── tavily.py    # SearchProvider → Tavily
│   ├── llm.py       # DateExtractor → Claude via langchain-anthropic
│   ├── sink.py      # ResultSink → POST /internal/jobs/{id}/results (Rust API)
│   └── api/app.py   # FastAPI: POST /tasks (inbound adapter, see ADR-005)
├── celery_app.py    # Celery app (broker/result backend on Redis)
└── tasks.py         # Celery tasks: thin glue calling application/
```

**Rule**: `tasks.py` and the FastAPI app contain no business logic — they
instantiate adapters and call the use case.

### ADR-005 — Rust → Celery integration: Python micro-API (FastAPI)

**Decision**: the Rust backend does not write directly to Redis in the Celery
format. It calls a FastAPI micro-API (`POST /tasks`) which enqueues through the
official Celery client.

**Why**: the Celery message protocol is Python-centric and non-trivial to
reimplement faithfully in Rust (serialization, headers, acks). Going through the
official client eliminates that whole class of bugs. Accepted cost: a 4th service
to deploy.

**Rejected alternatives**:
- *Producing the Celery format from Rust (rusty-celery or a hand-built message)*:
  fewer services, but a fragile coupling to Celery's internal format.
- *Custom Redis queue + home-grown consumer*: loses Celery's native retries/acks.

**Security**: the FastAPI service is not publicly exposed (internal docker-compose
network) and requires a shared token (`INTERNAL_API_TOKEN`).

### ADR-006 — Returning results: HTTP callback to the Rust API

**Decision**: the worker never touches the database. It posts results to an
internal endpoint of the Rust API: `POST /internal/jobs/{job_id}/results` (same
internal token as ADR-005). If the agent fails: `POST /internal/jobs/{job_id}/failure`.

**Why**: a single application owns the database schema (the Rust backend).
The hexagonal boundary stays clean: on the agent side it is the `ResultSink`
port; on the Rust side it is the `IngestResults` use case.

**Rejected alternative**: direct database writes from the worker — two
applications coupled to the same schema, riskier migrations.

### ADR-007 — Database: PostgreSQL + sqlx

**Decision**: PostgreSQL 16, accessed via `sqlx` (compile-time-checked queries),
migrations with `sqlx migrate`.

**Initial schema**:
- `users(id, email, password_hash, created_at)`
- `research_jobs(id, user_id, keyword, status, error, created_at, completed_at)`
  — `status ∈ {pending, running, completed, failed}`
- `search_results(id, job_id, title, url, snippet, published_at NULLABLE, date_confidence, raw JSONB)`

**Why**: the production standard; JSONB is handy for keeping the agent's raw
response.

**Implementation notes** (2026-07-07):
- Queries are runtime-checked (`sqlx::query`), not macro-checked, so the project
  compiles without a database.
- Migrations run automatically at backend startup (`run_migrations`).
- The backend uses PostgreSQL whenever `DATABASE_URL` is set and falls back to
  the in-memory adapter otherwise (dev without infra; data lost on restart).
- `store_results` uses transactional replace semantics (delete + insert), so a
  worker re-delivery (Celery retry) never duplicates results.
- Integration tests (`backend/tests/postgres_repositories.rs`) are gated on
  `DATABASE_URL`: compose service locally, GitLab service in CI (ADR-012); they
  skip cleanly when unset.
- On the host, the compose PostgreSQL maps to port **5433** to avoid clashing
  with a locally installed PostgreSQL (container-internal port stays 5432).

### ADR-008 — Authentication: JWT

**Decision**: JWT access token (short-lived, ~15 min) + refresh token (rotated,
stored hashed in the database to allow revocation). Passwords hashed with **argon2id**.

- Rust side: `jsonwebtoken` + `argon2` crates; refresh tokens are opaque
  (~244 bits of entropy), stored **SHA-256-hashed** in `refresh_tokens`
  (migration 0002) so a leaked table cannot be replayed.
- **Rotation**: refresh tokens are single use — `/refresh` consumes the
  presented token and issues a new pair; a replayed token gets a 401. Expired
  tokens are purged by the background reaper (ADR-016) and garbage-collected on
  use. *Hardened by ADR-056*: rotation is a **family lineage** with **reuse
  detection** — replaying an already-consumed token revokes the whole family.
- Cookie: `HttpOnly; Secure; SameSite=Strict; Path=/api/auth`, TTL
  `REFRESH_TOKEN_DAYS` (default 30). Scoped to the auth endpoints only.
- Vue side: access token in memory (Pinia store) — never in localStorage; the
  refresh cookie enables **silent session restore** on page reload (router
  guard) and a refresh-and-retry on 401 (`withAuth` in the auth store).
- Endpoints: `POST /api/auth/register`, `/login`, `/refresh`, `/logout`
  (all implemented, 2026-07-07), plus `/verify`, `/verify/resend` (ADR-062)
  and `/password/forgot`, `/password/reset` (ADR-063). Neither registration
  nor login issues a session — answering the emailed code does.

Full sequence diagram (sign-up → login → silent refresh → sign-out):
[`docs/diagrams/auth-refresh-flow.puml`](diagrams/auth-refresh-flow.puml)
— render with `plantuml -tpng docs/diagrams/auth-refresh-flow.puml`.

**Rejected alternative**: server-side cookie sessions — simpler, but the JWT
choice prepares for multiple clients / public APIs.

### ADR-009 — Web search: Tavily

**Decision**: Tavily as the default `SearchProvider`.

**Why**: designed for LLM agents, official LangChain integration
(`langchain-tavily`), often returns `published_date` directly, free tier
sufficient for a boilerplate. The `SearchProvider` port allows plugging in
Brave, SerpAPI, or DuckDuckGo without touching the domain.

**Config**: `TAVILY_API_KEY` (environment variable).

**Error handling (added 2026-07-29)**: a Tavily error response (`{"error": …}`
— e.g. an exhausted key quota) **raises**, so the job fails fast with the
provider's message. It is deliberately *not* swallowed as "zero results": doing
so let the agent keep searching against a dead provider, exhausting its step
budget and LLM spend on a run that silently returned nothing. Found via the
cost/decision visibility of ADR-038/029.

### ADR-010 — LLM: Claude (Anthropic) via langchain-anthropic

**Decision**: default model **`claude-opus-4-8`** (Claude Opus 4.8), configurable
via `AGENT_MODEL_ID`. Accessed through `langchain-anthropic` (`ChatAnthropic`).

**Role of the LLM in the agent**: reasoning for the research agent and, above
all, extraction/normalization of publication dates from page content
(see ADR-011). Use adaptive *thinking* (the model's default behavior; do not
configure `budget_tokens`, a parameter removed on this model generation).
Do not pass `temperature`/`top_p` (rejected with a 400 on Opus 4.8).

**Cost reference** (per million tokens, July 2026):

| Model | ID | Input | Output |
|---|---|---|---|
| Claude Opus 4.8 (default) | `claude-opus-4-8` | $5 | $25 |
| Claude Sonnet 5 | `claude-sonnet-5` | $3 ($2 intro) | $15 ($10 intro) |
| Claude Haiku 4.5 | `claude-haiku-4-5` | $1 | $5 |

The `LLM`/`DateExtractor` port is abstract: switching models (or providers) is
configuration, not a rewrite.

**Config**: `ANTHROPIC_API_KEY`, `AGENT_MODEL_ID`.

*Amended by ADR-041 (2026-07-22)*: the same adapters can now run on a local
model instead — `AGENT_LLM_BACKEND=ollama` + `AGENT_LLM_BASE_URL` (then
`AGENT_MODEL_ID` names the local model and `ANTHROPIC_API_KEY` is no longer
required); usage guide in `docs/COMMANDS.md` §“Local LLM”.

### ADR-011 — Publication date extraction: cascade strategy

Publication dates are often missing or wrong. Strategy, in order:

1. **Tavily metadata** (`published_date`) when present → confidence `high`.
2. **Page metadata**: JSON-LD (`datePublished`),
   `<meta property="article:published_time">` tags, OpenGraph → confidence `high`.
3. **LLM extraction** from page content (dates in the text, "published on…"
   mentions) → confidence `medium`, date normalized to ISO 8601.
4. **Failure** → `published_at = NULL`; the source is kept and displayed in an
   "unknown date" section (at the end of the list), never dropped.

Final sort: dates descending, `NULL` last. `date_confidence ∈ {high, medium, unknown}`
is stored and exposed to the frontend.

### ADR-012 — TDD: per-component strategy

General rule: test first; the hexagonal structure makes the domain testable
without I/O.

| Component | Tools | Pyramid |
|---|---|---|
| `backend/` | `cargo test`, hand-written fakes for the ports, PostgreSQL integration tests gated on `DATABASE_URL` (compose service locally, GitLab service in CI), HTTP tests via `tower::ServiceExt` | domain/use-case units ≫ adapter integration ≫ API e2e |
| `agent/` | `pytest`, fakes for the ports (`SearchProvider`, `DateExtractor`, `ResultSink`), `respx`/`responses` for outbound HTTP, Celery in eager mode for task tests | domain units ≫ adapter integration (LLM mocked, never a real call in CI) |
| `frontend/` | `vitest` + `@vue/test-utils` / Testing Library, `msw` to mock the API | components + stores |

**CI**: each component has an independently runnable suite; no test calls a paid
service (Tavily, Anthropic) — those adapters are covered by optional integration
tests behind a flag (`RUN_LIVE_TESTS=1`).

**Implementation note (2026-07-17)**: the live tests exist —
`agent/tests/test_live_providers.py`, one per paid adapter (Tavily search,
Claude enricher/policy/critic). They detect **provider drift** (renamed fields,
a model that stops following the JSON instructions) that the defensive parsing
elsewhere would degrade silently. Skipped without the flag; run them after a
model bump or when extraction quality drops.

### ADR-013 — Development environment: docker-compose

**Decision**: a single `docker-compose.yml` at the repository root covering **all**
bricks (see ADR-014). Two modes via Compose *profiles*:

- `docker compose up` (profile `infra`, default): PostgreSQL + Redis only —
  the Rust backend, the agent, and the frontend run locally (`cargo run`,
  `uvicorn`/`celery`, `npm run dev`) for hot-reload comfort.
- `docker compose --profile full up`: the fully containerized stack, identical
  to what CI builds — useful for validating end-to-end integration.

Configuration comes from `.env` (see `.env.example`). The Rust backend loads
the root `.env` automatically in development (dotenvy); containers get their
configuration from compose/CI environment variables only.

### ADR-014 — Dockerizing every technical brick

**Decision**: every technical brick has its own Docker image, built as a
**multi-stage** build for minimal final images. One source of truth per brick:
the Dockerfile serves dev (profile `full`), CI, and deployment.

| Brick | Dockerfile | Build stage | Final image |
|---|---|---|---|
| Rust backend | `backend/Dockerfile` | `rust:slim` + **cargo-chef** (dependency cache in a separate layer) | `debian:bookworm-slim` (binary only, non-root user) |
| Agent — FastAPI API | `agent/Dockerfile` | `python:3.12-slim` + **uv** (deps installed from `uv.lock`) | same slim base, non-root user |
| Agent — Celery worker | `agent/Dockerfile` (same image) | — | same image, different `command` (`celery -A ... worker`) |
| Vue frontend | `frontend/Dockerfile` | `node:22-alpine` (`npm ci && npm run build`) | `nginx:alpine` serving static files + reverse-proxying `/api` → backend |
| PostgreSQL / Redis | official images (`postgres:16-alpine`, `redis/redis-stack-server` — core Redis for the Celery broker **+** RediSearch for the LangGraph checkpointer, ADR-046; plain `redis:7-alpine` suffices only with `AGENT_ORCHESTRATOR=loop`) | — | — |

**Rules**:
- A single image for the FastAPI API and the Celery worker (same code, same
  deps) — only the `command` differs. Avoids version drift.
- `HEALTHCHECK` on every application service (`/healthz` for Axum and FastAPI,
  `celery inspect ping` for the worker); `depends_on: condition: service_healthy`
  in compose.
- Configuration comes **exclusively from environment variables** (see
  `.env.example`) — no hardcoded values in the images, same images from dev
  to prod.
- Images tagged `$CI_COMMIT_SHORT_SHA` + `latest` on the default branch
  (see ADR-015).
- One `.dockerignore` per brick (excludes `target/`, `node_modules/`, `.venv/`, etc.).
- The nginx `/api` proxy re-resolves the backend hostname per request
  (`resolver 127.0.0.11` + variable `proxy_pass`, added 2026-07-22): a literal
  `proxy_pass` hostname is resolved once at nginx startup, so recreating the
  backend container (new IP) 502'd every API call until the frontend
  container was restarted too.

### ADR-015 — CI/CD: GitLab CI, GitLab registry

**Decision**: GitLab CI pipeline (`.gitlab-ci.yml` at the root), designed for the
monorepo: each brick has its own jobs, triggered only when its files change
(`rules: changes:`), linked as a DAG with `needs:` so nothing waits on global stages.

**Stages**:

```
lint → test → build → publish → deploy
```

| Stage | backend/ (Rust) | agent/ (Python) | frontend/ (Vue) |
|---|---|---|---|
| `lint` | `cargo fmt --check`, `cargo clippy -- -D warnings` | `ruff check`, `ruff format --check`, `mypy` | `eslint`, `vue-tsc --noEmit` |
| `test` | `cargo test` — GitLab services `postgres:16` + `redis:7` (`DATABASE_URL`/`REDIS_URL` pointing at the services) | `pytest` (port fakes, Celery in eager mode — no external service required) | `vitest run` |
| `build` | `docker build` of the image via **kaniko** (no privileged Docker-in-Docker) | same | same |
| `publish` | push to the **GitLab Container Registry**: `$CI_REGISTRY_IMAGE/backend:$CI_COMMIT_SHORT_SHA` (+ `latest` on `main`) | same (`/agent`) | same (`/frontend`) |
| `deploy` | **manual** trigger (`when: manual`) on `main`, GitLab environment `production` → **VPS** (see below). |

**Deployment: VPS + docker compose (decided 2026-07-07)**:

- **Target**: a single VPS with Docker + the compose plugin installed. Enough to
  start with; full containerization (ADR-014) makes a later migration to
  Kubernetes or a PaaS painless — same images, only the orchestrator changes.
- **Mechanism**: the `deploy` job connects to the VPS over SSH (protected CI
  variables: `DEPLOY_HOST`, `DEPLOY_USER`, `SSH_PRIVATE_KEY`) and runs
  `docker compose pull && docker compose up -d`.
- **On the VPS**: a `/opt/aiagent/` directory holds the `docker-compose.yml`,
  the repo's `deploy/` directory (prod override + Caddyfile, copied as-is so
  compose paths match) and the production `.env` (secrets entered once by
  hand, never in the repository or in CI). The VPS authenticates against the
  GitLab registry with a read-only **deploy token**.
- **Prod override** (`deploy/docker-compose.prod.yml` — production-only files
  are grouped under `deploy/`, they play no role in local development): adds a
  **Caddy** reverse proxy
  in front (automatic TLS via Let's Encrypt, the only service exposing 80/443)
  in front of the frontend's nginx and the Rust API; pins image tags to
  `$CI_COMMIT_SHORT_SHA` (reproducible deployments, rollback = redeploy the
  previous tag); PostgreSQL and Redis publish no port on the host; named volume
  for PostgreSQL data.
- **Backups**: daily `pg_dump` via cron on the VPS (outside CI scope, documented
  in the upcoming installation runbook).

**Rules and choices**:
- **Per-brick CI cache**: `~/.cargo` + `target/` (keyed on `Cargo.lock`),
  uv cache (keyed on `uv.lock`), `node_modules/` (keyed on `package-lock.json`).
- **Rust integration tests**: rather than testcontainers (which would require
  Docker-in-Docker), CI provides PostgreSQL/Redis as GitLab *services*; the
  tests read `DATABASE_URL`/`REDIS_URL` and therefore behave identically
  locally (testcontainers or compose) and in CI.
- **Image builds with kaniko**: no Docker daemon or privileged runner required;
  layer cache pushed to the registry (`--cache=true`).
- No secret in the repository: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, etc. are
  **GitLab CI/CD variables** (masked/protected) and are only used by the
  optional `RUN_LIVE_TESTS` jobs (manual, never in the default pipeline,
  see ADR-012).
- The `publish` job runs only on `main` and tags; merge requests stop after
  `build` (the image is built for validation but not pushed).

### ADR-016 — Job lifecycle robustness (decided 2026-07-07)

**Problem**: without safeguards, a job whose Celery message is lost, whose worker
dies, or whose callback never arrives stays `pending` forever and the frontend
polls indefinitely. Transient failures (network, provider hiccup) failed jobs
that a simple retry would have completed.

**Decision** — three complementary mechanisms:

1. **`running` transition**: the worker notifies
   `POST /internal/jobs/{id}/started` before searching. `start()` only
   transitions `pending → running`; late or duplicate notifications are no-ops.
2. **Backend reaper** (`FailStaleJobs`): a background task (every 60 s) fails
   any job stuck in `pending`/`running` longer than `JOB_TIMEOUT_MINUTES`
   (default 15) with an explicit timeout error.
3. **Celery retries**: `acks_late` + `task_reject_on_worker_lost` (a task is
   re-queued if the worker dies mid-run), and the research task auto-retries
   transient exceptions up to 3 times with exponential backoff + jitter.

**Idempotence makes retries safe** — the invariants that allow re-running the
whole flow at any point:

- `start()` is a no-op unless the job is `pending`;
- result delivery uses replace semantics (ADR-007), never duplicating;
- a **late completion overwrites a timeout failure** (results are valuable —
  the reaper's verdict is not final);
- a **failure never clobbers a completed job** (late duplicate callbacks);
- `report_failure` from the worker is best-effort: if the backend is
  unreachable, the original error still reaches Celery (which retries), and the
  reaper eventually settles the job status.

**Rejected alternative**: heartbeats from the worker during long searches —
more precise staleness detection, but more moving parts than a boilerplate
needs; the timeout covers the realistic failure modes.

### ADR-017 — Abuse protection: rate limiting + per-user quota (decided 2026-07-07)

**Problem**: every search triggers paid Tavily and Anthropic calls, and the auth
endpoints are a brute-force target. Without limits, one user (or one script) can
burn the API budget or hammer login.

**Decision** — two complementary layers:

1. **Per-user daily quota** (business rule, lives in the `LaunchSearch` use
   case): at most `DAILY_SEARCH_QUOTA` searches per user per rolling 24 h
   (default 20), counted via the `count_created_since` port method. Exceeding
   it returns `429` with an explicit message.
2. **Per-IP rate limiting** (HTTP adapter middleware): fixed-window in-memory
   limiter keyed on the client IP — first `X-Forwarded-For` entry (set by
   Caddy/nginx in front, ADR-014/015), falling back to the socket peer address.
   Two knobs: `RATE_LIMIT_AUTH_PER_MINUTE` on `/api/auth/*` (default 10,
   brute-force protection) and `RATE_LIMIT_API_PER_MINUTE` on the rest of
   `/api/*` (default 120). Internal routes (worker traffic) are never limited.
   *Extended by ADR-057*: a third, **per-account** login throttle
   (`LOGIN_MAX_ATTEMPTS_PER_MINUTE`) closes IP-rotating credential-stuffing that
   per-IP limits miss, and a `security_events` audit log records the hits.

**Why in-memory fixed-window**: single-instance deployment (ADR-015) — no shared
store needed, ~80 lines, fully unit-testable. **Trade-offs accepted**: limits
reset on restart, and the IP limiter is per-instance — with N instances the
effective limit becomes N× the configured one (benign degradation; the
per-user quota stays exact at any scale since it counts PostgreSQL rows).
If horizontal scaling makes that matter, prefer rate limiting at the reverse
proxy/load balancer over a Redis-backed limiter in the backend — the latter
only pays off for fine-grained per-user rules (see ROADMAP.md P4).
`X-Forwarded-For` is only trustworthy because the reverse proxy is the sole
public entry point in production.

**Rejected alternatives**: `tower-governor` (an extra dependency and IP-extractor
coupling for behavior we can state in a few dozen lines); quota stored as a
counter table (the existing `research_jobs` table already answers the question
with one `COUNT`).

### ADR-018 — Observability: end-to-end correlation + structured logs (decided 2026-07-07)

**Problem**: a research request crosses four processes (Rust API → FastAPI →
Celery worker → callback to Rust). Without a shared identifier and parseable
logs, diagnosing a production incident means eyeballing four unrelated streams.

**Decision**:

1. **Correlation id**: every HTTP request to the Rust API runs inside a tracing
   span carrying a `request_id` — taken from the incoming `X-Request-Id` header
   (proxy/client) or generated, and echoed on the response. For the
   asynchronous flow, **the `job_id` is the cross-service correlation key**:
   the dispatcher sends it as `X-Request-Id` to the FastAPI micro-API, which
   passes it into the Celery task, and the worker's sink returns it on every
   internal callback. `grep <job_id>` across the four services tells the whole
   story of one search.
2. **Structured logs**: `LOG_FORMAT=json` (set in `deploy/docker-compose.prod.yml`)
   switches every process to one JSON object per line — `tracing-subscriber`'s
   JSON layer (events flattened, span fields included) on the Rust side, a
   stdlib `JsonFormatter` (no extra dependency) on the Python side, with
   `request_id`/`job_id` as first-class fields. Dev keeps human-readable output.

**Why not OpenTelemetry now**: this is 80% of the diagnostic value for a
fraction of the moving parts. The ids and the structure are exactly what an
OTel migration (noted in §5) would need anyway — nothing is thrown away.

### ADR-015 amendment — scheduled security audits (added 2026-07-07)

A sixth stage `audit` runs **only** on scheduled pipelines (plus manual web
triggers): `cargo audit`, `pip-audit` (via `uv export`), `npm audit
--audit-level=high`, and **gitleaks** over the full git history (`GIT_DEPTH: 0`).
Scheduled pipelines skip every other job (`.not-on-schedule` rule), so the
weekly run is audit-only and never blocks merge requests. The weekly schedule
itself is created in the GitLab UI (see SETUP.md §3).

**Advisory exceptions (added 2026-07-18)**: `cargo audit` scans `Cargo.lock`,
which is **feature-agnostic** — it lists optional dependencies the project
never compiles. Exceptions therefore live in `backend/.cargo/audit.toml`
(read by both CIs, which run the audit from `backend/`), and the policy is:
**every ignored advisory carries a written justification and a revisit
condition**, or it is fixed rather than silenced.

Current exception — **RUSTSEC-2023-0071** ("Marvin Attack", timing sidechannel
in `rsa` 0.9.x, no fixed release upstream): `rsa` is a dependency of
`sqlx-mysql` only, and the backend enables the `postgres` driver exclusively
(`sqlx` with `default-features = false`). Both `cargo tree -i rsa --target all`
and `cargo tree -i sqlx-mysql --target all` return nothing: the crate sits in
the lockfile but is never built into the binary, so no RSA code path exists at
runtime. Revisit when `rsa` ships a fix or when sqlx drops the transitive
dependency. Yanked-crate warnings (e.g. `spin` 0.9.8) stay warnings and do not
fail the audit.

### ADR-019 — Open source on GitHub: GitHub Actions becomes the primary CI/CD (decided 2026-07-08, revisits ADR-015)

**Context**: the project goes open source on GitHub. Contributor pull requests
must be validated automatically, and `.gitlab-ci.yml` does not run on GitHub.

**Decision**:

1. **GitHub Actions is the primary CI** (`.github/workflows/`):
   - `ci.yml` — on every PR and push to `main`/tags: lint + test for the three
     bricks (PostgreSQL/Redis as job services, same env vars as GitLab), then
     image builds. PRs build **without pushing** (Dockerfile validation, no
     secrets involved — safe for fork PRs since the test suite calls no paid
     service, ADR-012); pushes to `main`/tags publish to **GHCR**
     (`ghcr.io/christopheduc-me/ai-agent-boilerplate/{backend,agent,frontend}`,
     short-sha + `latest` tags — same scheme as ADR-015).
   - `security.yml` — weekly cron (`0 6 * * 1`) + on-demand: gitleaks (full
     history), cargo/pip/npm audits. No manual schedule to create, unlike GitLab.
2. **The boilerplate repository deploys nothing.** It is a source-code repo:
   forks deploy to their own infrastructure. The VPS deployment story (ADR-015)
   remains fully documented — compose production override, Caddy, provisioning
   checklist in SETUP.md §4, and a **reference deploy job in the GitLab CI
   mirror** — but no GitHub Actions job touches any server.
3. **`.gitlab-ci.yml` is kept as a documented mirror** of the pipeline for
   GitLab-hosted forks (including the reference `deploy:vps` job); it is not
   executed on GitHub. Pipeline changes must update both files.
4. **Branching model: GitHub Flow** — `main` is the only permanent branch
   (protected: PR required, 1 approval, green status checks, squash merge
   only); short-lived branches; contributor flow is fork → branch → PR.
   Contributor-facing rules live in `CONTRIBUTING.md` (English only, TDD, this
   document updated in the same PR); reviews are auto-assigned via `CODEOWNERS`.

**Trade-off accepted**: two pipeline definitions to keep in sync. Acceptable
because the stages are stable and the mirror is clearly marked; the alternative
(dropping GitLab support) would gratuitously narrow the boilerplate's audience.

### ADR-020 — Fail-fast startup validation of environment variables (decided 2026-07-08)

**Problem**: a missing key surfaced only at the first task — the worker started
"healthy" without `TAVILY_API_KEY` and every research job then crashed at
runtime with a deep pydantic stack trace. Configuration gaps must abort startup
with an explicit message, not degrade silently.

**Decision** — per-process required sets, checked at startup (missing **or
empty** — an empty value in `.env` counts as absent):

| Process | Always required | Additionally in `APP_ENV=production` |
|---|---|---|
| agent-worker (`worker_init` signal) | `ANTHROPIC_API_KEY`, `TAVILY_API_KEY` | `INTERNAL_API_TOKEN` not left at a placeholder |
| agent-api (FastAPI lifespan) | — (all vars have dev defaults) | `INTERNAL_API_TOKEN` not left at a placeholder |
| backend (start of `main`) | — (graceful dev fallbacks, ADR-013) | `JWT_SECRET`, `INTERNAL_API_TOKEN`, `DATABASE_URL`, `AGENT_API_URL` — set, non-empty, and not a placeholder (`change-me`, `insecure-dev-secret`) |
| frontend | — (static files, no runtime env) | — |

On failure the process logs one explicit line naming the component and every
missing variable (pointing at `.env.example`) and exits with code 1 — under
compose, the container stops immediately instead of looping on broken tasks.

`APP_ENV=production` is set by `deploy/docker-compose.prod.yml`; development keeps the
graceful fallbacks (in-memory persistence, noop dispatcher, dev secrets with a
warning) so the clone-and-run experience stays intact. The check only fires in
the worker process (Celery `worker_init` signal), so the agent-api container —
which needs no provider key — is unaffected.

### ADR-021 — Fake providers + end-to-end smoke test of the full stack (decided 2026-07-08)

**Problem**: CI validated each brick in isolation but nothing validated the
assembled containerized stack — a regression in a Dockerfile, the nginx proxy
config, a healthcheck, or inter-container networking would pass CI unseen.
And a true e2e run seemed to require paid API keys, which ADR-012 bans from CI.

**Decision**:

1. **Deterministic fake providers** (`agent/src/aiagent/adapters/fake.py`),
   selected with `AGENT_PROVIDERS=fake` (default `live`): no network, no key.
   The fake search returns four fixed hits that exercise the whole date
   cascade (ADR-011) — provider date (high), LLM-extracted date (medium),
   unknown — so one run also asserts sorting and confidence levels. The
   ADR-020 startup gate skips the API-key requirement in fake mode. Selection
   happens in `build_providers()` (tasks layer); the hexagonal core is
   untouched. Fake mode doubles as **keyless local development**.
2. **E2E smoke script** (`scripts/e2e-smoke.sh`): drives the real user journey
   **through the nginx proxy** (register → login → launch → poll until the
   worker completes → assert result order and confidences). Works against any
   base URL (local, CI, staging).
3. **CI job `e2e`**: boots `docker compose --profile full up --build --wait`
   with `AGENT_PROVIDERS=fake` and runs the script — in GitHub Actions
   (native Docker on the runner) and in the GitLab mirror (docker-in-dind,
   base URL `http://docker:8080`). Compose logs are dumped on failure.

**What this covers that unit/integration tests cannot**: image builds, compose
wiring (`depends_on`, healthchecks, `--wait`), nginx `/api` proxying, the
Rust→FastAPI→Celery→callback chain over the real container network, and the
ADR-016 lifecycle (`pending → running → completed`) with a real worker.

### ADR-022 — Platform-agnostic tooling: portable core, thin CI adapters (decided 2026-07-08)

**Problem**: as a boilerplate, the project must not lock its users into one
forge's proprietary tooling (e.g. Dependabot, GitHub-only). Platform-specific
glue is unavoidable (workflows, templates), but logic inside it is not.

**Decision** — the hexagonal principle applied to tooling:

1. **All CI logic lives in portable artifacts**: shell scripts under
   `scripts/` (`e2e-smoke.sh`, `deps-report.sh`), compose files, Dockerfiles,
   and configs of open, multi-platform tools. **Every CI step must be runnable
   locally** with a documented command (docs/COMMANDS.md).
2. **Platform files are thin adapters**: `.github/workflows/` and
   `.gitlab-ci.yml` only install toolchains and call the portable artifacts.
   No business logic in YAML.
3. **Dependency updates without a platform bot**:
   - `scripts/deps-report.sh` — informative outdated report using only the
     native package managers (`cargo update --dry-run`,
     `uv lock --upgrade --dry-run`, `npm outdated`), run weekly by both CIs
     alongside the security audits. Applying upgrades stays a human action
     (creating PRs is inherently a platform API).
   - `renovate.json` — optional automation for forks that want update PRs:
     **Renovate, not Dependabot**, because the same config file works on
     GitHub, GitLab, Bitbucket, Gitea, and self-hosted. The file is inert
     until a Renovate runner is connected.
4. **Neutral integration points**: the image registry is only referenced
   through `CI_REGISTRY_IMAGE`/`IMAGE_TAG` (GHCR, GitLab registry, or any
   other); `CODEOWNERS` sits at the repository root, read by both GitHub and
   GitLab.

**Accepted platform-specific remainders**: issue/PR templates and the workflow
files themselves — adapters by nature, duplicated per forge where useful.

### ADR-023 — Code coverage measurement and reporting (decided 2026-07-09)

**Decision**: the three test jobs measure coverage with each ecosystem's
standard tool — `cargo llvm-cov` (backend), `pytest --cov` (agent),
`vitest --coverage` via v8 (frontend) — and reporting differs per platform:

- **GitHub Actions → Codecov** (per-brick flags, README badge, diff-coverage
  comment on PRs). Codecov passes the ADR-022 filter: it works on GitHub,
  GitLab, Bitbucket, and self-hosted — unlike GitHub-centric alternatives.
  Uploads are tokenless for public repos and **never fail the pipeline**
  (`fail_ci_if_error: false` + `informational: true` in `codecov.yml`):
  coverage is a signal for reviewers, not a gate.
- **GitLab mirror → native `coverage:` regex** (job coverage shown in MRs and
  available as a GitLab badge) — zero external service.

**Thin binaries are excluded, their logic is extracted and tested** (added
2026-07-09): `src/main.rs` and `src/bin/healthcheck.rs` cannot be meaningfully
unit-tested (they bind sockets and block), so their logic moved into library
modules that are — `config::AppConfig` (env parsing, defaults, dev-fallback
warnings, ADR-020 production validation) and `healthcheck::check` (probe
tested against a stub TCP server). The residual shells are excluded from
coverage (`--ignore-filename-regex` + `codecov.yml ignore`); everything they
delegate to is measured. Same philosophy on the agent: `tasks.py` is covered
end to end by calling the Celery task directly with fake providers (ADR-021)
and respx-mocked backend callbacks.

Baseline: backend ≈93 % lines, agent ≈95 %, frontend low by design (only the
domain-critical `ResultList` component is unit-tested — the e2e smoke covers
the views end to end instead, ADR-021).

### ADR-024 — Graceful shutdown of the backend (decided 2026-07-10)

**Problem**: `docker compose stop`, a VPS redeploy, or an orchestrator killing
the container sends SIGTERM; without handling it, in-flight requests are
dropped — including a worker callback in the middle of writing results.

**Decision**: `axum::serve(...).with_graceful_shutdown(...)` on SIGTERM/SIGINT:
the listener stops accepting connections, in-flight requests drain, then the
process exits 0 (compose's default 10 s `stop_grace_period` is the hard cap).
The Celery worker already handles SIGTERM natively (warm shutdown), and the
ADR-016 idempotence covers the truly-interrupted cases — graceful shutdown
makes them rare instead of systematic.

### ADR-025 — Cross-language contract fixtures (decided 2026-07-10)

**Problem**: the internal JSON contracts (backend ↔ FastAPI ↔ worker
callbacks) are serialized by Python and deserialized by Rust (and vice versa),
but each side was tested only against itself — a drift (renamed field, changed
date format, new enum value) would surface in the e2e test at best, in
production at worst.

**Decision**: golden fixtures in `contracts/` (`task-request.json`,
`results-callback.json`, `failure-callback.json`), asserted on **both sides in
the direction of real traffic**: the producer must serialize exactly the
fixture (Python `serialize_result`; Rust `HttpJobDispatcher`, captured by a
stub server), the consumer must accept it (Rust: fixtures POSTed through the
real router; Python: pydantic parse). Tests: `backend/tests/contract.rs`,
`agent/tests/test_contract.py`. A drift now breaks a unit-speed test suite
naming the exact contract.

### ADR-015 amendment — trivy image scanning (added 2026-07-10)

The weekly security audits also scan the three published images for
HIGH/CRITICAL CVEs with **trivy** (`--ignore-unfixed`: only actionable
findings) — the cargo/pip/npm audits cover application dependencies, trivy
covers the **base images** (debian-slim, python-slim, nginx-alpine).

**npm audit resolution (added 2026-07-27)**: unlike `cargo audit`, `npm audit`
has no lockfile-external ignore file, so the policy here is **fix, never
silence** — the gate stays `npm audit --audit-level=high` with no allowlist.
Two high advisories (a `brace-expansion` ReDoS/OOM DoS reachable through the
lint/test toolchain, and a `postcss` build-time path-traversal) were cleared by
upgrading the frontend dev toolchain to current majors: **eslint 9 → 10**
(with `eslint-plugin-vue` 9 → 10), **vitest + @vitest/coverage-v8 3 → 4**, and
**vue-tsc 2 → 3** (Volar 3). No production dependency and no shipped bundle code
was affected — every advisory sat in build/lint/test tooling — and `vite`
stayed on 6 (all three upgrades accept it). The one pin that survives is
`package.json` → `overrides: { "brace-expansion": "5.0.8" }`: the OOM advisory's
vulnerable range is the **continuous** `<=5.0.7`, so *every* published version
except `5.0.8` is affected (the per-major "latests" 1.1.16 / 2.1.2 are still
inside the range). brace-expansion is a single stable `expand()` function across
all majors, so forcing one version on every consumer (minimatch → glob →
js-beautify → `@vue/test-utils`) is safe; `npm ls brace-expansion` shows a
single 5.0.8. `package.json` cannot carry a comment — this note is that pin's
written justification, and its revisit condition is: drop the override once
`@vue/test-utils` (via `js-beautify`) ships a tree that no longer resolves
brace-expansion `<5.0.8`.

### ADR-022 amendment — local pre-commit hooks (added 2026-07-10)

**lefthook** (single multi-platform Go binary) provides opt-in pre-commit
hooks (`lefthook install`): fast format/lint checks per brick plus a gitleaks
staged scan when installed. Deliberately fast — test suites and clippy stay in
CI. Bypass with `git commit --no-verify`.

### ADR-026 — Live job updates over SSE (decided 2026-07-10, revisits ADR-003)

**Decision**: `GET /api/searches/{id}/events` streams the job detail as SSE
`update` events (same JSON shape as the GET endpoint — one shared builder),
one event per change, closing after the terminal status. The frontend
subscribes on the detail view and **falls back to the previous polling**
automatically if the stream fails.

Two deliberate implementation choices:

1. **Per-connection database polling (1 s), not an in-process broadcast** — a
   `tokio::broadcast` bus would be faster but silently wrong with two backend
   instances; the job state lives in PostgreSQL, so polling it keeps the
   stream correct at any scale. Upgrading to Postgres LISTEN/NOTIFY or Redis
   pub/sub is a swap inside `adapters/http/sse.rs` (noted in §5).
2. **Client uses fetch + ReadableStream, not `EventSource`** — the browser's
   `EventSource` API cannot send an `Authorization` header, and the usual
   workaround (access token in the query string) leaks tokens into proxy and
   server logs. A small incremental SSE parser (`createSSEParser`, unit
   tested) keeps the client dependency-free.

### ADR-027 — Timeline: hit enrichment and chronological rendering (decided 2026-07-10)

**Context**: the product's promise is chronological ("what happened about this
keyword, in order"), but results were rendered as a flat list and carried no
information beyond title/snippet/date.

**Decision**:

1. **The `DateExtractor` port becomes `HitEnricher`**: one LLM call per hit
   returns `{published_date, event_type, summary}` as JSON. The ADR-011 date
   cascade is unchanged (provider date wins → high; LLM date → medium; else
   unknown), and the reply parsing is defensive — any malformed piece degrades
   to its neutral value (`other`, no summary) instead of failing the job.
   **Cost note**: every hit now triggers an LLM call (previously only undated
   hits did) — that is the price of badges/summaries; the ADR-017 quota is the
   budget guard, and fake providers keep tests/e2e free.
2. **`event_type`** is a coarse closed enum (`announcement`, `release`,
   `funding`, `legal`, `incident`, `research`, `opinion`, `other`), carried end
   to end (agent → contract → backend column, migration 0003 → frontend
   badge). Unknown values degrade to `other` on the backend (forward
   compatibility); serde defaults keep pre-ADR-027 payloads parseable.
3. **Frontend `ResultTimeline`** replaces the flat list: dated results grouped
   by month, `date_confidence` made visible (solid marker for
   provider-confirmed dates, hollow + "(estimated)" for LLM-extracted ones),
   event badge, LLM summary (falling back to the snippet), and the undated
   section kept apart (ADR-011).

### ADR-028 — Browser-level e2e tests with Playwright (decided 2026-07-11, extends ADR-021)

**Context**: the ADR-021 smoke script exercises the full stack over raw HTTP
but never runs the SPA itself — a broken build, router, store, or the SSE
client (ADR-026) would slip through.

**Decision**: **Playwright** specs in `frontend/e2e/` drive a real Chromium
through nginx against the same fake-provider stack as the smoke script
(`--profile full` + `AGENT_PROVIDERS=fake`; the stack must be up first — no
`webServer` block, the system under test is the containerized one). Two
journeys: register → search → live status → timeline assertions (month groups,
estimated marker, badges, undated section), and login-again → previous
searches (exercises the refresh-cookie flow, ADR-008). Wired into the existing
`e2e` job on both CIs — on GitLab through the official Playwright image run on
the dind daemon (`--network host`, sources `docker cp`'d in; keep the image
tag in sync with `@playwright/test`). Locally: `npm run test:e2e`
(`E2E_BASE_URL` overrides the default `http://localhost:8080`). Playwright
specs are excluded from vitest and from coverage.

### ADR-029 — Opt-in OpenTelemetry traces (decided 2026-07-11, extends ADR-018)

**Context**: the ADR-018 correlation ids make `grep <job_id>` work across the
four processes, but reconstructing latency and causality by hand from logs is
tedious; distributed tracing is the standard answer. A boilerplate, however,
must not force an observability stack on every fork.

**Decision**: OpenTelemetry traces, **strictly opt-in** behind the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` variable — unset or empty (the default), nothing
is installed and both bricks behave exactly as before.

1. **Backend (Rust)**: `telemetry::layer()` adds an OTLP/HTTP export layer to
   the existing `tracing` subscriber; the per-request `http_request` spans
   (ADR-018 middleware) become exported spans for free. The dispatcher injects
   the W3C `traceparent` header on `POST /tasks`, and buffered spans are
   flushed on graceful shutdown (ADR-024).
2. **Agent (Python)**: `configure_telemetry()` installs the OTLP tracer
   provider plus the FastAPI (extracts `traceparent`), Celery (carries the
   context producer → worker through the broker; instrumented per worker
   process) and httpx (propagates it again on the result callbacks)
   instrumentations. One search = one trace across all four hops.
3. **Local trace backend**: `docker compose --profile observability` adds a
   dev-only **Jaeger v2** (OTLP on 4318, UI on 16686); the app services pass
   `OTEL_EXPORTER_OTLP_ENDPOINT` through from the environment. Production
   forks point the variable at their own collector.
4. **Test hygiene (ADR-012)**: unit tests assert the gate (disabled without
   the variable, no header leakage) and the propagation path with an
   exporter-less local provider — no collector, no network.

Metrics and logs stayed out of the original scope: traces are where the
multi-process debugging pain is, and the rest could follow the same opt-in
pattern later — which they since did (log↔trace correlation below, and metrics
in ADR-050).

**Amendment — per-call LLM spans (added 2026-07-29)**: the propagation above
gives one trace per search, but the agent's LLM calls — the expensive,
slow, non-deterministic part — were invisible inside it. The three adapters
(`LlmHitEnricher`/`LlmAgentPolicy`/`LlmResultCritic`) now open **one span per
call** via a shared `llm_span` helper, tagged with the OpenTelemetry **GenAI
semantic conventions** (`gen_ai.operation.name`, `gen_ai.system`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens` / `output_tokens`) plus the
domain outcome that makes a run readable at a glance — `aiagent.agent.action`
(search/ask/finish), `aiagent.critic.dropped` / `has_gap`, the enrichment
`batch_size` (ADR-042 runs the per-hit calls together, so it is one span with
summed usage, not a false per-hit latency). Latency is the span duration; the
`model`/`system` labels are threaded from `Settings` at the wiring point. It
respects the opt-in gate for free: the module tracer is a **no-op proxy** until
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, so the spans cost nothing in the keyless
demo/CI and appear in Jaeger as children of the job trace only when enabled.
Instrumentation lives in the adapter; the domain ports are untouched. Tested
with an in-memory span exporter (`test_llm_spans.py`) — no provider, no paid
call. Together with the cost meter (ADR-038) and the spend cap (ADR-048), this
closes the cost/quality/observability loop for the agent.

**Amendment — log ↔ trace correlation (added 2026-07-29)**: the two pillars now
cross-reference. Each structured log line (ADR-018) also carries the active
`trace_id` / `span_id` when tracing is on — the agent stamps them with a stdlib
`logging.Filter` reading the current OTel span, the backend records the trace id
as a field on the `http_request` span (so `with_current_span` emits it). A no-op
when tracing is off (no active span → the fields are simply absent), so the
keyless output is unchanged. You can now jump from a log line to its Jaeger trace
and back; the `job_id` stays the cross-service key that works even with tracing
off. What to watch and where to find it is written up in
[OBSERVABILITY.md](OBSERVABILITY.md); metrics remain the deferred third pillar
(§5).

### ADR-030 — Two research modes: the workflow and the agentic loop (decided 2026-07-12)

> *Amended by ADR-046 (2026-07-25)*: the agent mode now has **two
> orchestrators** — the LangGraph `StateGraph` (default) and this hand-rolled
> loop (`AGENT_ORCHESTRATOR=loop`). Both drive the same ports; everything below
> about the loop still holds and is asserted for both.

**Context**: the original flow is a *workflow* — a fixed pipeline (one search,
enrich, sort) where the LLM is a component the code calls. A boilerplate named
"AI agent" should also demonstrate an actual **agent**: the LLM deciding the
control flow. Both are legitimate patterns (per the standard workflow-vs-agent
distinction) with different cost/determinism trade-offs, so the boilerplate
now ships both, side by side, on the same plumbing.

**Decision**:

1. **A job has a `mode`** — `workflow` (unchanged pipeline) or `agent` —
   carried end to end: `POST /api/searches` body → jobs table (migration
   0004) → task request contract → Celery task routing. Serde/pydantic
   defaults keep every pre-ADR-030 payload and client working.
2. **The agentic loop** (`run_agent_research`, Python): a new **`AgentPolicy`
   port** (LLM in production, scripted fake under `AGENT_PROVIDERS=fake`)
   sees the goal, the transcript of its own decisions and the collected
   titles, and returns the next action — `search(query, reason)` or
   `finish(reason)`. The loop enforces the mechanics: URL deduplication
   across searches, a **step budget** (`AGENT_MAX_STEPS`, default 5;
   exhaustion forces a reasoned finish) and, alongside it, a **spend cap**
   (`AGENT_MAX_COST_USD`, ADR-048 — money stops the run before the step budget
   does), and the shared enrich/sort/deliver tail (ADR-011/027). The policy
   reply is parsed defensively: anything malformed means finish, never a crash
   or a burned budget.
3. **The live decision journal**: every executed decision is reported through
   a new **`StepReporter` port** → `POST /internal/jobs/{id}/steps` →
   `agent_steps` table (idempotent on `(job_id, seq)`, ADR-016) → the job
   detail payload → the SSE stream (ADR-026, no new streaming code). Journal
   reporting is best-effort by contract: losing a step never fails the job.
   Step `kind` stays an open string end to end so newer agents can add kinds
   without breaking older backends.
4. **Frontend: two demo blocks** on the searches view — "Workflow demo" vs
   "Agent demo" — launching the same form into either mode; agent jobs render
   an `AgentJournal` (query, dedup-aware hit count, the policy's own reason
   verbatim, pulsing "thinking" indicator while live) above the shared
   timeline.
5. **Determinism for tests/e2e (ADR-021)**: `FakeAgentPolicy` scripts
   search → refine (deduplicated to 0 new hits) → reasoned finish, so the
   journal itself demonstrates deduplication and the smoke/Playwright suites
   assert exact step sequences. New contract fixture:
   `agent-step-callback.json` (ADR-025).

Next agentic steps stay in ROADMAP.md: self-critique of results, recurring
searches with memory, human-in-the-loop clarification.

### ADR-031 — Result self-critique before delivery (decided 2026-07-12, extends ADR-030)

**Context**: the ADR-030 loop decides *how to search* but delivers whatever it
collected. A credible agent also judges its own output — "I searched" versus
"I checked that what I found actually answers the goal".

**Decision**: a **`ResultCritic` port** (one LLM call in production, a stable
fake under `AGENT_PROVIDERS=fake`) reviews the collected hits against the goal
once, after the policy finishes and before delivery. The critique returns:

1. an **assessment** (one-two sentences), journaled verbatim as a new
   `critique` step — no backend or contract change needed: the step `kind` is
   an open string end to end (ADR-030) and the frontend renders unknown kinds
   generically;
2. **irrelevant URLs**, dropped from the delivery (the journal reason gets a
   "(dropped N off-topic result(s))" suffix). The prompt tells the critic to
   be conservative — only obvious noise;
3. at most **one gap query**: if set and the `AGENT_MAX_STEPS` search budget
   is not exhausted, the loop runs a single **repair search** (journaled as a
   normal `search` step) and delivers — no re-critique, so the total cost is
   bounded by `max_steps` searches + one critique call.

Parsing is defensive like every LLM reply in this codebase: a malformed
critique degrades to a neutral review (nothing dropped, no gap) and the job
delivers normally. The critic is optional in the use case signature
(`critic=None` keeps the exact ADR-030 behaviour), wired only in agent mode.

### ADR-032 — Human-in-the-loop clarification (decided 2026-07-12, extends ADR-030)

**Context**: an ambiguous goal ("jaguar") makes the agent burn its search
budget on the wrong meaning. A distinctive agent capability is knowing when to
ask instead of guessing.

**Decision**: the policy gains an **`ask` action** — one short question when
the goal is genuinely ambiguous and no clarification is present yet.

1. **Pause**: on `ask`, the loop calls the new `ClarificationRequester` port
   (`POST /internal/jobs/{id}/question`), the job transitions to the new
   **`awaiting_input` status** (migration 0005: status constraint + `question`
   / `answer` columns) and the Celery task ends — a worker never blocks
   waiting for a human. The **reaper ignores `awaiting_input`** (ADR-016
   amendment): the job is paused on the user, not stuck.
2. **Resume**: the user answers via `POST /api/searches/{id}/answer` (owner
   +`awaiting_input` only, else 409). The backend stores the answer, clears
   the journal (replace semantics: the resumed loop starts fresh), flips the
   job back to `pending` and re-dispatches with `clarification` in the task
   request (ADR-025 fixture updated). The task folds the answer into the goal
   — the `AgentPolicy` protocol is unchanged.
3. **One question per job** (cost guard): once a clarification is present (or
   without a clarifier wired), a repeated `ask` degrades to `finish` — no
   question ping-pong. Determinism (ADR-021): the fake policy asks exactly
   when the goal contains "ambiguous" and no clarification yet.
4. **Frontend**: `awaiting_input` renders the question with an answer form in
   the detail view (the SSE stream stays open — not a terminal status); after
   the answer, the dialog stays visible as a recap.

Full sequence diagram: `docs/diagrams/hitl-clarification-flow.puml`.

### ADR-033 — Recurring searches with memory (decided 2026-07-15)

> *Hardened by ADR-053 (2026-07-29)*: the scheduler runs behind a single-leader
> lock, so multiple backend replicas do not launch duplicate recurring jobs.

**Context**: the monitoring use cases (tech watch, real-estate alerts…) need
searches that re-run on their own and can tell what changed — the agent must
live in time, not only answer one-shot requests.

**Decision**:

1. **Saved searches**: a `recurring_searches` table (keyword, mode, interval
   with a 1-minute floor / 7-day ceiling, `last_run_at`) with a minimal CRUD
   (`POST/GET /api/recurring`, `DELETE /api/recurring/{id}`, 20 per user).
   Each run is an ordinary `research_job` linked via `recurring_search_id`
   (kept on deletion, `ON DELETE SET NULL`) — history, results, journal and
   SSE need nothing new.
2. **The scheduler is the backend's background loop** — the existing reaper
   ticker (cadence `SCHEDULER_TICK_SECONDS`, default 60 s) also launches every
   due recurring search. **Rejected: Celery beat** — a fifth process whose
   schedule state would live in the brick that must not own the database
   (ADR-006); the backend ticker already exists and the quota check needs the
   jobs table anyway. Runs count against the owner's daily quota (ADR-017);
   a quota-skipped or failed run still waits for the next interval (never
   retried every tick).
3. **Memory = previously delivered URLs**: the dispatch carries `seen_urls`
   (up to 200 distinct URLs from the search's past runs) in the task request
   (ADR-025 fixture updated). Both modes flag every result with **`is_new`**
   (agent + contract + `search_results.is_new` column, migration 0006 +
   frontend); the agent mode additionally journals a final **`report` step**
   — "N new result(s) since the last run" / "Nothing new since the last run"
   — the agent's own verdict on whether the run was worth it.
4. **Frontend**: a "Recurring searches" block (create/list/delete, last-run
   info); on recurring runs the timeline shows a **new** chip and dims
   already-seen results; one-shot searches are visually unchanged.
5. **Determinism (ADR-021)**: the fake providers return stable URLs, so run 2
   of a recurring fake search reports "Nothing new" — asserted end to end by
   the smoke script (fast tick in the e2e environment).

URL matching uses canonical URLs since ADR-034.

### ADR-034 — Canonical URLs: deduplication and memory matching (decided 2026-07-15)

**Context**: search providers return the same article under cosmetically
different URLs — tracking parameters (`utm_*`, `fbclid`…), fragments, host
casing, parameter order. That double-counts results in one run and, worse,
makes the recurring-search memory (ADR-033) report retagged links as new.

**Decision**: a pure-domain canonicalizer (`domain/urls.py`, stdlib only)
produces the **comparison key**; the displayed URL always stays the original.
Canonical form: scheme/host lowercased, default ports dropped, fragment
dropped, known tracking parameters removed, remaining query sorted, trailing
slash trimmed. Anything unparseable is returned unchanged — a weird URL must
never fail a job, it just deduplicates less well.

Applied at every URL comparison:

1. **workflow mode**: provider hits deduplicated before enrichment (also a
   cost saving: no LLM call for a retagged duplicate);
2. **agentic loop** (ADR-030): cross-search deduplication and the journal's
   `new_hits` counts use canonical keys — including the critique repair
   search (ADR-031);
3. **recurring memory** (ADR-033): `flag_new` canonicalizes both the stored
   URLs and the fresh ones, so a re-tagged link never masquerades as new.

The backend stays unchanged: it stores and forwards raw URLs; canonicalization
is a domain concern of the brick that compares them.

### ADR-035 — Date cascade stage 2: the page's own metadata (decided 2026-07-16, extends ADR-011)

**Context**: for hits without a provider date, the cascade jumped straight to
the LLM — a paid call returning a `medium`-confidence guess, when most
publishers embed the exact date in the page itself.

**Decision**: a **`PageDateFetcher` port** inserted between the provider date
and the LLM: fetch the page (only when the provider gave no date — cost
guard), read **JSON-LD `datePublished`** (object, list and `@graph` shapes)
then **OpenGraph `article:published_time`**. Publisher-declared metadata is
source-authoritative, so it ranks **`high`** — the full cascade is now:
provider (high) → page metadata (high, ADR-035) → LLM (medium) → unknown.

Implementation notes: stdlib parsing (`html.parser` + `json`), bounded fetch
(10 s timeout, download capped at 512 KiB — the metadata lives in `<head>`),
and silent degradation: a dead page, malformed HTML or garbage dates mean "no
date, continue the cascade", never a failed job. The fake stack gains a
`fake-page-datable` hit so the keyless e2e demonstrates every cascade stage.
The LLM enrichment call still runs for every hit (event type + summary,
ADR-027) — stage 2 improves the date, it does not replace the enrichment.

### ADR-036 — Digest webhooks for recurring searches (decided 2026-07-16, extends ADR-033)

> *Hardened by ADR-047 (2026-07-25)*: digests are optionally **HMAC-SHA256
> signed** (`X-Signature-256`) when `DIGEST_SIGNING_SECRET` is set, so a
> consumer can authenticate them; `job_id` remains the at-least-once dedup key.

**Context**: the recurring memory can tell a run found something new, but the
user still has to open the app to notice. Monitoring use cases need a push.

**Decision**: an optional **`webhook_url`** on the recurring search (migration
0007, `http(s)` only). When a recurring run completes **with new results**,
`IngestResults` builds a digest — keyword, run id, `new_count`, and the new
results only (title/url/published_at) — and a **`DigestSender` port**
delivers it. This repository ships the **webhook adapter** (the universal
integration surface: Slack, n8n, Zapier, a fork's endpoint); an e-mail sender
is one more adapter behind the same port.

Rules: strictly **best-effort** (a dead webhook, a deleted recurring search,
or a lookup error are logged and never fail the ingestion), 5 s timeout, no
digest when nothing is new, and the outbound shape is pinned by the
`contracts/digest-webhook.json` fixture (ADR-025 — produced by the backend,
consumed by the user's systems). Note for forks: the URL is user-supplied and
fetched server-side — restrict egress if your deployment is sensitive to SSRF.

### ADR-037 — Opt-in Redis-backed rate limiting (decided 2026-07-16, revisits ADR-017)

**Context**: ADR-017 deliberately kept the per-IP limiter in-memory —
per-instance, benign degradation — and deferred the distributed variant to
"if the backend ever scales horizontally", preferring a reverse-proxy rule.
Forks that do scale out asked for a ready-made in-app option, and the swap
surface was already one file.

**Decision**: a **`RedisWindowLimiter`** behind the existing middleware —
same fixed window, one Redis counter per (scope, client IP, window index) via
`INCR` + `EXPIRE` — activated by **`RATE_LIMIT_REDIS_URL`**; unset keeps the
in-memory limiter (the default stays exactly ADR-017). Two rules worth
noting:

1. **Fail-open**: if Redis is unreachable (tight 1 s budgets), requests pass
   with a warning — the limiter protects LLM spend, it must not turn a Redis
   outage into an API outage.
2. The `auth` and `api` scopes keep independent counters, as before.

The reverse-proxy recommendation of ADR-017 still stands as the zero-code
alternative; this adapter is for forks that want the limit inside the app
(e.g. no shared proxy tier). Integration-tested against the compose/CI Redis
(skipped without `REDIS_URL`), including cross-replica sharing and fail-open.

### ADR-038 — Per-run API spend tracking (decided 2026-07-17)

**Context**: every run spends real money (Claude tokens, Tavily credits) and
the quota (ADR-017) only counts runs, not dollars. Users and forks need to
see what each search actually cost.

**Decision**:

1. **A `UsageMeter`** (pure domain, agent side) is handed to every paid
   adapter: the three Claude adapters record token counts from langchain's
   `usage_metadata` (one `record_llm` per call), the Tavily provider records
   each search. The **fakes record their calls too, with zero tokens** — the
   keyless demo shows honest call counts and a $0 cost.
2. **Pricing is env-driven**, not a hardcoded per-model table (rates rot):
   `LLM_COST_INPUT_PER_MTOK` / `LLM_COST_OUTPUT_PER_MTOK` /
   `SEARCH_COST_PER_CALL`, defaults documented in `.env.example` (Claude
   Opus 4.x and Tavily basic rates at the time of writing). Fake mode prices
   at $0 regardless. Costs are **indicative** — the provider's invoice is the
   source of truth.
3. **One usage callback per task attempt** (`POST /internal/jobs/{id}/usage`,
   fixture `contracts/usage-callback.json`), sent in a `finally` block —
   success, HITL pause and failure all report. The backend **accumulates**
   (`UPDATE … SET x = x + $n`, never replaced by lifecycle updates): retries
   and resumed runs each add their real spend. Best-effort: losing the metric
   never fails the job.
4. **Frontend**: a cost line on the run detail ("$0.0885 — 9 LLM calls
   (8500 in / 1200 out tokens), 2 searches") and, on the searches list, the
   per-run cost plus the total across listed runs.

### ADR-039 — Single-page workbench (decided 2026-07-17, revisits ADR-003's page layout)

**Context**: launching a run navigated to a per-search detail page. For a demo
whose value is *watching* the agent work (live journal, HITL dialog, costs),
the page switch broke the flow — and the user asked for everything on one page.

**Decision**: the searches view becomes a **workbench**: a two-column layout
(launchers + recurring searches + history on the left, the active run on the
right). Launching sets the run **inline** — no navigation; picking a previous
search loads it in the same panel. The follow logic (SSE with polling
fallback, journal, clarification form, cost line, timeline) moves from the
detail view into a reusable **`RunPanel`** component that re-subscribes when
its `id` prop changes and notifies the workbench on terminal status (history
and total cost refresh). The `/searches/:id` route and detail view are
removed — the SPA has two routes left (login, workbench). Deep-linking a
specific run is given up deliberately: this is a demo workbench, not a
document store; a fork wanting shareable URLs can sync the selected run to a
query parameter.

---

### ADR-040 — Ops consoles: Flower + workbench links (decided 2026-07-21)

**Context**: the stack has monitoring UIs (Jaeger for traces, ADR-029) but no
view of the Celery workers themselves — are they up, what ran, what retried,
what failed — and nothing on the workbench points at any of these consoles.

**Decision**: a **Flower** service (`mher/flower`, official Celery monitoring
UI) joins the compose **`observability` profile**, reading the Redis broker
directly — no code change, no new dependency in the agent brick, dev-only like
Jaeger. The workbench gains an **"Ops consoles" card** linking Flower
(`:5555`) and Jaeger (`:16686`) on the app's own hostname, so it also works
when the stack runs on a remote dev box. The links are static: probing each
console to hide dead links was rejected (browsers block the cross-port
`fetch` probes without CORS on the consoles, and the failure mode — a link
that doesn't answer with a stopped profile — is self-explanatory). Production
deployments don't publish these ports (`deploy/docker-compose.prod.yml`
doesn't include the profile); a fork wanting them exposed must put them
behind its own auth proxy — Flower ships none by default.

---

### ADR-041 — Local LLM backend via a chat-model factory (decided 2026-07-22)

**Context**: the live LLM adapters (enricher, policy, critic — ADR-010/030/031)
hardcoded `ChatAnthropic` in their constructors, so running the agent against
a model on the developer's own machine (Ollama) required either code changes
or a whole parallel adapter set.

**Decision**: the LLM *brand* is a construction detail, not a port. The
adapters already typed their logic against langchain's `BaseChatModel` —
prompts, defensive parsing and usage metering are provider-agnostic — so they
now **require the chat model injected** and a single factory
(`adapters/chat_model.py`) builds it from the environment:
`AGENT_LLM_BACKEND=anthropic` (default, hosted, needs `ANTHROPIC_API_KEY`) or
`ollama` (local server at `AGENT_LLM_BASE_URL`, no key; `AGENT_MODEL_ID` then
names the local model). The classes were renamed `Claude*` → `Llm*`
accordingly. Adding a backend (e.g. an OpenAI-compatible local server —
LM Studio, vLLM) is one `elif` in the factory, never a new adapter class:
writing an `OllamaEnricher` would have duplicated every prompt and parser.

Consequences: the worker's fail-fast check (ADR-020) requires
`ANTHROPIC_API_KEY` only on the `anthropic` backend (Tavily stays required in
live mode — search remains remote); the compose worker resolves
`host.docker.internal` (with the Linux `host-gateway` mapping) so a
containerized worker reaches the host's Ollama; with a local backend the
ADR-038 cost rates should be set to 0. Small local models follow the JSON
instructions less reliably — the defensive parsing degrades instead of
failing (ADR-030's guarantee), and an opt-in drift test
(`RUN_OLLAMA_TESTS=1`, mirroring ADR-012) checks a local model against the
same extraction bar as the hosted one. The factory disables Ollama's
*thinking* mode: reasoning models (gemma4, deepseek-r1…) otherwise burn the
whole `num_predict` budget on hidden reasoning and return empty content for
these short strict-JSON tasks — found the hard way, live, on 2026-07-22.

---

### ADR-042 — Batched concurrent enrichment (decided 2026-07-23)

**Context**: both use cases enrich a whole result set, but the `HitEnricher`
port was single-hit and the use cases looped over it — 9 hits meant 9
*sequential* LLM round-trips, the dominant share of a run's wall-clock time
(measured on the ADR-041 full-stack validation).

**Decision**: the port becomes **batch-shaped** — `enrich_many(hits)` returns
one enrichment per hit in order — because that is the shape both callers
actually need; a use case never enriches a single hit. The live adapter
implements it with one `llm.batch(...)` (langchain fans the per-hit calls out
concurrently, replies stay in prompt order), **bounded by `max_concurrency=5`**
so a burst of hits cannot hammer the Anthropic API or overload a local Ollama
(ADR-041). Still one LLM call per hit (ADR-027's cost model is unchanged);
usage is metered on the caller's thread after the batch returns, so the
`UsageMeter` needs no thread-safety. The fakes stay sequential (determinism);
the single-hit `enrich` survives as an adapter convenience for the live drift
tests (ADR-012). Page-date fetches (ADR-035) remain sequential — they only run
for hits without a provider date; parallelizing them is a possible follow-up
if profiles ever show them dominating.

### ADR-043 — Native structured output for the LLM adapters (decided 2026-07-24)

**Context**: the three LLM adapters (enricher, policy, critic) asked the model
to "reply with a single JSON object" in prose and parsed the raw text
defensively. That works but is fragile — precisely where it matters most, on
the small local models the ADR-041 backend enables, which drift from strict
JSON far more than the hosted ones.

**Decision**: each adapter binds a **pydantic reply schema**
(`EnrichmentReply`, `ActionReply`, `CritiqueReply`) through langchain's
`with_structured_output(schema, include_raw=True)` — tool calling on Anthropic
(`function_calling`), grammar-constrained decoding on Ollama (`json_schema`,
its default). The field descriptions carry the instructions the prompts used
to spell out, so the prompts shrink to the task. Conversion functions
(`enrichment_from_reply` etc.) map the validated schema onto the domain types,
still degrading **field by field** (an invented event type → `other`, a prose
date → unknown) so a well-formed but nonsensical reply never voids the whole
result. `include_raw=True` keeps the underlying message so the `UsageMeter`
(ADR-038) still sees token counts.

The legacy text parsers (`parse_enrichment`/`parse_action`/`parse_critique`)
are **kept as a fallback**: when the native path returns no parsed object (a
model that ignored the tool, a schema-validation failure), the raw text runs
through them — the reply degrades, the job never crashes, preserving ADR-030's
"malformed reply is a neutral value, never an exception" guarantee at two
levels now. Conversion is **case-tolerant** on the enum-like fields
(`event_type`, `action`): schema-mode models capitalize freely — found live on
2026-07-24 with gemma4 returning `"Software Release"`, which the strict enum
would have dropped to `other`. The fakes and unit tests exercise both the
structured happy path and the text fallback; the opt-in live tests (ADR-012 /
ADR-041) validate the real tool-use and json_schema paths on each backend.

### ADR-044 — Per-call timeout and retries on the LLM clients (decided 2026-07-24)

**Context**: the chat-model factory (ADR-041) built the clients with no
explicit timeout or retry. A hung call — a cold Ollama loading a large model
into VRAM, a slow network to the Anthropic API — could block a worker for
minutes, and the batched enrichment (ADR-042) makes it worse: one stalled call
holds up the whole batch, hence the whole run. The only safety net was
Celery's task-level retry (ADR-016), which fires on a *failed* task, not on a
merely slow call.

**Decision**: the factory bounds every call, from two env-driven settings with
sane defaults — `AGENT_LLM_TIMEOUT_SECONDS` (60) and `AGENT_LLM_MAX_RETRIES`
(2). On Anthropic they map to the client's native `default_request_timeout`
and `max_retries`. ChatOllama has no direct timeout, so it receives one through
`client_kwargs={"timeout": ...}` (passed to the underlying HTTP client), and
has no built-in retry — a failed local call falls back to the Celery task
retry, which is the right layer for it. The two layers compose: the per-call
timeout caps latency and absorbs transient blips fast; Celery's retry with
backoff still covers a task that fails outright.

### ADR-045 — Model evaluation harness (decided 2026-07-24)

**Context**: the local-LLM backend (ADR-041) raises an immediate question it
gave no way to answer — *which* local model is good enough? The opt-in live
tests (ADR-012/041) are pass/fail drift checks, not a way to compare models
side by side.

**Decision**: a small evaluation harness in `aiagent/evaluation.py` scores a
model on the agent's three LLM capabilities — enrichment, policy, critique —
against a set of **golden cases** with known-good answers, and a CLI prints a
**comparison table** across the models you name
(`python -m aiagent.evaluation ollama:gemma4:latest anthropic:claude-opus-4-8`).
Each capability gets a coarse 0–100% score; the table also shows total latency
and indicative cost (0 for local backends, the ADR-038 env rates for the
hosted one), so the "good enough / fast enough / cheap enough" trade-off is
visible at a glance.

It is explicitly a **directional signal, not a benchmark**: the case set is
tiny and the scoring coarse, enough to separate a model that follows the task
from one that does not, cheaply. It reuses the existing adapters unchanged —
the same `LlmHitEnricher`/`LlmAgentPolicy`/`LlmResultCritic` over a chat model
from the factory (ADR-041) — so it measures exactly what production runs, and a
shared `UsageMeter` (ADR-038) yields the cost column for free. The scoring and
runner are **pure and unit-tested with fakes** (a raised error becomes a
zero-scored result, never stops the sweep); only the CLI touches real, paid
providers, so like the live tests it is invoked by hand, never in CI. Forks
extend the three case lists (`ENRICHMENT_CASES`, `POLICY_CASES`,
`CRITIC_CASES`) with cases from their own domain.

**Amendment — pre-release gate (added 2026-07-27)**: the harness is the
regression net for the part of the system unit tests are blind to. Tests run
against port fakes (ADR-012), so they never touch the real prompts or model; a
prompt edit, a model bump, or a LangChain/LangGraph upgrade can stay CI-green
while the agent's output quality drops. The `--fail-under PCT` flag makes the
live run a **gate**: it prints the table and exits non-zero if any model's
overall score falls under the floor (the pure `failures_below` helper is
unit-tested; the flag validates its 0..1 range before any provider is built).
The deliberate split — the harness's fake-backed scoring/runner already run in
CI via `test_evaluation.py`, but the live gate stays **local, invoked by hand
before a release** — keeps Anthropic keys out of the repo's CI entirely
(GitHub withholds secrets from fork PRs anyway, and a scheduled live run would
spend real budget). Forks that want automation can add a `workflow_dispatch`
job wiring `--fail-under` to their own repo secret; the boilerplate ships only
the local ritual (see COMMANDS.md).

### ADR-046 — LangGraph as the default agent orchestrator (decided 2026-07-25, revisits ADR-030)

**Context**: the agent mode ran on a hand-rolled loop (ADR-030) — pure,
testable, framework-free, and a good teaching artifact. But most projects that
fork this boilerplate build on **LangGraph**, and the loop gave them no
starting point for it. The goal: ship LangGraph as the orchestrator forks start
from, without sacrificing the hexagonal architecture or the loop as a
reference.

**Decision**: a **second orchestrator** for `mode=agent`, selected by
`AGENT_ORCHESTRATOR` (**`langgraph`** default, `loop` keeps ADR-030). It is an
**adapter** (`adapters/orchestration/langgraph_agent.py`): a LangGraph
`StateGraph` whose nodes (`decide`/`search`/`ask`/`finalize`/`critique`) call
the **same domain ports** — so `domain/` and `application/` stay framework-free
and adopting LangGraph is exactly the "swap an adapter, not a rewrite" the
hexagonal split promised. Parity with the loop is asserted test-for-test
(dedup, budget, journal, critique + repair, recurring delta, ask-guard). The
graph topology is diagrammed in `docs/diagrams/langgraph-agent-graph.puml`.

Two capabilities the graph adds:
- **Durable checkpointing** — the graph state is persisted at every super-step
  in **Redis**, keyed by `job_id`. Redis is the worker's own infrastructure
  (the Celery broker), so this respects ADR-006: the worker still never
  touches the database. Postgres — the other obvious store — is therefore
  ruled out here, not just unnecessary. The saver needs **RediSearch**, so the
  compose Redis image becomes `redis/redis-stack-server` (core Redis + the
  module); `redis:7-alpine` works only with `AGENT_ORCHESTRATOR=loop`.
- **Native HITL** (ADR-032) via `interrupt()`: the clarification pause is a
  first-class graph primitive. The worker sees the interrupt, fires the
  `question` callback once (job → `awaiting_input`) and ends; the user's answer
  re-dispatches a task that **resumes the graph from its checkpoint**, so the
  searches done before the pause are not redone — the win over the loop's
  re-dispatch-from-scratch. The pause is detected from the **`invoke()` return
  value** (`__interrupt__`), never from a post-`invoke` `get_state()`: that
  read-back races the Redis checkpoint write and can miss the interrupt,
  silently delivering the empty partial state as a completed job (a
  non-deterministic bug that passed locally and failed in CI, 2026-07-26).
  Reading control signals from the checkpoint store right after writing it is
  the trap; the in-process invoke result is the source of truth.

Design constraints honored: the checkpointed state holds **only JSON-friendly
primitives** (hits/steps as dicts), converted to/from domain types at the node
boundary — the default checkpoint serializer round-trips our frozen dataclasses
only through a *deprecated* pickle fallback, which an exemplary boilerplate must
not depend on. Failure/idempotency semantics match the loop and ADR-016
(deliver replaces, steps upsert on `(job_id, seq)`; a resumed task re-runs the
post-graph enrich/deliver tail, which is idempotent at the backend). The
enrich pass stays batched (ADR-042) and the tail (sort, `flag_new`, deliver)
is shared with the workflow mode.

**Cost**: `langgraph-checkpoint-redis` pulls a heavy transitive chain
(`numpy`, `redisvl`, `ml-dtypes`) for RediSearch/vector features this project
does not use — a deliberate weight trade accepted for a correct, standard,
maintained checkpointer over a hand-rolled one. A fork minimizing image size
can set `AGENT_ORCHESTRATOR=loop` (LangGraph is then unused, though still
installed) or replace the saver with a lean custom `BaseCheckpointSaver`.

### ADR-047 — HMAC signing of digest webhooks (decided 2026-07-25, hardens ADR-036)

**Context**: the digest webhook (ADR-036) POSTs to a user-supplied URL with no
signature. Redelivery is handled — the payload carries `job_id`, so consumers
dedup on it (at-least-once) — but **authenticity** is not: webhook URLs leak
(logs, configs, `Referer`), and anyone who learns the URL can POST a forged
digest the consumer cannot tell from a real one. Dedup stops accidental
doubles; it does nothing against a malicious sender.

**Decision**: an **opt-in HMAC-SHA256 signature**, the same pattern GitHub /
Stripe / Slack use for their webhooks. With `DIGEST_SIGNING_SECRET` set, the
`WebhookDigestSender` signs the exact bytes it sends and adds
`X-Signature-256: sha256=<hex>`; the consumer recomputes
`HMAC-SHA256(secret, raw_body)` and compares in constant time before trusting
the payload. Unset (the default), digests go unsigned — opt-in like the Redis
rate limiter (ADR-037), since the common case is a URL on a trusted internal
tool. It stays a one-adapter concern behind the `DigestSender` port: the domain
and the `job_id` dedup contract are untouched, and the signed bytes are the
canonical body (serialized once, signed and sent), so the consumer verifies
over what it received, never a re-serialization.

Scope kept deliberately small: **body-only** signature (authenticity +
integrity). Replay protection (a signed timestamp the consumer age-checks, à la
Stripe) is a documented extension, not shipped — the digest is idempotent on
`job_id`, so a replay is already a harmless duplicate to a correct consumer.

Consumer verification (Python):

```python
import hashlib, hmac
def valid(secret: str, raw_body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)  # constant-time
```

---

### ADR-048 — Per-job spend cap (cost circuit breaker) (decided 2026-07-29, hardens ADR-030)

**Context**: the agent mode has two bounds — the **step budget**
(`AGENT_MAX_STEPS`, ADR-030) caps the number of decision steps, and
`DAILY_SEARCH_QUOTA` (ADR-017) caps a user's requests. Neither bounds the
**money** a single run spends. The step budget is a coarse proxy: at equal step
counts the cost varies by ~100× with the model and the per-step token size, and
the `UsageMeter` (ADR-038) already computes `cost_usd` — but only *after* the
run, for reporting. A pathological run (a long transcript, a verbose model, a
mis-set expensive model) can burn an unbounded budget while staying under the
step limit. For a fork that wires a real key, a surprise bill is the scariest
failure mode.

**Decision**: a pure-domain `SpendGuard` (`meter` + `pricing` + `cap_usd`) in
`domain/usage.py`, checked by **both orchestrators** (the hand-rolled loop and
the LangGraph `StateGraph`) at the **same seams as the step budget** — before
each `decide`, and to skip the self-critique's extra LLM call / repair search.
When the run's indicative cost crosses `AGENT_MAX_COST_USD`, the agent degrades
to a **clean forced finish** and still delivers what it found — the same
"degrade, never crash" contract as the step budget. It reads the *same live
meter* the adapters feed, so nothing is double-counted, and it stays pure: the
guard is domain, the wiring (meter + env-priced `Pricing` + the setting) is in
the Celery task.

Deliberate choices:

- **Enabled by default at $2.00/job**, well above a normal run (cents). The
  keyless fakes (ADR-021) price at **$0**, so the guard never trips in the
  demo/e2e — it only bites live paid runs, exactly where it should. `0`
  disables it.
- **Model-independent**: a cheap and an expensive model get the same dollar
  ceiling, unlike the step budget.
- **Bounds the tail transitively**: capping the exploration loop caps the number
  of searches, hence the collected hits, hence the batched enrichment cost
  (ADR-042) that runs after the loop.
- **Not deduped across retries** — consistent with ADR-038's "cost is
  intentionally not idempotent": a Celery retry re-spends, and each attempt is
  capped independently.

### ADR-049 — Public API contract fixtures + backward-compat rule (decided 2026-07-29, extends ADR-025)

**Context**: ADR-025 pins the **internal** contract (Rust ↔ Python) with golden
fixtures asserted on both sides. The **public** contract (Rust → Vue) had no
such net: the same shapes (`SearchResult`, `JobUsage`, `AgentStep`, the job
detail) were hand-typed a third time in `frontend/src/api.ts`, with nothing
tying them to what the backend actually serves. A Rust field rename or an added
enum variant compiled, passed every Rust and Python test, and reached the
browser as a silent `undefined` — found by a user, not CI. The boilerplate's
goal is scale-readiness (bricks deployable on separate machines, ADR-006) while
staying a monorepo, so the boundary correctness must be machine-checked.

**Decision**: extend the ADR-025 fixture pattern to the public contract, and add
the missing third side.

1. **Fixtures** `contracts/search-job-detail.json` and `recurring-search.json`
   pin the exact wire shape of `GET /api/searches/{id}` and `/api/recurring`.
2. **Producer (Rust)**: `job_detail_json` / `recurring_search_json` are the
   single serialization path used by the handlers, made `pub` so
   `backend/tests/contract.rs` asserts their output **equals** the fixtures.
3. **Consumer (Vue)**: `api.ts` is rebuilt on **`zod`** schemas as the single
   source of truth — the TS types are derived (`z.infer`, no more hand-written
   duplicates) and every data response is **validated at runtime** (`.parse`),
   so a drift throws a clear `ZodError` client-side instead of an `undefined`.
   `frontend/src/__tests__/contract.spec.ts` validates the same fixtures.

**Backward-compat rule (the scale-ready part)**: because the bricks deploy
independently, a rolling deploy can briefly pair a new frontend with an old
backend (or vice versa). So the contract is **additive by default**: the zod
objects are **non-strict** (unknown fields are stripped, not rejected), an
older frontend keeps working when a newer backend adds a field; and a removal
or rename takes a **deprecation window across two releases**, never a hard flip.
This is the discipline the monorepo's atomic-source guarantee does *not* give
you at the deploy layer.

**Cost**: one dependency (`zod`, ~18 kB gzip in the bundle) — the price of real
runtime validation on the one side that had none.

**Amendment — OpenAPI documentation (added 2026-07-29)**: the public API now
also carries a machine-generated **OpenAPI 3.1 spec** (`utoipa`), served as
interactive **Swagger UI at `/api/docs`** and raw JSON at `/api/openapi.json`
(assets vendored — self-hosted, no CDN). This is **documentation, not a second
source of truth**: the contract stays pinned by the fixtures + zod above; the
spec is derived from the same handlers and DTOs, and a committed
`docs/openapi.json` is drift-checked in CI (`backend/tests/openapi.rs`,
regenerated via `cargo run --example openapi`). One deliberate architectural
note: `utoipa::ToSchema` is derived on a few **domain** wire types
(`SearchResult`, `JobUsage`, `AgentStep`, the enums) alongside the `serde`
derives already there. This does **not** breach the domain-purity rule
(ADR-002/004): `utoipa` core is a representation-level derive with **no I/O** —
same category as `serde` — and the framework pieces (Swagger UI, the `OpenApi`
assembly, the routes) live entirely in the HTTP adapter. Deriving it in the
adapter instead would mean a hand-kept duplicate of every wire shape — exactly
the drift this ADR removes.

A **generated TS/Python client** from that spec (via `openapi-typescript` /
`datamodel-code-generator`) remains deferred (§5): it earns its keep only when
the bricks split into **separate repos**, where a published, versioned schema
becomes the decoupling artifact. Now that the spec exists, that step is cheap.

### ADR-050 — Metrics: the third observability pillar (decided 2026-07-29, extends ADR-029)

**Context**: ADR-029 shipped traces (and its amendments added per-call LLM spans
and log↔trace correlation), explicitly deferring metrics. Traces answer *why
was this run slow/expensive*; they cannot answer *is the fleet healthy, what is
the trend, alert me* — that is the aggregate view, and it is exactly what an
operator of a forked deployment needs.

**Decision**: OpenTelemetry **metrics**, behind the same opt-in gate
(`OTEL_EXPORTER_OTLP_ENDPOINT`) and the same OTLP push as traces.

1. **Agent** emits, via a proxy-meter module (`aiagent/metrics.py`, no-op until
   the provider is installed): `aiagent.llm.call.duration` (histogram),
   `aiagent.llm.tokens` (counter, by operation/type/backend), `aiagent.job.cost`
   and `aiagent.jobs` (counters, by outcome). Recording sits in the adapters and
   the Celery task — the domain stays untouched — reusing the hooks the LLM
   spans already added.
2. **Backend** emits HTTP **RED** metrics from the ADR-018 middleware:
   `http.server.requests` (counter) and `http.server.duration` (histogram), by
   method / **matched route** (`/api/searches/{id}`, low cardinality) / status.
3. **The collector is now the OTLP entry point** (dev-only, observability
   profile): an **OpenTelemetry Collector** receives OTLP and fans it out —
   traces → Jaeger, metrics → a Prometheus scrape endpoint — with **Grafana**
   for dashboards. `OTEL_EXPORTER_OTLP_ENDPOINT` therefore points at
   `otel-collector:4318` instead of Jaeger directly (Jaeger only ingests
   traces); the collector forwards them, so tracing is unchanged. Production
   forks point the variable at their own collector — no code change.

Same discipline as ADR-029: **no-op instruments when telemetry is off** (zero
cost in the keyless demo/CI), instrumentation in the adapters not the domain,
and tested without a live backend — the agent metrics with an in-memory metric
reader (`test_metrics.py`), the backend RED path by the existing middleware
tests recording into a no-op meter, and the whole pipeline once end-to-end
(apps → collector → Prometheus → a provisioned Grafana dashboard). What to watch
and the PromQL for each signal is in [OBSERVABILITY.md](OBSERVABILITY.md); a
starter "AI agent overview" dashboard ships provisioned. The three pillars are
now in place: traces, logs, metrics.

### ADR-051 — Multi-provider search aggregation (decided 2026-07-29, extends ADR-009)

**Context**: a single search engine (ADR-009, Tavily) is a single point of
failure and of recall — different engines index different things, and one
provider's quota/outage (the `Error 432` that motivated the ADR-009 fail-fast
fix) takes the whole run down. A boilerplate for agents should show how to fan a
query across sources.

**Decision**: an `AggregatingSearchProvider` — itself a `SearchProvider`
(the port) — wraps several inner providers, queries them **concurrently**, and
**fuses** their results. Pure adapter-layer composition: the agent loop, the
domain and the ports are untouched (the hexagonal payoff). Selected by
`AGENT_SEARCH_PROVIDERS` (comma-separated: `tavily`, `duckduckgo`); one name
builds the bare adapter, several build the aggregator. A **keyless DuckDuckGo**
adapter ships as the second source, so a fork can run live search with zero
credentials.

Two design choices:

- **Reciprocal Rank Fusion** (`fuse_by_rrf`): a URL's score is `Σ 1/(k+rank)`
  over the engines that returned it (`k=60`), so a result several engines agree
  on outranks a single-engine one, with no weights to tune. Deduplication is by
  canonical URL (ADR-034); the richer hit (one carrying a date) wins a tie.
- **Partial-failure tolerance** — a deliberate departure from ADR-009's
  fail-fast. A single provider must fail the job (silence hides a dead search);
  but with several, a failing or slow engine is logged and the run continues
  with the survivors, each bounded by a per-provider timeout. The aggregator
  raises only when **every** provider fails.

Metering is unchanged (ADR-038): each inner provider records its own credit, so
N engines = N credits per aggregated query. The fusion and the aggregator's
concurrency/tolerance are unit-tested with fake providers (no network); the
concrete DuckDuckGo adapter, like Tavily, is not exercised in CI (ADR-012).

### ADR-052 — LLM fallback chain (decided 2026-07-29, extends ADR-041/044)

**Context**: with search aggregated (ADR-051), the **LLM provider is the biggest
remaining external single point of failure** — if Anthropic is down or the key
is quota'd, *every* agent call fails and the job dies. Per-call retries (ADR-044)
absorb a transient blip; they do nothing for a provider *outage*.

**Decision**: a fallback chain via LangChain's `.with_fallbacks()`, at the same
construction seam as the model factory (ADR-041) — no new port. Each adapter
binds structured output on the primary and each fallback, then chains them:
`structured_with_fallbacks([primary, *fallbacks], schema)`. The primary runs
first; on error the next model is tried, in order. Fallbacks are configured with
`AGENT_MODEL_FALLBACKS` — `backend:model_id` specs (the eval harness grammar,
ADR-045), e.g. `anthropic:claude-haiku-4-5,ollama:qwen3:14b` — so the chain can
cross providers, ending on a **keyless local Ollama** for a last-resort survival
with zero external dependency. Empty by default: a single model means no wrapper,
i.e. the exact previous behavior (backward compatible, so the `fallbacks=[]`
default leaves every existing call site untouched).

The mechanism is unit-tested with fakes whose structured output is a real
`RunnableLambda`, so LangChain's actual fallback path is exercised (a raising
primary, a returning secondary) — no network. Usage metering (ADR-038) reads the
survivor's `usage_metadata`, so cost is attributed to whichever model answered.

### ADR-053 — Single-leader background loop (decided 2026-07-29, hardens ADR-016/033)

**Context**: the backend runs the reaper (ADR-016), the recurring-search
scheduler (ADR-033) and the refresh-token purge (ADR-008) on a shared in-process
ticker (`main.rs`). The API layer is otherwise stateless and horizontally
scalable (JWT sessions, DB-polling SSE ADR-026, the Redis-backed rate limiter
ADR-037) — **except this loop**. With several replicas each would run it every
tick, and the scheduler is **not idempotent**: it would launch *duplicate*
recurring jobs. That is a correctness bug the moment the backend scales past one
instance — exactly the multi-machine target of this boilerplate.

**Decision**: gate the tick behind a `LeaderLock` so exactly one replica runs
the loop. `PostgresLeaderLock` takes a **session advisory lock**
(`pg_try_advisory_lock`) on a fixed key: the first replica to win runs the tick
and releases after, so leadership rotates naturally tick-to-tick; the others
skip. No new infrastructure — Postgres is already there — and no leader election
protocol. The connection that took the lock is **held until release** (a session
advisory lock must be unlocked on the same connection) and returned to the pool
between ticks; if a leader crashes mid-tick its session ends and the lock frees
automatically. The in-memory / single-instance path uses a `NoopLeaderLock` that
always leads. The reaper and purge are idempotent, but gating the whole tick
also spares every replica a redundant DB scan.

Tested: the `NoopLeaderLock` in a unit test, and the Postgres mutual-exclusion
(a second instance is locked out until the first releases) in a `DATABASE_URL`-
gated integration test with a per-run key, so it never contends with a live
instance. This closes the backend's horizontal-scaling story.

### ADR-054 — HTTP security headers (decided 2026-07-29)

**Context**: neither the API, the SPA nor the TLS edge set any security headers
— a standard hardening gap (clickjacking, MIME sniffing, referrer leakage, no
declared content policy).

**Decision**: set them at the layer that owns each response, so there is exactly
one source per header (no duplicates).

- **Backend API** (`security_headers` middleware, outermost so it covers error
  responses too): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, and a **maximally strict CSP**
  `default-src 'none'; frame-ancestors 'none'` — a browser should never load
  anything from, or frame, a JSON endpoint.
- **Frontend SPA** (nginx, on the HTML `location /` only — the `/api` proxy keeps
  the backend's headers): the same three headers plus the app's real
  **`Content-Security-Policy`** — `default-src 'self'`, `connect-src 'self'`
  (same-origin API + SSE), `script-src 'self'`, `style-src 'self' 'unsafe-inline'`
  (Vue SFC styles), `object-src 'none'`, `frame-ancestors 'none'`. The Vite build
  ships external JS/CSS with no inline scripts, so `script-src 'self'` holds.
- **TLS edge** (Caddy): `Strict-Transport-Security` — HSTS belongs where HTTPS is
  terminated, not on the app behind the proxy over plain HTTP.

The API middleware is unit-tested (headers present on every response, errors
included); the SPA and edge headers were verified live against the running
stack. Tune the SPA CSP if a fork adds an external font/CDN or images from third
parties (`img-src`).

### ADR-055 — Security hardening pass: SSRF, constant-time, weak secrets (decided 2026-07-29)

**Context**: a review of every trust boundary (see the checklist below) found
the auth, cookies (HttpOnly/Secure/SameSite=Strict), CORS (absent = same-origin
only), JWT (HS256 pinned + exp), argon2id, parametrized SQL and the fail-fast
secret gate (ADR-020) all sound — but four gaps worth closing.

1. **SSRF on the digest webhook** (highest — user-controlled URL). `webhook_url`
   was validated for scheme only, and `reqwest` followed redirects with no IP
   filtering: an authenticated user could point a recurring digest at
   `169.254.169.254`, `redis:6379`, the internal API… `WebhookDigestSender` now
   resolves the host and **refuses any non-public address** (loopback, private,
   link-local, CGNAT, ULA…) *before* sending, and the client **disables
   redirects** so a public URL cannot 3xx to an internal one.
2. **SSRF on the page fetcher** (agent, blind). The date-fetch stage pulls result
   URLs — attacker-influenceable. It now applies the same public-host check
   (`is_public_host`, injectable for tests) and **disables redirects**; a blocked
   host degrades silently to "no date", like any other fetch failure.
3. **Non-constant-time internal-token compare**. `check_internal_token` used
   `!=`, a timing side-channel; replaced with a `constant_time_eq` byte compare,
   matching the constant-time care the HMAC path (ADR-047) already takes.
4. **Weak-secret warning**. A short (`< 32` char) `JWT_SECRET` / `INTERNAL_API_TOKEN`
   is a brute-forceable key; the config now warns (not a hard fail, so it never
   breaks an existing deployment).

Each fix is unit-tested (the IP classifier and host guard with literals — no
network; the token compare; the config warning). **Residual risk**: the SSRF
guards resolve then let the client re-resolve, so a determined **DNS-rebinding**
attacker could still slip through — mitigating that fully needs pinning the
connection to the checked IP (a documented follow-up); the guards stop every
realistic case (internal hostnames, literal internal IPs, `localhost`).
*Hardened by ADR-056 (2026-08-01)*: the digest sender now filters addresses in a
custom connect-time DNS resolver, closing that rebinding window.

**Scope note — inter-brick traffic is not affected.** The SSRF guards sit only
on the two surfaces reaching the outside world: the *user-supplied* digest
webhook and *arbitrary result-page* URLs. The trusted internal channels
(backend → agent via `AGENT_API_URL`, agent → backend via `BACKEND_INTERNAL_URL`,
Postgres/Redis) use configured endpoints and never pass through these guards, so
a Docker-network or private-network deployment is unaffected. The one legitimate
case the guard would otherwise block — a fork whose **notification** service
(n8n, a relay) lives on the same private network — is covered by an opt-in,
`DIGEST_ALLOW_PRIVATE_WEBHOOKS=true` (default off): it flips the digest sender's
`allow_private`, allowing internal webhook targets on a trusted network. The
page fetcher keeps no opt-in — result pages are public by nature.

---

### ADR-056 — Security hardening pass 2: token-reuse detection, input caps, DNS-rebinding (decided 2026-08-01)

**Context**: a follow-up to ADR-055 closing three remaining gaps — one it
explicitly deferred (DNS-rebinding) and two on inputs.

1. **Refresh-token reuse detection + family revocation** (ADR-008 hardened).
   Rotation used to *delete* the presented token, so replaying a stolen cookie
   was indistinguishable from an unknown token — the theft went unnoticed while
   both parties kept refreshing. Rotation is now a **lineage**: every token
   carries a `family_id` (one per login, inherited on each rotation), and a
   rotated-away token is **marked `consumed_at`, not deleted**. Replaying a
   consumed token is therefore detectable and treated as a compromise — the
   **whole family is revoked** (`delete_family`), killing the thief's rotated
   token too and forcing re-authentication. Other logins (their own families)
   are untouched. Consumed tokens are kept only until they expire; the existing
   reaper purge (`delete_expired`) cleans them, and logout revokes the family.
   Trade-off: a genuine double-submit of the *same* token (two tabs racing)
   trips the detector and logs that lineage out — the accepted, standard cost of
   reuse detection. Migration `0009` adds `family_id`/`consumed_at` (existing
   rows each get their own family via `gen_random_uuid()`, so they stay usable).
2. **Input length caps** (ADR-017 abuse-protection family). `keyword` (≤ 200),
   the clarification `answer` (≤ 2000) and `webhook_url` (≤ 2048) were bounded
   for emptiness/scheme only — an authenticated user could store multi-KB
   free-text. The **domain** now caps each (measured in `chars`), returning the
   new `JobError::{KeywordTooLong, AnswerTooLong, WebhookUrlTooLong}` → `422`.
   Domain-level so every caller (one-shot, recurring, clarification) inherits it.
3. **DNS-rebinding on the digest webhook** (the ADR-055 follow-up). The
   resolve-then-check pre-flight left a TOCTOU: an attacker's DNS could answer a
   public IP to the check and `127.0.0.1` to the connect. `WebhookDigestSender`
   now installs a custom **`reqwest` DNS resolver** (`PublicOnlyResolver`) that
   filters non-public addresses at connect-time resolution, so the validated and
   the connected addresses are the *same* resolution. `ensure_public_host` stays
   as a fast pre-flight for a clear error; the resolver is the race-free backstop.
   The `DIGEST_ALLOW_PRIVATE_WEBHOOKS` opt-in keeps the default resolver, so
   internal notification targets on a trusted network are unaffected (ADR-055).

Each fix is unit-tested with fakes/literals — no network, no paid service: the
reuse-detection lineage and family-scoped revocation (in-memory + Postgres), the
domain caps, and the resolver (via `localhost`/literal, offline).

---

### ADR-057 — Security audit log + per-account login throttle (decided 2026-08-01)

**Context**: the hardening passes (ADR-055/056) closed attack surfaces but left
abuse **unobservable** and one brute-force vector open. Two additions, both in
the abuse-protection family (ADR-017).

1. **Security audit log.** An append-only `security_events` table (migration
   `0010`) records abuse-relevant moments — `login_failed`, `login_throttled`,
   `refresh_reuse_detected` (ADR-056), `quota_exceeded` (ADR-017) — behind a
   `SecurityAudit` port (in-memory + Postgres). Recording is **best-effort**: it
   never fails the request that triggered it, and every event is *also* emitted
   as a structured log line (ADR-018), so it surfaces in the observability
   pillars (ADR-050) even without a database query. `kind` is free text so a
   fork adds its own kinds without a schema change (like `AgentStep.kind`). The
   `RefreshReuseDetected` event is raised inside `RefreshSession` — the only
   layer that can tell a replay from an unknown token — carrying the `user_id`;
   the edge events (`login_*`, `quota_*`) are raised in the HTTP handlers, where
   the client IP (first `X-Forwarded-For`, trusted proxy — ADR-014/015) is
   known. The background loop purges events older than
   `SECURITY_EVENT_RETENTION_DAYS` (default 90; 0 = keep forever), alongside the
   refresh-token purge. No read endpoint ships — the operator reads it via SQL
   or the logs; a guarded admin route is a documented extension.
2. **Per-account login throttle.** The per-IP limiter (ADR-017/037) does not
   stop credential-stuffing that rotates IPs against **one** account. Login now
   also passes a throttle **keyed by the normalized email**, capped at
   `LOGIN_MAX_ATTEMPTS_PER_MINUTE` (default 10), reusing the exact fixed-window
   `Limiter` (in-memory, or Redis-shared via `RATE_LIMIT_REDIS_URL` when scaled
   out — ADR-037). It is checked **before** the deliberately-costly argon2
   verify, so a throttled account also sheds that CPU load, and it refuses even
   a correct password during the cooldown. Trade-off: an attacker can thus
   briefly lock a victim out (the accepted cost of account throttling); the
   window is short (per-minute) and IP-rotation no longer helps them.

Tested end-to-end with fakes: the audit roundtrip + retention purge (in-memory +
Postgres), the reuse event (`RefreshSession`), and the HTTP throttle + audit
(three logins against a cap-of-2, asserting the `429` and the recorded events).

### ADR-058 — Pivot: onchain approval-risk scanning and auto-revocation via KeeperHub (decided 2026-08-02, revisits ADR-002/006/011/025/030/032/033/036/049)

**Context**: this fork repurposes the web-research boilerplate for the
KeeperHub "Last Mile" hackathon into a concrete, immediately-usable safety
tool ("Approval Firewall"): a user submits a wallet address, the agent scans
its outstanding ERC-20 **token approvals** across chains for malicious or
risky spenders, and — for the DANGEROUS tier only — **auto-revokes** the
approval as a real onchain transaction through KeeperHub's direct-execution
API. Every port from the original boilerplate is reused; only the domain
model, two outbound adapters, and the field names on the existing contracts
change. The hexagonal boundaries (ADR-002), the worker-never-touches-the-DB
rule (ADR-006), and the fixture-pinned contract (ADR-025/049) all hold
unchanged.

**Decision**:

1. **Domain rename, not a new domain.** `ResearchJob` → `ScanJob`
   (`keyword: String` → `wallet_address: String`, validated as `0x` + 40 hex
   chars — `is_valid_evm_address`, Rust and the same shape check in Python);
   `SearchResult` → `ApprovalFinding` (`chain_id`, `token_address`,
   `token_symbol`, `spender_address`, `spender_name`, `approved_amount`,
   `tier`, `malicious_behavior: Vec<String>`, `explanation`, `is_new`,
   `revocation_status`, `revocation_tx_hash`, `raw`). Migration `0011`
   **renames** `research_jobs` → `scan_jobs` (+ column, + indices) and
   `recurring_searches.keyword` → `wallet_address` in place (job/user history
   survives); `search_results` is dropped and replaced by `approval_findings`
   since its shape has no equivalent in the old table (title/url/snippet/date
   have no approval-risk analog). `EventType`/`DateConfidence` (ADR-027) are
   removed with no replacement — an approval finding has no publication date
   to classify.
2. **`RiskTier` is a closed, deterministic vocabulary — never an LLM
   score.** `Safe` / `Watch` / `Dangerous` is assigned by a pure function,
   `classify_risk(approval)`, reading GoPlus's own verified signals
   (`malicious_address`/`malicious_behavior` on the token, `is_open_source` on
   the spender) — never by prompting a model. This is a safety property, not
   a style choice: the LLM (`LlmThreatIntel`) only ever produces the
   human-readable `explanation` string; a hallucinated judgment can never
   upgrade or downgrade what gets auto-revoked. The Rust backend stores and
   re-serves the tier verbatim — it has no `classify_risk` of its own and
   never recomputes one.
3. **New outbound port + adapter: `ApprovalSource`.** `GoPlusApprovalSource`
   calls GoPlus Security's `token_approval_security` endpoint (keyless,
   rate-limited; `GOPLUS_API_KEY` optional for sustained use) and combines the
   token-level and spender-level malicious signals into one `RawApproval` per
   (chain, token, spender). Reuses the existing `ThreatIntel` port
   unchanged — only its live adapter (`LlmThreatIntel`) and its fake
   (`FakeThreatIntel`, still delegating the tier to `classify_risk` for
   determinism, ADR-021) changed.
4. **New outbound port: `ApprovalRevoker`.** The only place the agent writes
   to the world. `KeeperHubApprovalRevoker` calls KeeperHub's direct-execution
   REST API (`POST /api/execute/contract-call`, Bearer `kh_...`) and is
   engineered around three of its issue-tracker bugs from day one: (a)
   `chainId`/`functionArgs`/`gasLimitMultiplier` must be sent as **JSON
   strings**, not numbers (#1841); (b) a **reused `Idempotency-Key` caches a
   failure** — a fresh UUID is minted per attempt, never per finding (#1840);
   (c) the POST response **never carries `transactionHash`** — the adapter
   always polls `GET /api/execute/{executionId}/status` until `completed` or
   `failed` (#1784). `KEEPERHUB_SIMULATE_ONLY` (default `false`) forces every
   call to KeeperHub's `simulate=true` dry-run regardless of tier, for
   rehearsals without spending real gas. `FakeApprovalRevoker` "succeeds" with
   a synthetic tx hash under `AGENT_PROVIDERS=fake` — no test may trigger a
   real revocation (extends the ADR-012 rule: live/paid-service tests are
   opt-in via `RUN_LIVE_TESTS=1`, but KeeperHub execution has **no** opt-in
   test path at all, live or fake-gated, by design).
5. **Auto-revoke gate: DANGEROUS only, unconditional.**
   `approvals_to_auto_revoke(findings)` filters to the DANGEROUS tier and
   nothing else decides whether a revoke fires — both orchestrators (the
   ADR-046 LangGraph graph and the ADR-030 hand-rolled loop) call the same
   shared `revoke_dangerous()` (`application/execute_revocations.py`) right
   after the scan/triage step, before `flag_new`/report. WATCH and SAFE
   findings are only *surfaced*, never acted on automatically — revoking them
   would need a second, blocking confirmation, and ADR-032's job model
   supports exactly **one** clarification slot per job; stacking a second
   synchronous question would need new backend contract fields for a
   secondary use case (deferred, tracked in `ROADMAP.md` as a frontend-driven
   manual revoke instead of a second HITL prompt).
6. **Scope cut: ADR-031's self-critique node is dropped, not ported.** It
   existed to judge "does this search hit actually answer the goal" —
   off-topic URLs have no equivalent in approval scanning (a fetched approval
   either exists on the chain queried or it does not; there is nothing to
   critique). The LangGraph graph (ADR-046) is simplified to
   `decide → scan → ask → finalize`, with the revoke step folded into
   `finalize` via `revoke_dangerous()`. `ROADMAP.md`'s ADR-031 line is marked
   superseded rather than rewritten.
7. **Contracts** (ADR-025/049, fixtures updated, both sides asserted):
   `POST /api/searches` and `POST /api/recurring` take `wallet_address`
   instead of `keyword`; `POST /tasks` carries `wallet_address` and
   `seen_approval_keys` (was `seen_urls`) — the memory key is now
   `"{chain_id}:{token_address}:{spender_address}"` lowercased
   (`ApprovalFinding::approval_key`), computed by
   `recent_approval_keys_for_recurring`; `POST /internal/jobs/{id}/results`
   carries `results: ApprovalFinding[]`; the digest webhook (ADR-036) carries
   `wallet_address` and `new_results: {token_symbol, spender_address, tier,
   revocation_status}[]` instead of `{title, url, published_at}[]`.
8. **Config**: `AGENT_PROVIDERS` defaults to `live` combining GoPlus (keyless-
   capable) + Anthropic (explanation text only) + KeeperHub (required key).
   `agent-worker`'s fail-fast startup check (ADR-020) now requires
   `KEEPERHUB_API_KEY` unconditionally outside `fake` mode — a worker that
   cannot execute a revocation is misconfigured for this agent, whichever job
   arrives first. `AGENT_SCAN_CHAIN_IDS` (default `1,8453,42161,137`) replaces
   `AGENT_SEARCH_PROVIDERS`; Tavily/DuckDuckGo and the `aggregating_search`
   adapter (ADR-051) are removed with no replacement — there is no "search
   the web" step left in this domain.
9. **Audit trail, extending the three observability pillars (ADR-018/029/050)
   to the one call that moves money onchain.** `KeeperHubApprovalRevoker`
   wraps every attempt in a `keeperhub revoke` span (chain/spender/tier/outcome
   attributes, no-op unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set — same pattern
   as `llm_span`), records `aiagent.revocations` (counter, by tier/outcome) and
   `aiagent.revocation.call.duration` (histogram) — new instruments in
   `metrics.py` — and logs a structured `"revocation attempt"` line for
   **every** outcome, not just failures, so a spike in failed revocations is
   greppable/alertable without a DB query. The Rust side logs a matching
   `"scan completed"` line in `IngestResults::complete` (finding/dangerous/
   revoked counts) at the moment a scan's findings land in the system of
   record — the durable half of the audit trail is the `approval_findings`
   rows themselves (tier, revocation_status, revocation_tx_hash), kept forever
   per job; these logs/spans/metrics are the searchable, real-time layer on
   top, not a replacement for the DB rows.

Tested end-to-end with fakes: `classify_risk` unit tests for all three tiers,
`GoPlusApprovalSource`/`KeeperHubApprovalRevoker` against a stubbed HTTP
server (string-typed body, fresh idempotency key per call, status polling
including a `failed` terminal state, and the `simulate=true` synchronous
response shape confirmed live against the real API), `approvals_to_auto_revoke`
DANGEROUS-only filtering, both orchestrators' scan→revoke→report sequencing
including revocation-failure and no-revoker paths on both the LangGraph and
hand-rolled orchestrators, the revoke span/metrics/audit-log line (in-memory
OTel exporter/reader + `caplog`), and the full contract fixture set on both
the Rust and Python sides.

### ADR-059 — Pre-frontend security audit of the execution path (decided 2026-08-02, hardens ADR-058)

**Context**: before building any UI on top of it, the whole server side was
audited specifically as what it is — a tool that spends a stranger's money by
broadcasting transactions on their behalf. The question asked of every code
path was not "is this correct?" but "what does this do when it is wrong, and
who pays for it?". Five issues came out of it; four were fixed, one is
documented as accepted.

1. **Provider flags were read with bare truthiness — fixed.** `classify_risk`
   tested `raw.get("malicious_address")` directly. GoPlus returns `0`/`1`
   **ints** on this endpoint (confirmed live against the real API during the
   audit), so the code worked — by luck. `bool("0")` is `True` in Python, so
   the day that provider returns `"0"` as a string, every clean spender reads
   as flagged and gets **auto-revoked with a real transaction**. Replaced with
   `flag_state()`, a strict tri-state read (`True`/`False`/`None` for
   unrecognized), and the adapter now passes provider values through verbatim
   instead of pre-coercing them with `bool()`, so the domain owns the one
   coercion that matters.
2. **Unknown signals now resolve per-flag, in the direction that is safe for
   the action the tier authorizes — fixed.** These are deliberately not the
   same direction. `malicious_address` unknown resolves to *not* dangerous:
   DANGEROUS is the only tier that spends gas, so it demands a positive,
   recognized signal — never revoke on a shrug. `is_open_source` unknown
   resolves to *not* verified, i.e. WATCH, which is precisely that tier's
   meaning ("unable to audit") and costs nothing but a line in the UI. The
   previous default assumed unknown contracts were open-source and filed them
   SAFE, which hid exactly the contracts least worth trusting.
3. **The provider's trust list was fetched and ignored — fixed.**
   `trust_list` was carried into `raw` but never read. A spender that the
   provider both trusts and flags now degrades to WATCH instead of DANGEROUS:
   contradictory signals are a question for a human, not grounds to broadcast.
   Without this, one bad upstream flag on a router every wallet approves
   (Uniswap, Permit2) would have auto-revoked live positions.
4. **A dry run reported `revoked` — fixed, and it is why `Simulated` exists.**
   With `KEEPERHUB_SIMULATE_ONLY=true` the adapter returned
   `RevocationStatus.REVOKED` with no transaction hash, so the dashboard would
   show a still-live draining approval as neutralized. `REVOKED` now means one
   thing only — broadcast and confirmed, hash attached — and dry runs get their
   own `SIMULATED` status end to end (Python enum, Rust enum, migration `0012`
   widening the CHECK constraint, journal wording). Paired with a new
   fail-fast guard, `forbid_non_executing_modes`: in `APP_ENV=production` the
   worker refuses to boot under `AGENT_PROVIDERS=fake` (fabricated tx hashes)
   or `KEEPERHUB_SIMULATE_ONLY=true`. A security tool that claims protection it
   never applied is worse than no tool, so this fails loudly rather than
   degrading quietly.
5. **Unbounded agent→backend result payloads — accepted, not fixed.**
   `explanation`, `malicious_behavior` and the pass-through `raw` JSONB have no
   length caps, unlike the ADR-056 inputs. The endpoint is authenticated with
   the internal token (ADR-005/006) and the only caller is our own worker, so
   the realistic failure is accidental DB bloat from a hostile token name, not
   an open door. Adding caps under deadline risks rejecting a legitimately
   large wallet — a worse failure for this product than the bloat. Revisit if
   the callback surface is ever exposed beyond the worker.

Two further findings were investigated and deliberately left alone. A Celery
retry can broadcast a **duplicate revocation**, because the idempotency key is
minted fresh per attempt (required: KeeperHub caches failures against a reused
key — #1840). Making the key deterministic would stop the duplicate but also
make a transient failure permanently unretryable for that job; since
`approve(spender, 0)` twice is a state no-op and gas is sponsored, the
duplicate is the cheaper failure. And **one chain failing fails the whole
multi-chain scan** — correct for a security tool, where "no dangerous
approvals found" must never be reported for a chain that was never reached.

Verified against a real database, not just fakes: migrations `0001`–`0012`
applied to the compose Postgres and the full integration suite run against it,
including a new test pinning that `simulated` survives the round trip and is
never read back as `revoked`. That run also surfaced a latent bug the audit
would otherwise have missed — three test address constants were 38 hex
characters instead of 40, and had been silently skipping because the suite
short-circuits without `DATABASE_URL`.

### ADR-060 — The protection does not depend on a language model (decided 2026-08-02, revisits ADR-009/020/041)

**Context**: the audit above ended on a question the code answered badly.
With `AGENT_PROVIDERS=live` and no `ANTHROPIC_API_KEY`, the worker **refused to
start at all** (ADR-020 fail-fast). Tracing what the model actually does made
that look indefensible: the LLM writes the `explanation` string and, in agent
mode, picks which chain to scan next. It does **not** decide the risk tier —
`classify_risk` does (ADR-058) — and it does not execute anything — KeeperHub
does. So the entire safety-critical path, *fetch approvals → classify →
revoke the dangerous ones*, never needed a model, yet a missing or expired API
key left a wallet completely unprotected. An Anthropic outage or a lapsed card
became a security incident.

**Decision**: keep the LLM as an upgrade to the writing, never a dependency of
the protection.

1. **`DeterministicThreatIntel`** (`adapters/deterministic.py`): a real
   `ThreatIntel` with no model behind it. Same real GoPlus data, same
   `classify_risk` tier, explanation composed from the signals that produced
   that tier (the reported behaviours, whether source is published, whether
   the allowance is unlimited, the contradictory-trust case). It states only
   what the data supports.
2. **`DeterministicAgentPolicy`**: walks every configured chain in order, then
   finishes. Without a model there is no basis to prioritise one chain, so it
   covers all of them and the journal says exactly that rather than implying a
   judgement nobody made. It never asks a clarification — there would be no
   model to interpret the answer, and the job would sit in `awaiting_input`
   forever (ADR-032).
3. **Selection is automatic** (`llm_is_configured`): Ollama needs no
   credential; the hosted backend needs its key. Missing key → deterministic
   adapters, plus one warning line naming precisely what degrades. The worker
   startup check now hard-requires only `KEEPERHUB_API_KEY`, because a worker
   that cannot revoke is genuinely useless for this agent.
4. **These are not the ADR-021 fakes and must never be confused with them.**
   `adapters/fake.py` fabricates approvals and returns invented transaction
   hashes; it exists for keyless e2e and is refused in production (ADR-059).
   `adapters/deterministic.py` touches the real chain, classifies real risk and
   drives real revocations — the *only* difference from the LLM path is prose
   quality. That is why it ships as a production adapter rather than a test
   double, and why its spend is honestly metered at zero LLM calls (ADR-038).

Proven end to end against live mainnet data with no API key present: real
GoPlus responses for two funded wallets, correct tiers, and the auto-revoke
gate correctly selecting nothing on verified spenders.

**Consequence**: the product now runs on a KeeperHub key alone. An LLM key
buys better explanations and smarter chain ordering — never the difference
between a protected and an unprotected wallet.

---

### ADR-061 — One frontend: Next.js `web/` replaces the Vue `frontend/` (decided 2026-08-02, revisits ADR-003/026/028/049)

**Context**: ADR-058 renamed the domain from "web research" to "approval
scanning" across the API. The Vue SPA in `frontend/` was never migrated: it
still posts `{keyword}` to `POST /api/searches` and renders `published_at` /
`date_confidence` on results that no longer carry those fields. It does not
merely look wrong — it cannot complete a single scan against the current
backend. Meanwhile the product needs a public page that explains what an
approval is and why an unrevoked one is dangerous, which the SPA never had.

**Decision**: one Next.js 16 application in `web/` serves both surfaces, and
the Vue app is retired rather than repaired.

1. **Two surfaces, one app.** `/` is the public page — what the product does,
   the classifier, the real Sepolia transaction. `/console` is the operator
   surface — sign in, launch a scan, watch findings arrive, read the revocation
   receipts. A visitor who has never heard of an approval and an operator
   watching a live revoke want different pages; splitting them lets each be
   honest about its job.
2. **Why replace and not port.** Porting means rewriting every view, store and
   test in `frontend/` against a domain that changed underneath them, to end up
   with a second toolchain to keep alive for a hackathon deadline. The Vue app
   carried no logic worth saving — the risk decision lives in the worker
   (ADR-058) and the audit trail in Postgres. What it did prove (SSE-first with
   polling fallback — ADR-026; the pinned wire shape — ADR-049) is carried over
   as behaviour, not code.
3. **The backend contract does not move.** `web/` speaks the same routes with
   the same shapes; no endpoint was added, removed or reshaped for it. The
   contract fixtures (ADR-049) stay the arbiter of the wire shape, so a
   frontend rewrite cannot quietly redefine the API.
4. **Truthful status rendering is a hard requirement.** `simulated` renders as
   *not yet revoked* and never borrows the language or colour of `revoked`
   (ADR-059). Only a finding with `revocation_status: "revoked"` and a
   `revocation_tx_hash` shows as neutralised, and it links to the block
   explorer so the claim is checkable without trusting the UI.
5. **No fabricated proof on the public page.** The template this page was built
   from shipped a customer-logo wall (IBM, Netflix, Stripe…) and three invented
   testimonials with stock portraits. All of it is removed. Everything the page
   asserts — the transaction hash, the gas figure, the classifier source — is
   sourced from `web/lib/proof.ts` and independently verifiable.
6. **Polling is unconditional; SSE is the latency win on top.** ADR-026 made
   polling the *fallback*, triggered when the stream fails. Building this
   surfaced why that is not enough: a buffering proxy does not fail. It accepts
   the connection, answers `200 text/event-stream`, and then delivers nothing.
   An error-triggered fallback never fires, and the view freezes on whatever
   the first fetch returned — observed here as a scan sitting at *Queued* for
   its whole run while the job had actually paused for input. The console now
   polls every 1.5s until the job is terminal and runs the stream alongside it,
   so silence costs latency instead of correctness.
7. **A nonce-based CSP forces dynamic rendering, and the failure is silent.**
   The nonce is minted per request in `proxy.ts`, but a prerendered page has
   its nonce baked in at build time. The two never match, so the browser
   blocks *every* script on that page: the markup renders, the header is
   present, `npm run build` is green, and nothing runs. On the public page
   that meant no hydration at all, and content that animates in stayed at
   `opacity: 0` — a blank hero. Next only opts a route out of static rendering
   when the route itself reads headers, which these do not, so `export const
   dynamic = 'force-dynamic'` is declared in the root layout. The alternative,
   relaxing `script-src` to `'unsafe-inline'`, was rejected: the console holds
   a live access token in memory, and that is the surface the policy exists
   for. `web/e2e/csp.spec.ts` now asserts that scripts actually execute, since
   asserting on the header is what missed this in the first place.
8. **The clarification path is reachable from a browser for the first time.**
   `FakeAgentPolicy` asked for clarification when the goal contained
   "ambiguous" (ADR-032). A dispatched goal is always `scan wallet <address>
   for risky token approvals`, and no EVM address can spell "ambiguous" — it is
   not hex — so that trigger could only ever fire from a unit test, and an
   existing test comment said as much. `ASK_SENTINEL`, a valid address, is now
   a second trigger, which is what lets the pause/answer/resume dialog be
   driven end to end.

**Consequence**: `frontend/` is deleted. The following rows of earlier ADRs are
superseded and are kept only as history — read them against this entry:

- **ADR-003** (Vue 3 + Vite) — replaced wholesale by Next.js 16 + React 19 +
  Tailwind v4.
- **ADR-012**, testing table, `frontend/` row — `vitest` + `@vue/test-utils` +
  `msw` becomes `vitest` over the shared contract fixtures (`web/lib/__tests__`)
  plus the Playwright journey. No component-level mocking layer: what is worth
  pinning on this surface is the wire shape and the real browser flow.
- **ADR-014**, image table, "Vue frontend" row — no longer `nginx:alpine` over
  static files. The console server-renders, so the runtime image is
  `node:22-alpine` running Next's standalone server, and it proxies `/api`
  itself. The nginx re-resolution rule in that ADR no longer applies to
  anything, because there is no nginx in the path.
- **ADR-015**, stage table, `frontend/` column — the lint stage is `tsc
  --noEmit` (no eslint config ships with the template), and the published image
  is `$CI_REGISTRY_IMAGE/web`.
- **ADR-054**, app security headers — previously added by nginx, now set by
  `web/proxy.ts` on every app response. The policy is *stricter* than the one it
  replaces: `script-src` is nonce-based with `strict-dynamic` rather than
  `'self'`, because the console holds a live access token in memory.
- **ADR-028**, Playwright specs — rewritten against `/console`; the suite moves
  to `web/e2e/` and drives the approval journey instead of the search timeline.
  Two suites are added alongside it: `csp.spec.ts` (the page's JavaScript
  actually executes under the policy) and `layout.spec.ts` (no horizontal
  scroll at four widths; the walkthrough advances and rewinds with scroll).

Two things the pivot left behind, found while verifying this work rather than
by reading the code, are fixed here as well:

- **`docker-compose.yml` never passed the onchain configuration to the worker.**
  `KEEPERHUB_API_URL`, `KEEPERHUB_API_KEY`, `KEEPERHUB_SIMULATE_ONLY`,
  `GOPLUS_API_KEY`, `AGENT_SCAN_CHAIN_IDS`, `AGENT_MAX_STEPS`,
  `AGENT_MAX_COST_USD` and `AGENT_MODEL_FALLBACKS` were absent from the
  `agent-worker` environment, while `TAVILY_API_KEY` from the old domain was
  still being passed. The containerized worker could therefore only ever run
  the keyless fakes; with `AGENT_PROVIDERS=live` it refused to boot. The
  fail-fast of ADR-020 was working correctly and pointing at the operator for
  a fault that was in compose.
- **The findings feed and the stack wall pushed the page into a horizontal
  scroll below 390px.** Fixed-width columns and an unbreakable component name
  set a min-content width wider than the viewport. `web/e2e/layout.spec.ts`
  pins "never scrolls sideways" at four widths.

Next serialises `rewrites()` at build time, so the API target is a Docker build
arg (`API_ORIGIN`) rather than a runtime variable — the one place this brick
breaks the "configuration comes exclusively from environment variables at run
time" rule of ADR-014, and it is recorded here rather than left to be
discovered.

---

### ADR-062 — E-mail verification at sign-up (decided 2026-08-03, extends ADR-008/020/057)

**Context**: registration took an address and a password, hashed the password
with argon2 and issued a session on the spot (ADR-008). Nothing ever checked
that the address existed or belonged to the person typing it. Two consequences.
The digest webhook and every future recovery path point at an address that was
never confirmed, so a typo silently sends someone else's scan results to a
stranger — or nowhere. And an account costs nothing to create in bulk, which
matters for a product whose free tier dispatches paid worker runs (ADR-017).

**Decision**: an account exists the moment it is registered, but it cannot be
signed into until a six-digit code mailed to its address is answered.

1. **`email_verified_at` on `users` is the gate.** `NULL` means no session may
   be issued. `LoginUser` returns a distinct `EmailNotVerified` — checked
   *after* the password, never before, so login cannot be used to discover
   which addresses are registered. The HTTP layer maps it to `403` with
   `code: "email_not_verified"`, and the console opens the code screen instead
   of claiming the password was wrong.
2. **Codes are stored hashed, like refresh tokens (ADR-008).** Six digits carry
   almost no entropy, so three other things carry the security: a 10-minute
   TTL, a hard cap of five attempts per code (past it the code is dead even if
   guessed correctly), and a per-account throttle on `/api/auth/verify`. That
   throttle is a **separate budget** from the login throttle of ADR-057 on its
   own key namespace — they guess at different secrets, and sharing one meant
   fumbling a code spent the attempts needed to sign in afterwards.
3. **One live code per account.** `EmailVerificationRepository::insert`
   supersedes any outstanding code in the same transaction. The alternative —
   resolving "the current code" by comparing timestamps — is ambiguous for two
   codes issued inside the same microsecond (`now_utc` truncates there), and
   resend is exactly the operation that would hit it.
4. **New port `EmailSender`, two adapters.** `ResendEmailSender` posts to
   Resend's HTTP API — chosen over SMTP only because `reqwest` is already a
   dependency and an SMTP client would add a TLS stack and a MIME builder for
   one six-digit message. `DevEmailSender` logs the code and records it.
   Another provider, or `lettre` over SMTP, is one more implementation.
5. **The development affordance is forced off in production**, the same rule
   ADR-059 applies to simulated revocations. With no `RESEND_API_KEY` the API
   returns the code in its own response so the console works without a mailbox;
   `AppConfig` computes `expose_verification_codes` as
   `resend_api_key.is_none() && !is_production`, and the gate is applied in one
   place (`AppState::exposed_code`) so no handler can leak one by accident.
   `RESEND_API_KEY` and `EMAIL_FROM` join `REQUIRED_IN_PRODUCTION` (ADR-020):
   without a mailer, registration creates accounts nobody can ever sign into,
   and refusing at boot beats discovering that from a user.
6. **Answering a code is a full sign-in.** It has to be — the alternative is
   asking for the password again immediately after proving the address. Both
   doors therefore go through one `SessionIssuer`, so token lifetimes and
   rotation families (ADR-056) cannot drift between them.
7. **Resend and register-for-an-existing-address reveal nothing.** `POST
   /api/auth/verify/resend` answers `202` identically for an unregistered
   address, an already-verified one, and a real send. Its response carries no
   `sent` field, because saying `sent: true` where nothing was sent would be a
   field that lies; the console's wording is conditional to match.
8. **Migration 0013 grandfathers existing accounts** (`UPDATE users SET
   email_verified_at = created_at`). Locking out people who registered before
   the rule existed would be a data-loss event for someone who did nothing
   wrong.

**Consequences**: registration is a two-step flow, and the browser suite pays
for it — every `register()` in `web/e2e/console.spec.ts` now answers a code.
The compose stack has no mail provider, so the code arrives in the register
response and the console pre-fills it, with a visible notice saying why. A
deployment that sets `APP_ENV=production` must now hold a verified Resend
sending domain before it will boot.

**Not done**: no per-login 2FA. This verifies that an address is reachable,
once. Requiring a code on every sign-in is a different feature with a different
cost, and passkeys (WebAuthn) would be the better answer to that question.
*(Revisited the next day by ADR-063, which does exactly that.)*

---

### ADR-063 — Two-factor sign-in and account recovery (decided 2026-08-03, revisits ADR-062)

**Context**: ADR-062 verified an address once, at sign-up, and then let the
password alone open every later session. Two gaps followed. A leaked password
was still a complete account takeover — for a product whose whole pitch is
"your approvals are someone else's problem once they are in", that is the wrong
default. And there was no recovery at all: forgetting the password meant losing
every watched wallet, with no path back that did not involve a database.

**Decision**: the emailed code becomes the second factor of *every* sign-in,
and the same mechanism, under a separate purpose, recovers a lost password.

1. **`POST /api/auth/login` answers 202, not 200.** It checks the password and
   mails a code; it issues no session. `LoginUser` no longer holds a
   `SessionIssuer` at all, so "a password cannot sign you in" is a
   compile-time property rather than a rule someone has to remember.
   `ConfirmEmailVerification` is the single place a session is minted.
2. **`CodePurpose` (`verify` | `reset`) is stored on every code**, and every
   lookup filters on it (migration 0014). Without it a code handed out for a
   sign-in would also authorise setting a new password. Supersession is
   per-purpose too: asking to reset must not kill a sign-in code in flight.
3. **`verify/resend` stays restricted to unverified accounts.** This is the one
   passwordless code issuer, and the restriction is load-bearing: if it served
   verified accounts, mailbox access alone would be enough to sign in and the
   password would stop mattering. Resending a *sign-in* code therefore means
   re-submitting the password, which the console does from state.
4. **A superseded code says so, and does not spend an attempt.** Previously the
   older of two emails read as "invalid", sending people hunting for a typo
   that was not there — and five such submissions burned the live code's whole
   attempt budget, locking someone out of an account they could otherwise
   open. `find_by_hash` recognises a code that was genuinely issued here, so
   `Superseded` is distinguishable from a guess. It reveals nothing: naming a
   past code is exactly as hard as naming the current one, and grants nothing.
5. **Recovery ends signed in.** By the last step the person has proved both of
   the things an ordinary sign-in asks for — possession of the mailbox (the
   code) and knowledge of the password (the one they just chose). Sending a
   second email to log in afterwards would be ceremony, not security.
6. **A reset revokes every existing session** (`delete_for_user`). The common
   reason to reach for this form is "someone else may be in my account"; a
   reset that leaves the intruder's refresh cookie alive fails at the one job
   it was reached for.
7. **The password length rule is checked before the code is looked up**, so a
   too-short password never burns the code needed for the retry.

**Consequences**: signing in is two steps, always. That is a real cost —
demoing the console now means fetching a code — and it is the reason the
development configuration returns codes in the API response (ADR-062 §5)
rather than being a convenience. The browser suite and `scripts/e2e-smoke.sh`
both answer a code per sign-in.

**Found while building this**: `delete_expired` on the code repository is a
*global* sweep, and the PostgreSQL integration tests run in parallel against
one database. A test that purged with a future cutoff deleted live codes
belonging to whatever else was running, failing an unrelated test. Fixed by
issuing an already-expired code (negative TTL) and sweeping at `now`.

---

### ADR-064 — One chain's outage does not sink the scan (decided 2026-08-03, extends ADR-030/058/059)

**Context**: `fetch_approvals` was called in a bare loop over the configured
chains, in all three orchestration paths (`run_scan`, the hand-rolled loop, and
the LangGraph graph). A single provider error — a GoPlus rate limit on Base,
say — raised straight out of the loop, so the job failed *and* threw away every
finding already collected from the chains that had answered. A wallet with a
live drainer approval on Ethereum reported nothing at all because Base was busy.

The same investigation turned up `"GoPlus error: None"` in the logs:
`payload.get("message", "unknown error")` returns `None` when the key exists
and is null, which is what GoPlus sends on several error codes. The default
only applies to a *missing* key.

**Decision**: a chain that cannot be scanned is a recorded gap, not a crash —
unless it is the only chain.

1. **New `AgentStepKind.DEGRADED`.** A failed chain becomes a journal step
   carrying the chain id and the provider's error. `kind` was already an open
   string on the wire (Rust `String`, zod `z.string()`, no SQL CHECK), so this
   needed no migration and no contract change — the console's existing
   "unknown kinds render neutrally" rule already handled it safely.
2. **All three paths continue to the next chain.** Workflow mode gained an
   optional `StepReporter` for exactly this; it is the only kind of step a
   workflow run ever writes, which is the point — a journal entry there means
   something went wrong, and an empty journal means it did not.
3. **If every attempted chain fails, the job fails.** This is the ADR-059 rule
   in a new costume: delivering an empty result set renders as *no dangerous
   approvals found*, which is a clean bill of health for a wallet nobody
   managed to look at. Partial coverage is reportable; zero coverage is not.
4. **The console says so above the fold.** A `degraded-notice` banner names the
   unreachable chains next to the tier counts, because the dangerous way to
   read that page is "0 dangerous" — a number that means nothing for a chain
   that was never queried. It is derived from the journal steps rather than a
   second field, so the two cannot drift.
5. **Failure messages carry their cause.** `no chain could be scanned` alone is
   unactionable in a log; each entry is `{chain}: {provider error}`. A single
   configured chain failing keeps the raw provider error as the job's error,
   unchanged from before.

**Consequences**: a partial scan is now a *success* with a visible gap, where
it used to be a total failure. That is the right trade for a monitoring
product — the Ethereum drainer gets reported while Base is down — but it does
mean "completed" no longer implies "complete", and the banner is what carries
that distinction. Two pre-existing tests asserted the old fail-fast timing and
were updated rather than deleted: the invariant they protect (nothing is
delivered when nothing was scanned) still holds, it just triggers later.

---

### ADR-065 — A revocation is refused unless the scanned wallet is the delegated one (decided 2026-08-04, extends ADR-058/059)

**Context**: found while switching the stack to live mainnet, before the first
real transaction. `POST /api/execute/contract-call` carries **no wallet field** —
KeeperHub executes as whatever wallet the API key is bound to, readable at
`GET /api/user` (`walletAddress`). The console, meanwhile, accepts *any* valid
EVM address as a scan target, and nothing anywhere compared the two.

So for any scan of an address other than the operator's own, agent mode would:
send a real `approve(spender, 0)` from the **operator's** wallet, burn real gas,
clear an allowance that was never granted (a no-op), get back a genuine
transaction hash, and render the finding as **`Revoked · tx 0x…`**. The scanned
wallet's draining approval would still be live, with a blockchain receipt
presented as proof it was gone.

That is the failure ADR-059 exists to prevent, made worse: the earlier version
was a dry run mislabelled as real, this one comes with a real transaction to
point at.

**Decision**: `ApprovalRevoker.revoke` takes the scanned `wallet_address`, and
an implementation must refuse unless it can execute *as* that wallet.

1. **The guard lives in the adapter**, not the use case. The adapter is the
   only thing that knows its execution identity; the port contract states the
   obligation ("revoke this finding **for this wallet**") and the KeeperHub
   adapter enforces it by comparing against `GET /api/user`, cached for the
   life of the key. Case-insensitive, because EIP-55 checksum casing is
   routine and a case-sensitive compare would refuse the operator's own wallet.
2. **Refusal is `NOT_ATTEMPTED`, never `FAILED`.** Nothing was tried, and no
   network call is made — the guard runs before the POST. `FAILED` would imply
   an attempt that went wrong, which invites a retry that can never succeed.
3. **Unknown is not permission.** If `GET /api/user` cannot be read, the guard
   cannot be evaluated and no revocation proceeds.
4. **The journal names the reason**: *"not attempted: this wallet is not
   delegated to KeeperHub, so the approval can only be revoked by its own
   owner."* On the most dangerous row in the table, an unexplained blank is
   nearly as bad as a wrong label.

**Consequences**: scanning is still open to any address — that is the product's
front door and it is read-only. Only *execution* is restricted, and only to the
wallet that granted the approvals in the first place, which is the only wallet
whose approvals `approve(spender, 0)` can possibly clear. A future multi-wallet
KeeperHub (several delegated wallets per key) turns the equality check into a
membership check; nothing else moves.

**Why this was not caught earlier**: every prior end-to-end run used
`AGENT_PROVIDERS=fake` or a Sepolia dry run against the operator's own wallet,
where scanned and delegated wallet coincide. The bug is invisible until someone
scans an address they do not control — which, for a public product, is the
first thing a stranger does.

---

### ADR-066 — What the live provider actually answers (decided 2026-08-04, extends ADR-058/064)

**Context**: the first scan of a wallet nobody had scanned before, on live
mainnet. Three things surfaced that no unit test could have predicted, because
they are facts about GoPlus's behaviour rather than about our code.

**Decision**:

1. **`code 2` ("partial data obtained") is transient, and is retried.**
   Confirmed live: the first request for an address GoPlus has not indexed
   returns `code 2`; a follow-up seconds later returns `code 1` with the full
   set. The adapter treated any non-1 code as a hard error, so an entire chain
   was discarded — on the *first* scan of any wallet, which is the scan every
   new user runs, and the one a demo runs. Now retried (3 attempts, linear
   backoff); other codes are not retried, because 2018/2029 are settled
   answers about the chain and retrying only slows every scan down.
2. **Partial data is never merged in as if complete.** If `code 2` persists,
   the chain is reported unscanned (ADR-064 marks it `degraded`) rather than
   delivering a half-built approval list as a finished sweep. An incomplete
   list rendered as a complete one is a coverage lie in the ADR-059 family.
3. **The scannable chain list is exactly Ethereum, BSC and Base.** Probed live
   across 17 chain ids: everything else answers `2018` ("Main chain does not
   exist") or `2029`. Arbitrum and Polygon were in `.env.example`'s suggested
   default, so the documented configuration would have reported two
   permanently unreachable chains on every scan. BSC was *missing* from it and
   is free coverage, so the default is now `1,56,8453`.

   **Sepolia (11155111) is not scannable either.** KeeperHub executes there
   happily — the first proof transaction was on Sepolia — but GoPlus holds no
   approval data for it, so a testnet wallet always scans empty. Execution and
   detection do not cover the same chains, and only their intersection
   (Ethereum, BSC, Base) is a working end-to-end product.
4. **An explanation describes the risk, never the action.** The DANGEROUS
   sentence ended `"Revoking automatically."` — written by the threat-intel
   port, which runs *before* anything is executed and is shared by both modes.
   In a report-only run it was rendered verbatim underneath the banner saying
   *nothing was revoked*: two contradictory claims on one screen, the more
   prominent one false. The sentence now ends with what the allowance means
   (*"It can move those tokens at any time without asking you again"*), and
   what happened is left to the revocation status beside it, which is the
   field that actually knows.

**Consequences**: point 4 is the one to remember. ADR-059 put the
revoked/simulated distinction in the status field, and that was enforced — but
free-text written by a different component, at a different time, slipped a
second claim about the same fact onto the same row. Any prose that asserts
what the system *did* has to come from the component that did it.

**How these were found**: by scanning a real wallet with real approvals on
mainnet, not by testing. The wallet used for every earlier live run was the
operator's own and had zero approvals, so the DANGEROUS path had never once
been rendered from live data.

---

### ADR-067 — A scan states which chains it covered (decided 2026-08-04, extends ADR-064/066)

**Context**: ADR-064 made a chain that *failed* visible. Nothing made a chain
that was *never attempted* visible. The approval source serves three chains
(ADR-066) out of the ~30 a wallet can hold ERC-20 approvals on, so the console
could show **0 dangerous approvals** for a wallet that has plenty — just not on
Ethereum, BSC or Base.

Verified on the wallet used for the mainnet demo: it holds four `Approval`
events on Arbitrum, two distinct tokens including USDC. The product had no way
to see them and, worse, no way to say it had not looked. "Clean" and "clean
where we looked" are different claims, and only the second one was ever true.

**Decision**: every run journals the chains it covered, not only the ones that
broke.

1. **Workflow mode emits a `scan` step per covered chain.** It previously
   emitted none, so its journal was empty and its coverage unknowable. Agent
   mode already did this as a side effect of the loop; now both modes carry
   the same evidence.
2. **The console states coverage next to the counts**: *"Covered Ethereum ·
   BNB Chain · Base · approvals on other networks were not examined."* It is
   derived from the journal steps, so it cannot drift from what actually ran.
3. **It is stated on every run, not only partial ones.** A notice that appears
   only when something went wrong trains people to read its absence as full
   coverage — which is exactly the wrong inference here.

**Consequences**: the honest ceiling of the product is now visible in the
product. That is a weaker-sounding page and a truer one; the alternative was a
green "0 dangerous" that a user could reasonably read as "my wallet is safe".

Widening the ceiling is a separate piece of work: GoPlus splits the problem
into *enumeration* (`token_approval_security`, three chains) and *spender
threat intel* (`approval_security`, verified working on Polygon, Arbitrum,
Optimism, Avalanche and Scroll). Classification is therefore already
chain-wide; only enumeration is missing, and `eth_getLogs` for `Approval`
topics returns it — confirmed against a public Arbitrum RPC. A second
`ApprovalSource` adapter doing that, composed per chain, is the designed
extension point and needs no change to the port.

---

### ADR-068 — The product is called Sever (decided 2026-08-04)

**Context**: "Approval Firewall" describes the category, not the product. It is
the kind of name three teams in the same hackathon could arrive at
independently, and it competes directly with the literal names already in this
space — Revoke.cash, Wallet Guard, Blockaid. A descriptive name cannot be
owned, and this one had already started to strain: the console is not a
firewall, it does not sit inline, and nothing is filtered.

**Decision**: the product is **Sever**.

1. **It names the action, not the category.** The one thing this software does
   that a scanner does not is cut a live permission. `approve(spender, 0)` is a
   severance.
2. **The mark already existed.** The logo has been a shield over a severed link
   since the first commit; the name now matches the drawing rather than
   arguing with it.
3. **Renamed: user-visible strings, package names, e-mail templates, the
   wordmark, metadata titles. Not renamed: the ADRs above.** Those record what
   was decided and when, under the name in use at the time. Rewriting them
   would be exactly the history-editing this document forbids, and the rename
   is itself a decision that belongs in the log rather than hidden by it.
4. **`web/components/firewall/` keeps its name for now.** It is an internal
   path, and renaming it churns every import in the brick for no behavioural
   gain. Worth doing in a quiet moment, not on the way to a deadline.

**Known cost**: "sever" and "server" are one keystroke apart, which matters in
a technical audience. Accepted because the alternative candidates each traded
that for something worse — a longer name, a grimmer one, or one that described
the problem rather than the cure.

---

### ADR-069 — The hero shows a photograph of the product, not a drawing of it (decided 2026-08-04)

**Context**: the landing page opened with `DashboardMock` — a hand-built
replica of the console, ~470 lines of JSX with invented KPIs, invented ledger
rows and invented chain distributions. It carried a "sample data" caption, and
earlier passes had already trimmed its worst claims (a "+14 this week" growth
figure, chains the scanner cannot read).

That was treating the symptom. The deeper problem is that a fabricated panel is
strange to ship *at all* when the product it depicts exists, works, and has
just been run against mainnet. Every invented number is a small liability, the
caption is doing work the artefact should not need, and nobody has to trust a
drawing when they could be looking at the thing.

**Decision**: the hero is a screenshot of the real console, captured by
`web/e2e/capture-console.spec.ts` against a live mainnet scan.

1. **The capture asserts before it saves.** It waits for `completed`, requires
   the first finding to be `data-tier="dangerous"`, and requires the coverage
   notice to be present. A screenshot that missed the point of the page fails
   the run rather than landing in `public/`.
2. **It has its own Playwright config.** The suite's global setup refuses a
   live-provider stack, correctly — the capture needs exactly that, so the
   exception is a separate config rather than a hole in the guard.
3. **`DashboardMock` is deleted, not disabled.** There is now no file in the
   repository whose job is to look like output.
4. **Regenerate rather than edit.** The image is a build artefact of a real
   run. Touching it up by hand would quietly turn it back into a mock.

**Consequences**: the hero is a raster image, so it does not adapt to theme or
viewport the way live DOM did, and it needs re-capturing when the console
changes shape. That is the cost of the page showing something true. The
screenshot also happens to demonstrate three of this document's honesty rules
at once — the coverage notice (ADR-067), *allowance still spendable* on a
finding that was not revoked (ADR-059), and an explanation that describes risk
rather than claiming an action (ADR-066) — which no drawing would have.

---

### ADR-070 — A still-live allowance always offers a way out (decided 2026-08-04, follows ADR-065)

**Context**: found by scanning a stranger's wallet, which is the thing the
product invites everybody to do. `0x57B97f…Ab2db` came back with four dangerous
approvals, two of them to a spender GoPlus flags `phishing_activities` and
`stealing_attack`, holding live allowances on that person's USDC on two chains.

The console showed them, labelled them *allowance still spendable* — and then
offered nothing. No button, no link, no next step. ADR-065 means Sever can only
execute for the single wallet delegated to KeeperHub, and ADR-058 means a WATCH
finding is never auto-revoked even for that wallet. So for almost every visitor,
almost every row, the product named a danger and abandoned them at it.

That is a worse failure than it looks. A security tool that reports a live
drainer approval and provides no remedy has converted a person who did not know
into a person who knows and is stuck.

**Decision**: every finding whose allowance is still live links out to
`revoke.cash`, scoped to the scanned wallet and the right chain.

1. **Shown for every still-live state, not only DANGEROUS.** WATCH is
   deliberately never auto-revoked; those rows need the link most, because
   nothing else will ever clear them.
2. **It is labelled as leaving.** *"Revoke it yourself ↗"*, opening in a new
   tab. revoke.cash will ask to connect a wallet — the exact step this product
   refuses to ask for — so it is not dressed up as one of our own controls.
3. **Never rendered for a chain we do not map**, because a revoke link that
   lands on the wrong network is worse than no link.
4. **Absent once genuinely revoked.** The link is the answer to "still live";
   when that stops being true the row stops asking for anything.

**Consequences**: the product sends people to a competitor, and that is the
correct trade. Sever's claim is that it *finds* what a wallet cannot show you;
pretending it can also fix everybody's wallet would require either a wallet
connection it has promised not to ask for, or a lie about what KeeperHub can
execute. Linking out keeps both promises and still leaves the visitor better
off than they arrived.

### ADR-071 — A second mail provider, because Resend cannot reach strangers (decided 2026-08-05, follows ADR-062)

**Context**: with `RESEND_API_KEY` configured and the default
`onboarding@resend.dev` sender, registering any address other than the Resend
account owner's fails:

```
403 validation_error: You can only send testing emails to your own email
address (…). To send emails to other recipients, please verify a domain.
```

Every transactional provider gates sending on a proven identity — that is
anti-spam, not a Resend quirk. But the *shape* of the proof differs, and it
decides whether the product works before a domain is bought:

- **Resend** verifies a **domain**. Until one is registered and its DNS records
  are in place, delivery is limited to the account owner.
- **Brevo** verifies a **single sender address** — a Gmail is enough — and then
  delivers to anyone.

ADR-062 was written assuming a mailer existed; it never asked *who that mailer
is allowed to write to*. Sign-up, per-login codes (ADR-063) and password reset
all ride on the same transport, so all three were unusable by anyone but the
operator. A product whose sign-up only works for its author is not a product.

**Decision**: add `BrevoEmailSender` as a second implementation of the existing
`EmailSender` port, and make the production check provider-agnostic.

1. **Brevo wins when both keys are set.** It is the one that can reach an
   arbitrary recipient without a domain; a deployment that has configured both
   has no reason to prefer the more restricted transport.
2. **Production requires *a* provider, not a named one.** `REQUIRED_IN_PRODUCTION`
   listed `RESEND_API_KEY` literally, so adding a second provider could not
   satisfy the boot check. It is now "`BREVO_API_KEY` or `RESEND_API_KEY`", and
   the error names the choice.
3. **`EMAIL_FROM` is split for Brevo.** The variable holds a mail header
   (`Sever <a@b.dev>`), which Resend takes verbatim and Brevo rejects as
   malformed — it wants `{name, email}`. `split_from` parses both that form and
   a bare address, and is unit-tested, because a sender that fails to parse
   fails *every* send while the configuration looks correct.
4. **Message bodies are shared.** Subject, text and HTML come from the same
   helpers as ADR-062, so the two providers cannot drift into sending
   different mail.

**Consequences**: sign-ups work for anyone the day a Brevo key is set, with no
purchase. The cost is deliverability and appearance — mail sent from a Gmail
sender lands in spam more often than mail from a verified domain, and it reads
as less legitimate. That is a real downgrade, and the reason Resend is kept
rather than replaced: verifying a domain later is a config change, not a code
change, and the port makes a third provider one more file.

---

## 4. API contracts (summary)

### Public (Next.js → Rust)

| Method | Route | Description |
|---|---|---|
| POST | `/api/auth/register` | Account creation → mails a verification code; issues **no** session (ADR-062) |
| POST | `/api/auth/verify` | `{email, code}` → access token + refresh cookie. The only route to a session (ADR-062/063) |
| POST | `/api/auth/verify/resend` | `{email}` → 202, identical whether or not the address needs a code; unverified accounts only (ADR-062/063) |
| POST | `/api/auth/login` | Password check → **202** and a mailed code, never a session (ADR-063) |
| POST | `/api/auth/password/forgot` | `{email}` → 202, identical whether or not the address has an account (ADR-063) |
| POST | `/api/auth/password/reset` | `{email, code, password}` → new password, every other session revoked, signed in (ADR-063) |
| POST | `/api/auth/refresh` | Rotates the refresh cookie → new access token |
| POST | `/api/auth/logout` | Revokes the refresh token, clears the cookie |
| POST | `/api/searches` | Launches a scan `{wallet_address, mode?}` → `{job_id}` (`mode`: `workflow` default, or `agent` — ADR-030) |
| GET | `/api/searches` | List of the user's scans |
| GET | `/api/searches/{id}` | Status + findings sorted most-dangerous-first (ADR-058) + agent journal `steps` (ADR-030) + `question`/`answer` (ADR-032) |
| POST | `/api/searches/{id}/answer` | Answers the agent's clarification `{answer}` → job resumes (ADR-032; 409 unless `awaiting_input`) |
| GET | `/api/searches/{id}/events` | SSE stream of the same payload, one `update` event per change, closes on terminal status (ADR-026) |
| POST | `/api/recurring` | Saves a recurring scan `{wallet_address, mode?, interval_minutes, webhook_url?}` (ADR-033/036) |
| GET | `/api/recurring` | Lists the user's recurring scans |
| DELETE | `/api/recurring/{id}` | Deletes a recurring scan (run history is kept) |

All `/api/*` routes can answer `429` (per-IP rate limit; `POST /api/searches`
also enforces the per-user daily quota — ADR-017; `POST /api/auth/login` also
enforces a per-account throttle — ADR-057).

### Internal (Rust → FastAPI, shared token)

| Method | Route | Description |
|---|---|---|
| POST | `/tasks` | `{job_id, wallet_address, mode, clarification?, recurring, seen_approval_keys}` → Celery enqueue |

### Outbound (Rust → the user's systems)

| Method | Route | Description |
|---|---|---|
| POST | *the saved `webhook_url`* | Digest of a recurring run with news `{recurring_search_id, job_id, wallet_address, new_count, new_results[]}`, each entry `{token_symbol, spender_address, tier, revocation_status}` (ADR-036/058, fixture `contracts/digest-webhook.json`). Dedup on `job_id` (at-least-once). Signed `X-Signature-256: sha256=<HMAC-SHA256 of the body>` when `DIGEST_SIGNING_SECRET` is set (ADR-047) |

### Internal (worker → Rust, shared token)

| Method | Route | Description |
|---|---|---|
| POST | `/internal/jobs/{id}/started` | Worker picked the job up → status `running` (ADR-016) |
| POST | `/internal/jobs/{id}/results` | Delivers findings `[{chain_id, token_address, token_symbol, spender_address, spender_name, approved_amount, tier, malicious_behavior, explanation, is_new, revocation_status, revocation_tx_hash, raw}]` (ADR-058) |
| POST | `/internal/jobs/{id}/steps` | Records one agent-loop decision `{seq, kind, detail, reason, new_hits}` (ADR-030, idempotent on seq) |
| POST | `/internal/jobs/{id}/question` | The agent asks the user `{question}` → status `awaiting_input` (ADR-032) |
| POST | `/internal/jobs/{id}/usage` | One task attempt's spend `{llm_calls, llm_input_tokens, llm_output_tokens, search_calls, cost_usd}` — accumulated (ADR-038) |
| POST | `/internal/jobs/{id}/failure` | Reports failure `{error}` |

---

## 5. Possible evolutions (out of the boilerplate's scope)

- Migrating the VPS deployment (ADR-015) to Kubernetes or a PaaS if scaling
  needs arise — the images being identical, only the orchestrator changes.
- Upgrade the SSE change-detection (ADR-026) from per-connection DB polling to
  Postgres LISTEN/NOTIFY or Redis pub/sub if connection counts grow.
- ~~Multiple search providers with aggregation/deduplication.~~ Done — ADR-051
  (`AggregatingSearchProvider`, RRF fusion, Tavily + keyless DuckDuckGo).
- An e-mail `DigestSender` adapter (SMTP/SES) behind the ADR-036 port, for
  forks that prefer inboxes over webhooks.
- **Generated API client from the OpenAPI schema.** The `openapi.json` already
  exists (ADR-049 amendment: `utoipa`, served at `/api/docs`) but is used only
  as **documentation** — the contract is pinned by fixtures + zod, proportionate
  while the boilerplate is a **monorepo** (one CI validates every side
  atomically). If a fork splits the bricks into **separate repos**, promote the
  schema to the source of truth: publish a versioned `openapi.json` as the
  release artifact and **generate** the TS client (and Python models) from it
  (`openapi-typescript` / `datamodel-code-generator`) — the published schema is
  then the inter-repo decoupling mechanism the co-located fixtures can no longer
  be. Independent *deployment* is already supported (ADR-006); this is only
  needed for independent *repositories/teams*.
- **Anthropic prompt caching — measured and deferred (2026-07-26).** The stable
  prefix of each live LLM call (the structured-output tool schema + the prompt
  instructions) is 733–928 tokens on `claude-opus-4-8` — below its 1024-token
  cache minimum, so a `cache_control` breakpoint would silently no-op
  (`cache_creation_input_tokens: 0`). Measured with `count_tokens`; the tool
  schema (ADR-043) is the bulk (~630–763 tokens), the instructions ~100–180.
  The prefix clears the **512-token** minimum on Opus 5, and the batched
  enrichment (ADR-042) reuses one prefix across a run — so revisit this if the
  default model moves to Opus 5 (or a lower cache minimum ships).
