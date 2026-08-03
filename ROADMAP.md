# ROADMAP — technical roadmap and ideas

Deliberate scope cuts, hardening steps, and ideas for the boilerplate, ordered
by risk. The ports are in place — each item is an adapter/use-case cycle away.
Manual setup and deployment steps live in [SETUP.md](SETUP.md).

## P1 — Core reliability (before real usage)

- [x] **One frontend: Next.js `web/` (ADR-061, replaces ADR-003)** — done: the
      Vue SPA still spoke the pre-ADR-058 API (`keyword`, `published_at`) and
      could not complete a scan, so it was deleted rather than ported. `web/`
      serves the public page at `/` and the operator console at `/console`,
      with the security headers moved from nginx into `web/proxy.ts` (now a
      nonce-based CSP, stricter than the policy it replaced).
- [x] **The onchain configuration reaches the containerized worker (ADR-061)**
      — done: `docker-compose.yml` never passed `KEEPERHUB_*`, `GOPLUS_API_KEY`
      or `AGENT_SCAN_CHAIN_IDS` to `agent-worker`, and still passed
      `TAVILY_API_KEY` from the old domain, so the full profile could only run
      the keyless fakes.
- [x] **The public page's JavaScript runs under the CSP (ADR-061)** — done:
      a per-request nonce cannot match a prerendered page, so every script was
      blocked and the page shipped as inert markup. All routes render per
      request; `web/e2e/csp.spec.ts` asserts execution, not configuration.
- [x] **Live updates converge even behind a buffering proxy (ADR-061)** — done:
      polling is unconditional and SSE runs alongside it. ADR-026 made polling
      the error-triggered fallback, which never fires when a proxy answers 200
      and then delivers nothing — the console froze at `pending` for a whole
      run. Silence is now treated as failure.
- [x] **PostgreSQL adapter** (sqlx, ADR-007) — done. PostgreSQL whenever
      `DATABASE_URL` is set (migrations at startup), in-memory fallback
      otherwise; integration tests against compose locally / GitLab service in CI.
- [x] **Job lifecycle robustness (ADR-016)** — done: `running` transition,
      backend reaper (`JOB_TIMEOUT_MINUTES`), Celery retries with backoff,
      idempotent end to end.
- [x] **Rate limiting + quotas (ADR-017)** — done: per-user daily search quota
      (`DAILY_SEARCH_QUOTA`), per-IP fixed-window limits on auth and API routes
      (`RATE_LIMIT_AUTH_PER_MINUTE`, `RATE_LIMIT_API_PER_MINUTE`). Per-account
      login throttle (ADR-057, `LOGIN_MAX_ATTEMPTS_PER_MINUTE`) caps IP-rotating
      credential-stuffing, plus an append-only security audit log
      (`security_events`, migration 0010; failed/throttled logins, refresh reuse,
      quota hits) purged after `SECURITY_EVENT_RETENTION_DAYS`.
- [x] **Refresh tokens (ADR-008)** — done: single-use rotation on `/refresh`,
      SHA-256-hashed storage (migration 0002), HttpOnly cookie scoped to
      `/api/auth`, revocation on `/logout`, expired-token purge by the reaper.
      Reuse detection + family revocation (ADR-056, migration 0009): replaying a
      consumed token revokes the whole login lineage. Frontend: silent session
      restore on reload + refresh-and-retry on 401 (`withAuth`), redirect to
      login when the session is gone.

## P2 — Operability

- [x] **End-to-end correlation (ADR-018)** — done: `X-Request-Id` middleware on
      the Rust API, `job_id` propagated Rust → FastAPI → Celery → callbacks,
      `LOG_FORMAT=json` structured logs on all three server processes
      (enabled in `deploy/docker-compose.prod.yml`).
- [x] **Security hygiene in CI (ADR-015 amendment)** — done: `audit` stage with
      `cargo audit`, `pip-audit`, `npm audit`, gitleaks; runs on the weekly
      schedule only (creation of the schedule: SETUP.md §3).

## P2.5 — Agentic capabilities (ADR-030 follow-ups)

- [x] **Agentic loop + live decision journal (ADR-030)** — done: `mode=agent`
      end to end, `AgentPolicy`/`StepReporter` ports, step budget
      (`AGENT_MAX_STEPS`), `agent_steps` journal streamed over SSE, two demo
      blocks in the frontend.
- [x] ~~**Result self-critique (ADR-031)**~~ — superseded by ADR-058: the
      `ResultCritic`/`critique` step had no equivalent once the domain pivoted
      to approval scanning (a fetched approval has no "off-topic" judgment to
      make); dropped rather than ported.
