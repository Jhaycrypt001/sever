# Approval Firewall

**An agent that finds the token approvals draining your wallet and revokes them
onchain, through [KeeperHub](https://keeperhub.io).**

[![Rust](https://img.shields.io/badge/Rust-stable-B7410E?logo=rust)](backend/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](agent/)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)](web/)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![ADRs](https://img.shields.io/badge/ADRs-61-8A2BE2)](docs/ARCHITECTURE.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Every token approval you have ever signed is still live. Most drains are not
a new exploit — they are an old `approve()` being called months later, by a
contract you forgot you trusted. Nothing in a wallet tells you which of those
are dangerous, and revoking one by hand costs gas and attention you will not
spend.

Approval Firewall scans a wallet's outstanding ERC-20 approvals, classifies
every spender, and **auto-revokes the dangerous ones as real onchain
transactions**. KeeperHub sponsors the gas, so the protected wallet needs no
funding and never signs anything.

## Proof it executes

A real revocation, broadcast and confirmed on Sepolia:

**[`0xa3e2b054752adda3aa9696a6d5460ac40c9670e34044da276b62ee10d9822c28`](https://sepolia.etherscan.io/tx/0xa3e2b054752adda3aa9696a6d5460ac40c9670e34044da276b62ee10d9822c28)**
— 75,255 gas, 7.9 s from decision to confirmation, gas sponsored by KeeperHub.

## The part that matters: an LLM cannot authorize a transaction

The decision to revoke is made by
[`classify_risk`](agent/src/aiagent/domain/models.py) — an ordinary, total
function over verified provider signals. It reads flags from
[GoPlus Security](https://gopluslabs.io) and returns one of three tiers:

| Tier | Condition | What happens |
|---|---|---|
| `DANGEROUS` | flagged malicious, and not on the provider's trust list | auto-revoked through KeeperHub |
| `WATCH` | unverified contract (no published source), or a contradictory signal | surfaced, never touched |
| `SAFE` | verified, no malicious signal | left alone |

A language model is allowed to write the `explanation` sentence and, in agent
mode, to choose which chain to look at next. It is **not** in the path that
decides what gets revoked, and the product runs correctly with no model key at
all ([ADR-060](docs/ARCHITECTURE.md)) — a lapsed API key degrades the prose,
never the protection.

Three further rules the code enforces rather than promises:

- **A dry run is never reported as a revocation.** `simulated` is a distinct
  status from `revoked` end to end, and the console renders it as *still live*.
  A production deployment refuses to boot in simulate-only or fake-provider
  mode ([ADR-059](docs/ARCHITECTURE.md)).
- **Nothing is revoked on a shrug.** An unreadable or missing signal produces
  `WATCH`, never `DANGEROUS` — revocation requires a positive, recognized
  malicious signal.
- **A trust-listed spender is never auto-revoked**, even when another signal
  disagrees. Auto-revoking a router someone actively uses is its own incident.

## How it runs

```
Next.js console
  │  POST /api/searches {wallet_address}          (JWT)
  ▼
Axum (Rust) ── persists the job, dispatches it
  ▼
Celery worker ── LangGraph agent
      1. fetch approvals + threat intel   (GoPlus)
      2. classify_risk                    (deterministic)
      3. revoke the DANGEROUS ones        (KeeperHub, real tx)
      4. sort most-dangerous-first
  ▼
POST /internal/jobs/{id}/results ──▶ Axum ── persists the audit trail
  ▲
Console (SSE + polling) ─────────────┘
```

Two modes over the same plumbing: **report-only**, a read-only inventory that
never writes to a chain, and **auto-revoke**, the agent loop that triages and
executes. A watched wallet is re-scanned on a schedule, alerts only on
approvals it has not seen before, and can push a signed digest webhook.

## Stack

| Brick | Tech |
|---|---|
| `backend/` | Rust, Axum, sqlx — API, accounts, job orchestration (hexagonal) |
| `agent/` | Python, Celery + LangGraph — scanning worker (hexagonal), FastAPI micro-API |
| `web/` | Next.js 16, React 19, Tailwind v4 — public page + operator console |
| Onchain | KeeperHub (execution, sponsored gas), GoPlus Security (approvals + threat intel) |
| Infra | PostgreSQL 16, Redis 7, Docker, Caddy, GitHub Actions |

## Quick start

Prerequisites: Docker, Rust, [uv](https://docs.astral.sh/uv), Node 22.
The only key the product actually requires is `KEEPERHUB_API_KEY`.

```sh
cp .env.example .env          # fill in KEEPERHUB_API_KEY

docker compose up -d          # infra only: PostgreSQL + Redis

# Terminal 1 — Rust backend (http://localhost:8000)
cd backend && cargo run --bin backend

# Terminal 2 — agent micro-API (http://localhost:8001)
cd agent && uv sync && uv run uvicorn aiagent.adapters.api.app:app --port 8001

# Terminal 3 — Celery worker
cd agent && uv run celery -A aiagent.celery_app worker --loglevel=info

# Terminal 4 — console (http://localhost:3000/console)
cd web && npm install && npm run dev
```

Or the fully containerized stack (what CI builds):

```sh
docker compose --profile full up --build   # console on http://localhost:8080
```

No keys to hand? `AGENT_PROVIDERS=fake` runs the whole journey on
deterministic providers — real pipeline, invented approvals, refused in
production.

## Tests

```sh
cd backend && cargo test        # domain + use cases with port fakes; +Postgres when DATABASE_URL is set
cd agent && uv run pytest       # domain + use cases, Celery in eager mode
cd web && npm test              # the wire contract, against the shared fixtures
cd web && npm run test:e2e      # Playwright journey against the compose stack (keyless)
```

No test calls a paid service; live provider tests are opt-in behind
`RUN_LIVE_TESTS=1`.

## Repository layout

```
backend/src/domain/             # entities + ports (traits) — no infrastructure deps
backend/src/application/        # use cases, unit-tested with fakes
backend/src/adapters/           # http (axum), persistence (sqlx), auth, dispatch
agent/src/aiagent/domain/       # approvals, classify_risk, tiers + ports (Protocols)
agent/src/aiagent/application/  # run_scan / run_agent_scan / execute_revocations
agent/src/aiagent/adapters/     # goplus, keeperhub, llm, deterministic, sink, api
web/app/                        # / (public page) and /console (operator surface)
web/lib/api.ts                  # zod schemas — the wire contract, validated at runtime
contracts/                      # golden fixtures asserted on both sides of every boundary
docs/                           # ARCHITECTURE.md (61 ADRs), COMMANDS.md, diagrams/
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — every technical decision
  (ADR-001 → ADR-061) with the rejected alternatives, kept in sync with the
  code. Start at **ADR-058** (the product), **ADR-059** (the security audit of
  the execution path) and **ADR-060** (why the protection does not depend on a
  model).
- [docs/COMMANDS.md](docs/COMMANDS.md) — every dev/test/deploy command.
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) — traces, logs and metrics.
- [contracts/README.md](contracts/README.md) — the cross-language contracts.
- [ROADMAP.md](ROADMAP.md) — what is next.

## License

MIT — see [LICENSE](LICENSE).

---

Built on a hexagonal AI-agent boilerplate; the pivot to onchain approval
scanning is recorded in ADR-058 onward.
