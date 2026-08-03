// Layout invariants that are easy to break and easy to miss.
//
// A page that scrolls sideways on a phone looks broken in a way no unit test
// notices: everything renders, nothing throws, and the reader gets a wobbling
// viewport. The findings table caused exactly that at 375px, because its
// fixed-width columns added up to more than the screen.
import { expect, test } from '@playwright/test'

const VIEWPORTS = [
  { name: 'small phone', width: 320, height: 720 },
  { name: 'phone', width: 390, height: 844 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'laptop', width: 1280, height: 800 },
]

for (const vp of VIEWPORTS) {
  test(`the public page never scrolls sideways on a ${vp.name}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await page.goto('/')
    await page.waitForTimeout(1200)

    const { clientWidth, scrollWidth, offenders } = await page.evaluate(() => {
      const doc = document.documentElement
      const clipped = (el: Element) => {
        let p: Element | null = el.parentElement
        while (p) {
          const o = getComputedStyle(p).overflowX
          if (o !== 'visible') return true
          p = p.parentElement
        }
        return false
      }
      const offenders: string[] = []
      for (const el of Array.from(document.querySelectorAll('*'))) {
        const r = el.getBoundingClientRect()
        if (r.width === 0 && r.height === 0) continue
        if (r.right > doc.clientWidth + 1 && !clipped(el)) {
          offenders.push(
            `${el.tagName}.${String((el as HTMLElement).className).slice(0, 60)}`,
          )
        }
      }
      return {
        clientWidth: doc.clientWidth,
        scrollWidth: doc.scrollWidth,
        offenders: [...new Set(offenders)].slice(0, 6),
      }
    })

    expect(scrollWidth, `overflowing: ${offenders.join(', ')}`).toBeLessThanOrEqual(
      clientWidth + 1,
    )
  })
}

test('the walkthrough advances from 1/4 to 4/4 as the page scrolls', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/')

  const counter = page.getByText(/^Step \d \/ 4$/)
  await counter.scrollIntoViewIfNeeded()
  await page.waitForTimeout(600)

  const seen = new Set<string>()
  for (let i = 0; i < 12; i++) {
    const text = (await counter.textContent())?.trim()
    if (text) seen.add(text)
    if (seen.has('Step 4 / 4')) break
    await page.mouse.wheel(0, 500)
    await page.waitForTimeout(400)
  }

  // All four stages must be reachable by scrolling alone.
  expect([...seen].sort()).toEqual([
    'Step 1 / 4',
    'Step 2 / 4',
    'Step 3 / 4',
    'Step 4 / 4',
  ])

  // And scrolling back up rewinds it, rather than latching on the last stage.
  for (let i = 0; i < 14; i++) {
    await page.mouse.wheel(0, -500)
    await page.waitForTimeout(250)
    if ((await counter.textContent())?.includes('Step 1 / 4')) break
  }
  await expect(counter).toHaveText('Step 1 / 4')
})

test('the header folds on the way down and unfolds on the way up', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/')
  await page.waitForTimeout(800)

  const header = page.locator('header').first()
  const topOf = async () => (await header.boundingBox())?.y ?? 0

  expect(await topOf()).toBeGreaterThanOrEqual(-1)

  await page.mouse.wheel(0, 1800)
  await page.waitForTimeout(1000)
  const folded = await topOf()
  expect(folded, 'header should be off-screen after scrolling down').toBeLessThan(
    -20,
  )

  await page.mouse.wheel(0, -400)
  await page.waitForTimeout(1000)
  const unfolded = await topOf()
  expect(unfolded, 'header should return when scrolling up').toBeGreaterThan(
    folded,
  )
})

test('the mobile menu opens and closes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.waitForTimeout(800)

  await expect(page.getByRole('navigation', { name: 'Mobile' })).toHaveCount(0)

  await page.getByRole('button', { name: 'Open menu' }).click()
  const nav = page.getByRole('navigation', { name: 'Mobile' })
  await expect(nav).toBeVisible()
  await expect(nav.getByRole('link', { name: 'CONSOLE' })).toBeVisible()

  await page.getByRole('button', { name: 'Close menu' }).click()
  await expect(nav).toHaveCount(0)
})
