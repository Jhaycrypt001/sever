# Deploying Sever to Railway

Six services in one Railway project (ADR-073). Only the console is public; the
API, the agent and the data stores stay on the private network.

```
            internet
               |
          [ web ]  <-- the only public domain
               |  proxies /api/* over the private network
          [ backend ] --- [ postgres ]
               |     \
               |      `-- [ agent-api ] --,
               |                          |-- [ redis ]
               `--------- [ agent-worker ]'
```

The console proxies `/api/*` to the backend itself rather than the browser
calling it cross-origin, so the refresh cookie stays same-origin (ADR-008).
That is why the backend needs no public domain.

## Before you start

- **Turn off the Brevo IP allowlist.** Railway's egress address is not your home
  address and is not stable. Leave the restriction on and every verification
  e-mail fails, which means nobody can finish signing up. Brevo → SMTP & API →
  API keys → remove the IP restriction.
- Have the KeeperHub API key and the Brevo API key to hand. They go into the
  Railway dashboard, never into the repository.
- Generated secrets (JWT, internal token, Redis password) are in the file this
  runbook was prepared alongside — see the note at the end.

## Three rules that decide whether this works

1. **Bind to every interface, and tell Railway the port.** The private network
   is dual-stack (`IPv4 & IPv6`), but the health check arrives over IPv4, so a
   listener that answers only IPv6 fails it while the application log looks
   perfectly healthy. Rust's `[::]` accepts IPv4 too and is fine; uvicorn's
   `--host ::` does not, so the agent API binds `0.0.0.0` instead. Railway also
   guesses the port unless told, so `backend` and `agent-api` each set a `PORT`
   variable matching what they listen on — 8000 and 8001.
2. **Redis must be Redis Stack.** `agent/src/aiagent/tasks.py` calls
   `checkpointer.setup()`, which issues `FT.CREATE` — a RediSearch command. The
   managed Redis plugin does not have the module and the worker will crash on
   its first job. Deploy the image below instead.
3. **`API_ORIGIN` is a build argument.** Next serialises rewrites into the build
   output, so the value must exist when the image is built. Railway exposes
   service variables to the build, which is why setting it as a normal variable
   works — but it must be set *before* the first successful build, or the
   console will proxy to `localhost` and every API call will fail.

## 1. Project and databases

1. New Project → **Deploy from GitHub repo** → `Jhaycrypt001/sever`.
   Railway will try to build the repo root; that first attempt is expected to
   fail and is discarded in step 2.
2. **+ New → Database → PostgreSQL.** Name it `Postgres`. Nothing else to do —
   the backend runs its own migrations on boot (`backend/src/main.rs:132`).
3. **+ New → Empty Service**, name it `redis`. Settings → Source → **Docker
   Image**: `redis/redis-stack-server:7.4.0-v3`. Then add one variable:

   ```
   REDIS_ARGS=--bind :: --requirepass PASTE_REDIS_PASSWORD
   ```

   Redis on a private network is not reachable from the internet, but an
   unauthenticated data store inside a security product is a bad look if anyone
   checks. The password costs one variable.

## 2. The four application services

For each: **+ New → GitHub Repo → `sever`**, then Settings → set **Root
Directory** and **Config-as-code path** exactly as below. The config files pin
the builder, the start command and the health check, so there is nothing else
to configure per service.

| Service name | Root directory | Config-as-code path | Public domain |
|---|---|---|---|
| `backend` | `/backend` | `backend/railway.json` | no |
| `agent-api` | `/agent` | `agent/railway.api.json` | no |
| `agent-worker` | `/agent` | `agent/railway.worker.json` | no |
| `web` | `/web` | `web/railway.json` | **yes** |

Service names matter: they become the private hostnames
(`backend.railway.internal` and so on) that the variables below refer to.

Only `web` gets **Settings → Networking → Generate Domain**.

## 3. Variables

Paste each block into that service's Variables tab using the raw editor. Replace
every `PASTE_` placeholder.

### backend

