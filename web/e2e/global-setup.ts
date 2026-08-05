/**
 * Refuses to run the browser suite against a stack it cannot pass on.
 *
 * The suite needs two things the live/mainnet configuration does not provide:
 * the deterministic fake providers (ADR-021), and auth rate limits loose
 * enough for ~20 registrations in a few minutes (ADR-017). Booting with the
 * demo configuration instead produces a dozen assertion failures that look
 * exactly like a regression and are not one — it cost a full debugging round
 * twice, including once where a partial result got reported as a pass.
 *
 * Checked here rather than documented again, because a note in COMMANDS.md
 * only helps the person who already suspects the cause.
 */

import { request } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:8080'

function bail(problem: string, fix: string): never {
  throw new Error(
    [
      '',
      '  The stack is not in a state this suite can pass against.',
      '',
      `  ${problem}`,
      '',
      `  ${fix}`,
      '',
      '  Boot it with:',
      '',
      '    AGENT_PROVIDERS=fake RATE_LIMIT_AUTH_PER_MINUTE=1000 \\',
      '    LOGIN_MAX_ATTEMPTS_PER_MINUTE=1000 DAILY_SEARCH_QUOTA=1000 \\',
      '    APP_ENV=development RESEND_API_KEY= \\',
      '    docker compose --profile full up -d --build',
      '',
      '  This shadows .env without editing it, so a live demo config survives.',
      '',
    ].join('\n'),
  )
}

export default async function globalSetup() {
  const api = await request.newContext({ baseURL: BASE })

  let reachable = false
  try {
    reachable = (await api.get('/console')).ok()
  } catch {
    reachable = false
  }
  if (!reachable) {
    bail(
      `Nothing is answering at ${BASE}.`,
      'The compose stack is not up, or it is still starting.',
    )
  }

  // Probe the per-IP auth limiter's headroom. A single successful request
  // proves nothing — it succeeds at a limit of 10 too, which is how the first
  // version of this check let a misconfigured stack through and produced 18
  // failures that looked like a regression.
  //
  // `/api/auth/refresh` with no cookie is the right probe: the limiter runs as
  // middleware, before the handler, so a 401 still counts against the budget,
  // and nothing is created. At the suite's configured limit these are free; at
  // the production default one of them will 429.
  const PROBES = 15
  for (let i = 0; i < PROBES; i++) {
    const probe = await api.post('/api/auth/refresh')
    if (probe.status() === 429) {
      bail(
        `The auth rate limiter refused request ${i + 1} of ${PROBES}.`,
        'RATE_LIMIT_AUTH_PER_MINUTE is at its production default; this suite makes far more auth calls than that.',
      )
    }
  }

  // The response carries a verification code only when no mail provider is
  // configured, which is what lets the browser finish a sign-up.
  const email = `preflight-${Date.now()}@test.dev`
  const res = await api.post('/api/auth/register', {
    data: { email, password: 'preflight-s3cret-password' },
  })
  if (!res.ok()) {
    bail(
      `Registration answered ${res.status()}, not 201.`,
      'Check `docker compose logs backend` — a stale image whose migrations lag the database looks like this.',
    )
  }

  const body = (await res.json()) as { verification_code?: string | null }
  if (!body.verification_code) {
    bail(
      'Registration returned no verification code.',
      'RESEND_API_KEY is set, so codes go to a mailbox the browser cannot read. Unset it for the suite.',
    )
  }

  // Prove the worker is on the fakes, rather than trusting that whoever booted
  // the stack passed the override. Nothing above catches this: registration
  // and rate limits behave identically either way, and a `--build web` that
  // recreates only one service leaves the worker on whatever `.env` says.
  //
  // The fake source answers for any address; the live one answers for a
  // freshly generated address with nothing at all. So one scan of a random
  // address separates them, and it costs a few seconds once per run.
  const token = (
    (await (
      await api.post('/api/auth/verify', {
        data: { email, code: body.verification_code },
      })
    ).json()) as { access_token: string }
  ).access_token

  const probeWallet = '0x' + 'a1b2c3d4'.repeat(5)
  const launched = (await (
    await api.post('/api/searches', {
      data: { wallet_address: probeWallet, mode: 'workflow' },
      headers: { authorization: `Bearer ${token}` },
    })
  ).json()) as { job_id: string }

  if (!launched.job_id) {
    bail(
      'The probe scan was not accepted for dispatch.',
      'Check `docker compose logs backend` — the API rejected a valid scan request.',
    )
  }

  const DEADLINE_MS = 60_000
  const startedAt = Date.now()
  let settled = false
  while (Date.now() - startedAt < DEADLINE_MS) {
    const detail = (await (
      await api.get(`/api/searches/${launched.job_id}`, {
        headers: { authorization: `Bearer ${token}` },
      })
    ).json()) as { status?: string; results?: unknown[] }

    if (detail.status === 'completed') {
      if (!detail.results || detail.results.length === 0) {
        bail(
          'A scan of a random unused address returned no findings.',
          'The worker is on the LIVE providers. The suite asserts the deterministic fakes of ADR-021.',
        )
      }
      settled = true
      break
    }
    if (detail.status === 'failed') {
      bail(
        'A probe scan failed outright.',
        'Check `docker compose logs agent-worker` — the worker cannot reach its providers.',
      )
    }
    await new Promise((r) => setTimeout(r, 2000))
  }

  // Falling through the loop used to continue silently, which would have made
  // this whole check decorative in exactly the case it exists for.
  if (!settled) {
    bail(
      `A probe scan never finished within ${DEADLINE_MS / 1000}s.`,
      'The worker is not consuming the queue — check `docker compose logs agent-worker`.',
    )
  }

  await api.dispose()
}
