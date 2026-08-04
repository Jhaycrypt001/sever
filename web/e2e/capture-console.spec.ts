/**
 * Captures the hero image: a screenshot of the real console, showing a real
 * scan of a real wallet on mainnet.
 *
 * Not part of the suite — it needs the LIVE provider configuration, which the
 * other tests explicitly refuse to run against, and it writes into
 * `public/`. Run it deliberately:
 *
 *     npx playwright test capture-console --grep-invert nothing \
 *       --config playwright.capture.config.ts
 *
 * Why it exists: the landing page used to show a hand-drawn mock of a console
 * that actually works. Every figure in it was invented, and no caption fully
 * fixes that — the honest version of "here is the product" is the product.
 */

import { expect, test } from '@playwright/test'

/** A real wallet with a real dangerous approval (unlimited WETH to Conduit). */
const WALLET = '0x203BDFC8174f94A16F118b0Eb5090d076e3c8701'
const PASSWORD = 'capture-s3cret-password'

test('capture the console with a live mainnet scan', async ({ page }) => {
  // Wide and tall enough that the findings table is not cut off, and dense
  // enough that the tilted hero crop still reads.
  await page.setViewportSize({ width: 1440, height: 1100 })

  const email = `capture-${Date.now()}@test.dev`
  await page.goto('/console')
  await page.getByRole('button', { name: 'No account? Create one' }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()

  await expect(
    page.getByRole('heading', { name: 'Check your email' }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Verify and continue' }).click()

  await page.getByLabel('Wallet to scan').fill(WALLET)
  await page.getByLabel('Mode').selectOption({ label: 'Report only · never revokes' })
  await page.getByRole('button', { name: 'Scan', exact: true }).click()

  // A live scan hits GoPlus on three chains and the model for explanations.
  await expect(page.getByTestId('run-status')).toHaveAttribute(
    'data-status',
    'completed',
    { timeout: 180_000 },
  )

  // The screenshot is worthless unless the dangerous finding is in it — that
  // row is the entire argument of the page.
  const findings = page.getByTestId('finding')
  await expect(findings.first()).toHaveAttribute('data-tier', 'dangerous')
  await expect(page.getByTestId('coverage-notice')).toBeVisible()

  // Let the reveal animations settle so nothing is caught mid-fade.
  await page.waitForTimeout(1500)

  await page.screenshot({
    path: 'public/console.png',
    fullPage: false,
  })
})
