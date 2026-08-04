/**
 * A separate config for `e2e/capture-console.spec.ts`, which is not a test.
 *
 * The main config runs a global setup that refuses a live-provider stack —
 * correctly, because the suite asserts deterministic fixtures. The capture
 * needs the opposite: real providers, real chains, a real wallet. Giving it
 * its own config keeps that exception explicit rather than weakening the
 * guard the suite depends on.
 */
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: /capture-console\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  // A live scan calls a threat-intel API on three chains and a model for the
  // explanations; the suite's 15s default is far too tight.
  expect: { timeout: 60_000 },
  timeout: 300_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
