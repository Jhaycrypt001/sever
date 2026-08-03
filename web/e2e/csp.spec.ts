// The check that was missing (ADR-061).
//
// A nonce-based CSP fails silently in exactly one direction: the header is
// set, the HTML carries nonces, every assertion about *configuration* passes,
// and the browser still refuses to run a single script because the nonce in
// the document does not match the nonce in the header. The page renders as
// markup with no behaviour.
//
// Asserting on the header proved nothing. These assert that the scripts
// actually execute.
import { type Page, expect, test } from '@playwright/test'

function collectViolations(page: Page) {
  const violations: string[] = []
  page.on('console', (m) => {
    const text = m.text()
    if (m.type() === 'error' && /Content Security Policy/i.test(text)) {
      violations.push(text)
    }
  })
  page.on('requestfailed', (r) => {
    if (r.failure()?.errorText === 'csp') violations.push(`blocked: ${r.url()}`)
  })
  return violations
}

test('the public page runs its JavaScript under the CSP', async ({ page }) => {
  const violations = collectViolations(page)

  await page.goto('/')
  await page.waitForTimeout(2500)

  expect(violations, violations.slice(0, 3).join('\n')).toEqual([])

  // Hydration proof: this heading is rendered by a client component that
  // animates its words in. If scripts were blocked it would sit at opacity 0
  // and measure zero height, which is precisely the failure this guards.
  const heading = page.getByRole('heading', {
    name: /The approval that drains you is already signed/i,
  })
  await expect(heading).toBeVisible()
  const box = await heading.boundingBox()
  expect(box?.height ?? 0).toBeGreaterThan(40)

  // Interactivity proof: a control that only works with JS.
  await expect(page.getByRole('button', { name: 'Close banner' })).toBeVisible()
  await page.getByRole('button', { name: 'Close banner' }).click()
  await expect(
    page.getByRole('button', { name: 'Close banner' }),
  ).toHaveCount(0)
})

test('the console runs its JavaScript under the CSP', async ({ page }) => {
  const violations = collectViolations(page)

  await page.goto('/console')
  await page.waitForTimeout(2500)

  expect(violations, violations.slice(0, 3).join('\n')).toEqual([])

  // The sign-in form is client-rendered; its presence means React booted.
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('the CSP is still strict', async ({ page }) => {
  const response = await page.goto('/')
  const csp = response?.headers()['content-security-policy'] ?? ''

  expect(csp).toContain("script-src 'self' 'nonce-")
  expect(csp).toContain("'strict-dynamic'")
  expect(csp).toContain("object-src 'none'")
  expect(csp).toContain("frame-ancestors 'none'")
  // The point of the nonce is that this never comes back for scripts.
  expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/)
  expect(csp).not.toMatch(/script-src[^;]*'unsafe-eval'/)
})