```
APP_ENV=production
BIND_ADDR=[::]:8000
PORT=8000
DATABASE_URL=${{Postgres.DATABASE_URL}}
AGENT_API_URL=http://agent-api.railway.internal:8001
INTERNAL_API_TOKEN=PASTE_INTERNAL_API_TOKEN
JWT_SECRET=PASTE_JWT_SECRET
BREVO_API_KEY=PASTE_BREVO_API_KEY
EMAIL_FROM=Sever <alamujude25@gmail.com>
EMAIL_VERIFICATION_TTL_MINUTES=10
RATE_LIMIT_REDIS_URL=redis://:PASTE_REDIS_PASSWORD@redis.railway.internal:6379/1
DAILY_SEARCH_QUOTA=20
RATE_LIMIT_AUTH_PER_MINUTE=10
RATE_LIMIT_API_PER_MINUTE=120
LOGIN_MAX_ATTEMPTS_PER_MINUTE=10
SECURITY_EVENT_RETENTION_DAYS=90
SCHEDULER_TICK_SECONDS=60
DIGEST_SIGNING_SECRET=PASTE_DIGEST_SIGNING_SECRET
KEEPERHUB_API_URL=https://app.keeperhub.com
CREDENTIAL_ENCRYPTION_KEY=PASTE_CREDENTIAL_ENCRYPTION_KEY
```

`CREDENTIAL_ENCRYPTION_KEY` (ADR-076) is 32 random bytes, base64, generated with
`openssl rand -base64 32`. It is what encrypts the KeeperHub keys accounts
connect for themselves, so a scan revokes as *their* delegated wallet instead of
only the one the worker's `KEEPERHUB_API_KEY` executes as (ADR-065). Leave it
empty to keep the feature off: `/api/settings/keeperhub-key` then answers `501`,
the settings panel hides itself, and every scan falls back to that single
deployment wallet. `KEEPERHUB_API_URL` is needed on the backend too — it
validates a key against KeeperHub before storing it.

Rotating `CREDENTIAL_ENCRYPTION_KEY` makes every stored key undecryptable.
Accounts keep scanning but stop auto-revoking until they reconnect their key, so
rotate deliberately, not as routine hygiene.

`APP_ENV=production` makes the missing-variable check fatal instead of a warning
and refuses to expose verification codes over the API (ADR-062). If the backend
will not start, read its log — it names exactly what is missing.

`RATE_LIMIT_REDIS_URL` uses database `1`; Celery has `0`. Sharing one database
would let a `FLUSHDB` from either side clear the other.

### agent-api

```
REDIS_URL=redis://:PASTE_REDIS_PASSWORD@redis.railway.internal:6379/0
INTERNAL_API_TOKEN=PASTE_INTERNAL_API_TOKEN
PORT=8001
```

### agent-worker

```
REDIS_URL=redis://:PASTE_REDIS_PASSWORD@redis.railway.internal:6379/0
BACKEND_INTERNAL_URL=http://backend.railway.internal:8000
INTERNAL_API_TOKEN=PASTE_INTERNAL_API_TOKEN
AGENT_PROVIDERS=live
AGENT_ORCHESTRATOR=langgraph
KEEPERHUB_API_URL=https://app.keeperhub.com
KEEPERHUB_API_KEY=PASTE_KEEPERHUB_API_KEY
AGENT_SCAN_CHAIN_IDS=1,56,8453
GOPLUS_API_KEY=PASTE_GOPLUS_API_KEY_OR_LEAVE_EMPTY
ANTHROPIC_API_KEY=PASTE_ANTHROPIC_API_KEY_OR_LEAVE_EMPTY
AGENT_MODEL_ID=claude-opus-4-8
AGENT_MAX_STEPS=5
AGENT_MAX_COST_USD=2.0
AGENT_LLM_BACKEND=anthropic
AGENT_LLM_TIMEOUT_SECONDS=60
AGENT_LLM_MAX_RETRIES=2
LLM_COST_INPUT_PER_MTOK=5.0
LLM_COST_OUTPUT_PER_MTOK=25.0
```

