// Browser-level e2e tests (ADR-028/061). They drive the fully containerized
// compose stack (--profile full with AGENT_PROVIDERS=fake, ADR-021) — boot it
// first, then run `npm run test:e2e`. No webServer block on purpose: the stack
// under test is the real one, not a dev server.
//
// Point E2E_BASE_URL at http://localhost:3000 to run them against a local
// `npm run dev` with the backend and worker running on the host.
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // `capture-console.spec.ts` is a build tool, not a test: it needs the LIVE
  // providers this suite refuses, and it writes a file into `public/`. Left
  // unexcluded it joined the suite, spent minutes waiting on a live scan that
  // was never coming, and — worse — silently overwrote the hero screenshot
  // with fake-provider output. It has its own config.
  testIgnore: /capture-console\.spec\.ts/,
  // Fails fast with the fix in the message when the stack is booted in the
  // live/demo configuration, instead of producing a dozen assertion failures
  // that read like a regression (see e2e/global-setup.ts).
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  // Every assertion here waits on a real backend round trip through the
  // console's proxy; the 5s default is tight enough to flake on a cold worker.
  expect: { timeout: 15_000 },
  // Longer than console.spec's COMPLETION_TIMEOUT_MS (90s), on purpose. The
  // default is 30s, which is *shorter* — so a test waiting out its full
  // completion budget was killed by the test timeout first and the 90s was
  // dead code. It went unnoticed while scans happened to finish inside 30s,
  // then failed the whole console file at once when the queue grew: the
  // worker runs Celery's solo pool (one job at a time, ADR-024), so scans
  // serialize no matter how many browser workers are running.
  timeout: 150_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