- [x] **Recurring searches with memory (ADR-033)** — done: saved searches
      re-run by the backend scheduler tick (Celery beat rejected — see the
      ADR), memory of prior findings (`seen_approval_keys` since the ADR-058
      pivot; `seen_urls` originally), `is_new` flags end to end, and a
      `report` journal step with the delta verdict.
- [x] **Digest webhooks (ADR-036)** — done: optional `webhook_url` per
      recurring search; runs with new results POST a digest (best-effort,
      shape pinned by `contracts/digest-webhook.json`). An e-mail sender is
      one more adapter behind the same `DigestSender` port.
- [x] **Human-in-the-loop clarification (ADR-032)** — done: the policy can ask
      one question (`awaiting_input` status, reaper-exempt), the answer
      re-dispatches the job with the clarification and a fresh journal.

## P3 — Agent product quality

- [x] **Date cascade stage 2 (ADR-035)** — done: `PageDateFetcher` port reads
      JSON-LD `datePublished` / OpenGraph `article:published_time` before the
      LLM fallback — `high` confidence, bounded fetch, silent degradation.
- [x] **URL normalization + deduplication (ADR-034)** — done: canonical URLs
      (tracking params, fragments, ports, param order) used for workflow and
      loop deduplication and for the ADR-033 memory matching; displayed URLs
      stay original.
- [x] **Opt-in live provider tests (ADR-012)** — done:
      `agent/tests/test_live_providers.py`, skipped unless `RUN_LIVE_TESTS=1`
      (never in CI). One test per paid adapter (Tavily search, Claude
      enricher/policy/critic) to catch provider drift that defensive parsing
      would degrade silently; validated once for real on 2026-07-17.

## P4 — Comfort (later)

- [x] **E2E smoke test on the full compose stack in CI (ADR-021)** — done:
      deterministic fake providers (`AGENT_PROVIDERS=fake`, keyless),
      `scripts/e2e-smoke.sh` through nginx, `e2e` job in GitHub Actions and
      the GitLab mirror.
- [x] **Browser-level e2e tests (Playwright, ADR-028)** — done: real Chromium
      journeys (register → search → timeline, re-login → history) against the
      same fake-provider stack, run by both CIs' `e2e` jobs.
- [x] **Opt-in OpenTelemetry observability (ADR-029/050)** — done: gated on
      `OTEL_EXPORTER_OTLP_ENDPOINT`, W3C context propagated backend → FastAPI
      → Celery → callbacks. All three pillars behind `--profile observability`:
      traces (Jaeger) with per-call LLM spans, structured logs carrying the
      `trace_id`, and metrics (OTel Collector → Prometheus → Grafana — LLM
      latency/tokens/cost, HTTP RED).
- [x] **Dependency freshness without a platform bot (ADR-022)** — done:
      `scripts/deps-report.sh` (native tools) run weekly by both CIs, plus an
      inert portable `renovate.json` for forks that want automated update PRs
      (connect the Mend app on GitHub, or a scheduled renovate container job
      on GitLab/self-hosted, to activate it).
- [x] **Live job updates over SSE (ADR-026)** — done: `GET
      /api/searches/{id}/events` (DB-poll stream, closes on terminal status),
      fetch-streaming client with automatic polling fallback.
- [x] **Code coverage reporting in CI (ADR-023)** — done: cargo llvm-cov /
      pytest-cov / vitest v8 in the test jobs, Codecov on GitHub (informational,
      per-brick flags), native `coverage:` regex on the GitLab mirror.
- [x] **Pre-commit hooks (lefthook, ADR-022 amendment)** — done: fast
      format/lint per brick + gitleaks staged scan; `lefthook install` to opt in.
- [x] **Graceful shutdown of the backend (ADR-024)** — done: SIGTERM/SIGINT
      drain via `with_graceful_shutdown`.
- [x] **Cross-language contract fixtures (ADR-025)** — done: `contracts/`
      golden files asserted by both the Rust and Python suites.
- [x] **Trivy image scanning (ADR-015 amendment)** — done: weekly HIGH/CRITICAL
      CVE scan of the three published images in both CIs.
- [x] **Distributed per-IP rate limiting (ADR-037, revisits ADR-017)** — done
      as an opt-in: `RATE_LIMIT_REDIS_URL` switches the middleware to a
      Redis-shared fixed window (fail-open on Redis outages); unset keeps the
      in-memory limiter. Rate limiting at the reverse proxy remains the
      zero-code alternative for fleets behind a shared proxy tier.
