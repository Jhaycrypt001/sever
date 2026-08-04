// The real user journey through the browser (ADR-028/061), against the compose
// stack running with the deterministic fake providers (ADR-021): register ->
// scan a wallet -> live status -> findings sorted by risk -> revocation
// receipts. The fake source returns one dangerous, one watch and one safe
// approval per chain, which is the whole risk cascade of ADR-058.
import { type Page, expect, test } from '@playwright/test'

const WALLET = '0x1234567890123456789012345678901234567890'
const PASSWORD = 'e2e-s3cret-password'

function uniqueEmail() {
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@test.dev`
}

/**
 * Fills the credentials form. Leaves the browser on whatever screen the
 * submission produced — for registration that is the verification step.
 */
async function fillCredentials(page: Page, email: string, submit: string) {
  await page.getByLabel('Email').fill(email)
  // `exact` matters: the reveal toggle's accessible name is "Show password",
  // which a substring match also picks up.
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: submit }).click()
}

/**
 * Answers the emailed code (ADR-062/063).
 *
 * The compose stack runs without a mail provider, so the backend returns the
 * code and the console pre-fills the box with it — which is exactly the state
 * a person reaching for their inbox would reach manually. The notice is
 * asserted because a silently empty box here would make every test below fail
 * for an unrelated-looking reason.
 */
async function verifyCode(page: Page) {
  await expect(
    page.getByRole('heading', { name: 'Check your email' }),
  ).toBeVisible()
  await expect(page.getByTestId('verification-notice')).toContainText(
    'No mail provider configured',
  )
  await expect(page.getByLabel('Verification code')).not.toHaveValue('')
  await page.getByRole('button', { name: 'Verify and continue' }).click()
}

async function register(page: Page, email: string) {
  await page.goto('/console')
  await page.getByRole('button', { name: 'No account? Create one' }).click()
  await fillCredentials(page, email, 'Create account')
  await verifyCode(page)
  await expect(page.getByLabel('Wallet to scan')).toBeVisible()
}

/** The full two-factor sign-in of ADR-063: password, then the emailed code. */
async function signIn(page: Page, email: string) {
  await fillCredentials(page, email, 'Sign in')
  await verifyCode(page)
}

// The two modes differ in whether the run may write to the chain, not in
// coverage: workflow is read-only by design (ADR-030/058), agent auto-revokes.
const MODE = {
  report: 'Report only · never revokes',
  revoke: 'Auto-revoke the dangerous ones',
} as const

async function launchScan(page: Page, mode: keyof typeof MODE) {
  await page.getByLabel('Wallet to scan').fill(WALLET)
  await page.getByLabel('Mode').selectOption({ label: MODE[mode] })
  await page.getByRole('button', { name: 'Scan', exact: true }).click()
}

/**
 * How long a launched scan may take to reach `completed`.
 *
 * Generous on purpose. The Celery worker runs `--pool=solo` (a Windows
 * requirement), so it handles exactly one job at a time while Playwright runs
 * tests in parallel — a scan can therefore sit in the queue behind another
 * test's job before it even starts. The observed failure is a job still at
 * `pending`, which is queueing, not slowness in the code under test.
 *
 * This is patience, not tolerance for a bug: `completed` still has to arrive,
 * and every assertion after it is unchanged. Thirty seconds was enough for two
 * chains and became marginal at three (ADR-066 added BSC).
 */
const COMPLETION_TIMEOUT_MS = 90_000

async function waitForCompletion(page: Page) {
  await expect(page.getByTestId('run-status')).toHaveAttribute(
    'data-status',
    'completed',
    { timeout: COMPLETION_TIMEOUT_MS },
  )
}

test('a report-only scan lists the findings by risk and revokes nothing', async ({
  page,
}) => {
  await register(page, uniqueEmail())
  await launchScan(page, 'report')

  // Live status (SSE with polling fallback, ADR-026) until the job finishes.
  await waitForCompletion(page)

  // Most dangerous first (ADR-058) — the console renders the backend's order.
  const findings = page.getByTestId('finding')
  await expect(findings.first()).toHaveAttribute('data-tier', 'dangerous')
  await expect(findings.last()).toHaveAttribute('data-tier', 'safe')

  const dangerous = findings.first()
  await expect(dangerous).toContainText('Unlimited')
  await expect(dangerous).toContainText('phishing activities')

  // Read-only means read-only: nothing may claim to have been revoked, and
  // the run says so at the top rather than leaving it to be inferred.
  await expect(page.getByTestId('report-only-notice')).toContainText(
    'nothing was revoked',
  )
  await expect(page.locator('[data-revocation="revoked"]')).toHaveCount(0)
  await expect(dangerous).toContainText('allowance still spendable')
})

test('an auto-revoke scan revokes the dangerous approval and shows the receipt', async ({
  page,
}) => {
  await register(page, uniqueEmail())
  await launchScan(page, 'revoke')
  await waitForCompletion(page)

  const dangerous = page.getByTestId('finding').first()
  await expect(dangerous).toHaveAttribute('data-tier', 'dangerous')
  await expect(dangerous).toHaveAttribute('data-revocation', 'revoked')
  await expect(dangerous).toContainText('Revoked')

  // Only DANGEROUS is auto-revoked; the unverified spender is flagged and
  // deliberately left alone, and the console must say so plainly.
  await expect(page.locator('[data-tier="watch"]').first()).toContainText(
    'Not revoked',
  )
})

test('a still-live approval is never presented as neutralised', async ({
  page,
}) => {
  // The invariant of ADR-059/061: every revocation state except `revoked`
  // means the allowance can still be spent, and the UI has to say which.
  await register(page, uniqueEmail())
  await launchScan(page, 'report')
  await expect(page.getByTestId('run-status')).toHaveAttribute(
    'data-status',
    'completed',
    { timeout: COMPLETION_TIMEOUT_MS },
  )

  // Every honest label for a still-live allowance. `simulated` in particular
  // has to read as "still live", never borrow the wording of a real
  // revocation — a dry run that looks handled is the failure that leaves
  // someone drained while their dashboard says they are safe.
  const STILL_LIVE_LABELS: Record<string, string> = {
    not_attempted: 'Not revoked',
    pending: 'Revoking…',
    simulated: 'Simulated · still live',
    failed: 'Revoke failed · still live',
  }

  const unrevoked = page.locator(
    '[data-testid="finding"]:not([data-revocation="revoked"])',
  )
  const count = await unrevoked.count()
  expect(count).toBeGreaterThan(0)

  for (let i = 0; i < count; i++) {
    const row = unrevoked.nth(i)
    const state = await row.getAttribute('data-revocation')
    expect(Object.keys(STILL_LIVE_LABELS)).toContain(state)
    await expect(row).toContainText(STILL_LIVE_LABELS[state!])
  }

  // And a dangerous finding that is still live says so in as many words.
  const danger = page.locator(
    '[data-testid="finding"][data-tier="dangerous"]:not([data-revocation="revoked"])',
  )
  if ((await danger.count()) > 0) {
    await expect(danger.first()).toContainText('allowance still spendable')
  }
})

test('the agent streams its decision journal', async ({ page }) => {
  await register(page, uniqueEmail())
  await launchScan(page, 'revoke')

  await expect(page.getByTestId('run-status')).toHaveAttribute(
    'data-status',
    'completed',
    { timeout: COMPLETION_TIMEOUT_MS },
  )

  await page.getByRole('button', { name: /^Journal/ }).click()
  const journal = page.getByTestId('agent-journal')
  await expect(journal).toBeVisible()

  // The fake policy scans Ethereum, then Base, then finishes; the revocations
  // are appended as their own steps (ADR-030/058).
  await expect(journal.locator('li[data-kind="scan"]')).toHaveCount(2)
  await expect(journal.locator('li[data-kind="finish"]')).toContainText(
    'All configured chains scanned',
  )
  await expect(journal.locator('li[data-kind="revoke"]').first()).toContainText(
    'auto-revoked',
  )
})

test('the agent asks for clarification and resumes with the answer', async ({
  page,
}) => {
  // ASK_SENTINEL is the one wallet the fake policy pauses on (ADR-032). It has
  // to be a valid address, because the console refuses anything else — which
  // is why the fake's older "ambiguous" trigger could never fire from a real
  // dispatch, and this path had no browser coverage until now.
  const ASK_SENTINEL = '0x00000000000000000000000000000000000a5c00'

  await register(page, uniqueEmail())
  await page.getByLabel('Wallet to scan').fill(ASK_SENTINEL)
  await page.getByLabel('Mode').selectOption({ label: MODE.revoke })
  await page.getByRole('button', { name: 'Scan', exact: true }).click()

  const status = page.getByTestId('run-status')
  await expect(status).toHaveAttribute('data-status', 'awaiting_input', {
    timeout: COMPLETION_TIMEOUT_MS,
  })
  await expect(page.getByText('Which chains should I scan')).toBeVisible()

  await page.getByLabel('Your answer').fill('every supported chain')
  await page.getByRole('button', { name: 'Answer' }).click()

  await expect(status).toHaveAttribute('data-status', 'completed', {
    timeout: COMPLETION_TIMEOUT_MS,
  })
  await expect(page.getByTestId('finding').first()).toHaveAttribute(
    'data-tier',
    'dangerous',
  )
})

test('a wallet can be watched on a schedule and unwatched (ADR-033)', async ({
  page,
}) => {
  await register(page, uniqueEmail())

  const section = page.getByTestId('recurring-section')
  await expect(section).toBeVisible()
  await expect(section).toContainText('Nothing watched yet')

  await section.getByLabel('Wallet to watch').fill(WALLET)
  await section.getByRole('button', { name: 'Watch' }).click()

  const item = section.locator('li').first()
  await expect(item).toBeVisible()
  await expect(item).toContainText('every 60 min')

  await item.getByRole('button', { name: /^Stop watching/ }).click()
  await expect(section).toContainText('Nothing watched yet')
})

test('a returning user signs back in and finds the previous scans', async ({
  page,
}) => {
  const email = uniqueEmail()
  await register(page, email)
  await launchScan(page, 'report')
  await expect(page.getByTestId('run-status')).toHaveAttribute(
    'data-status',
    'completed',
    { timeout: COMPLETION_TIMEOUT_MS },
  )

  // Fresh browser state = the HttpOnly refresh cookie is gone (ADR-008):
  // signing back in must list the scan launched above.
  await page.context().clearCookies()
  await page.goto('/console')
  // Verified or not, a sign-in always takes a second factor (ADR-063).
  await signIn(page, email)

  await expect(page.getByTestId('scan-detail')).toBeVisible()
  await expect(page.getByTestId('run-status')).toContainText('Complete')
  await expect(page.getByTestId('finding').first()).toHaveAttribute(
    'data-tier',
    'dangerous',
  )
})

test('the public page hands a pasted address to the console', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByLabel('Wallet address').fill(WALLET)
  // `exact` matters: the "how it works" stepper has a "01 · Detect" button
  // whose accessible name also contains "Scan".
  await page.getByRole('button', { name: 'Scan', exact: true }).click()

  // Not signed in: the console asks for credentials first, and keeps the
  // address so it is still there afterwards.
  await expect(page).toHaveURL(new RegExp(`/console\\?address=${WALLET}`))
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()

  const email = uniqueEmail()
  await page.getByRole('button', { name: 'No account? Create one' }).click()
  await fillCredentials(page, email, 'Create account')
  await verifyCode(page)

  // The pasted address survives registration *and* the verification detour.
  await expect(page.getByLabel('Wallet to scan')).toHaveValue(WALLET)
})

test('a password alone never opens a session, before or after verification', async ({
  page,
}) => {
  // ADR-063: the password is one factor of two. Abandoning the code screen and
  // coming back with correct credentials has to land on the code screen again —
  // telling someone their password is wrong when it is not is how people
  // conclude the product is broken.
  const email = uniqueEmail()
  await page.goto('/console')
  await page.getByRole('button', { name: 'No account? Create one' }).click()
  await fillCredentials(page, email, 'Create account')
  await expect(
    page.getByRole('heading', { name: 'Check your email' }),
  ).toBeVisible()

  // Walk away from the code and try the front door with correct credentials.
  await page.getByRole('button', { name: 'Use a different address' }).click()
  await fillCredentials(page, email, 'Sign in')

  await expect(
    page.getByRole('heading', { name: 'Check your email' }),
  ).toBeVisible()
  // No error was shown on the way: the password was right.
  await expect(page.getByTestId('auth-error')).toHaveCount(0)

  // The code that arrived with that sign-in attempt works.
  await verifyCode(page)
  await expect(page.getByLabel('Wallet to scan')).toBeVisible()
})

test('a wrong verification code is refused and the box stays usable', async ({
  page,
}) => {
  const email = uniqueEmail()
  await page.goto('/console')
  await page.getByRole('button', { name: 'No account? Create one' }).click()
  await fillCredentials(page, email, 'Create account')
  await expect(
    page.getByRole('heading', { name: 'Check your email' }),
  ).toBeVisible()

  const box = page.getByLabel('Verification code')
  const real = await box.inputValue()
  const wrong = real === '000000' ? '111111' : '000000'

  await box.fill(wrong)
  await page.getByRole('button', { name: 'Verify and continue' }).click()
  await expect(page.getByTestId('auth-error')).toContainText(
    'invalid or expired',
  )
  // Still on the code step, and still able to try the real one.
  await box.fill(real)
  await page.getByRole('button', { name: 'Verify and continue' }).click()
  await expect(page.getByLabel('Wallet to scan')).toBeVisible()
})

test('a forgotten password can be reset from the sign-in screen', async ({
  page,
}) => {
  // ADR-063: recovery ends signed in, because by then both factors have been
  // proved — the mailed code, and the password just chosen.
  const email = uniqueEmail()
  await register(page, email)
  await page.context().clearCookies()
  await page.goto('/console')

  await page.getByRole('button', { name: 'Forgot password?' }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByRole('button', { name: 'Send reset code' }).click()

  await expect(
    page.getByRole('heading', { name: 'Choose a new password' }),
  ).toBeVisible()
  await expect(page.getByTestId('verification-notice')).toContainText(
    'No mail provider configured',
  )
  await expect(page.getByLabel('Verification code')).not.toHaveValue('')

  const NEW_PASSWORD = 'a-brand-new-e2e-password'
  await page.getByLabel('New password', { exact: true }).fill(NEW_PASSWORD)
  await page.getByRole('button', { name: 'Set password and sign in' }).click()

  // Straight into the console, no second sign-in.
  await expect(page.getByLabel('Wallet to scan')).toBeVisible()

  // And the new password is the one that works from now on.
  await page.context().clearCookies()
  await page.goto('/console')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(NEW_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await verifyCode(page)
  await expect(page.getByLabel('Wallet to scan')).toBeVisible()
})

test('the old password stops working after a reset', async ({ page }) => {
  const email = uniqueEmail()
  await register(page, email)
  await page.context().clearCookies()
  await page.goto('/console')

  await page.getByRole('button', { name: 'Forgot password?' }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByRole('button', { name: 'Send reset code' }).click()
  await page
    .getByLabel('New password', { exact: true })
    .fill('another-new-e2e-password')
  await page.getByRole('button', { name: 'Set password and sign in' }).click()
  await expect(page.getByLabel('Wallet to scan')).toBeVisible()

  await page.context().clearCookies()
  await page.goto('/console')
  // PASSWORD is what the account was registered with, and it is now stale.
  await fillCredentials(page, email, 'Sign in')
  await expect(page.getByTestId('auth-error')).toContainText(
    'invalid credentials',
  )
})
