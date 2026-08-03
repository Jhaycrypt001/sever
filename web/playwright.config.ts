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
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  // Every assertion here waits on a real backend round trip through the
  // console's proxy; the 5s default is tight enough to flake on a cold worker.
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