`INTERNAL_API_TOKEN` must be byte-identical across all three services — it is
what authenticates the worker's result callback to the backend (ADR-006).

`AGENT_SCAN_CHAIN_IDS` is exactly the three chains GoPlus can enumerate
approvals on. Adding Polygon or Arbitrum produces `code 2029` per request.

`code 2029` on a *supported* chain is a rate limit, not a rejection — verified
on 2026-08-05, when Ethereum returned 2029 for one scan and full approval data
for the next. Without `GOPLUS_API_KEY` the worker is on the anonymous tier and
this happens under any burst; the run survives as `degraded` (ADR-064) but the
report carries a banner saying that chain was not reached. Set `GOPLUS_API_KEY`
before anything anyone is watching.

Leaving `ANTHROPIC_API_KEY` empty is safe and cheap: risk tiers come from the
deterministic classifier either way (ADR-060), and only the prose explanation
falls back to a template. Nothing that authorises a transaction depends on it.

**Do not set `KEEPERHUB_SIMULATE_ONLY`.** In production the worker refuses to
start with it (ADR-059), which is the guard working — a simulated run must never
be reported as a revocation.

### web

```
API_ORIGIN=http://backend.railway.internal:8000
HOSTNAME=::
```

`HOSTNAME` overrides the `0.0.0.0` baked into `web/Dockerfile`. Railway supplies
`PORT` itself; do not set it.

## 4. Deploy order

Postgres and `redis` first, then `backend`, then `agent-api` and `agent-worker`,
then `web` last so its build picks up a reachable `API_ORIGIN`.

## 5. Verify it actually works

Do not trust green service cards — a container can be up and still be wired to
nothing.

```sh
# 1. The console answers.
curl -sS -o /dev/null -w '%{http_code}\n' https://YOUR-DOMAIN.up.railway.app/

# 2. The proxy reaches the backend through the private network.
#    Expect 401 "invalid credentials" - that is the backend answering, which
#    is the whole point. A 502 means API_ORIGIN was wrong at build time;
#    rebuild web after fixing it, because the value is baked in.
#    Do not curl /api/healthz: the health endpoint is at the backend root,
#    so that path legitimately 404s and tells you nothing.
curl -sS -X POST https://YOUR-DOMAIN.up.railway.app/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"nobody@example.com","password":"wrong"}'

# 3. Sign up with a real address and confirm the code arrives by e-mail.
#    A code appearing in the HTTP response instead means APP_ENV is not
#    production or no mail provider key was picked up.
```

Then run one scan from the console against a wallet with known approvals and
confirm findings come back across the three chains. That exercises the whole
path: web → backend → Redis → worker → GoPlus → callback → Postgres.

## 6. After the first successful deploy

- **Passkeys (ADR-072) can now be finished.** WebAuthn binds credentials to a
  domain, so the RP ID could not be chosen until this URL existed. Registering
  passkeys against the Railway domain and later moving to a custom domain
  invalidates every one of them — so decide the final hostname before letting
  anyone register one.
- Point `REPO_URL` and any documentation at the deployed URL if you add a custom
  domain.

## Cost

Six services will exceed the $5 Hobby credit; budget roughly $10–20 a month
after any trial. The three application containers dominate — Postgres and Redis
are the cheap part. Scaling `agent-worker` to zero when idle is the easiest
saving, at the cost of a cold start on the first scan.

## Where the secrets are

The generated `JWT_SECRET`, `INTERNAL_API_TOKEN`, `REDIS_PASSWORD`,
`DIGEST_SIGNING_SECRET` and `CREDENTIAL_ENCRYPTION_KEY` were written to a scratch
file outside the repository so they cannot be committed by accident. They live
only in that file and in the Railway dashboard once pasted. Regenerate them with
`openssl rand -hex 32` if they are ever exposed —
`CREDENTIAL_ENCRYPTION_KEY` is the exception: it must be
`openssl rand -base64 32`, and regenerating it costs every account its connected
KeeperHub key (see above).
