// ACT-SURFACES: web.list web.reader
// mail.dismiss_deadline on web.list and web.reader: the Due chip's menu ("Not a deadline", "Not a
// deadline from this sender") from a click, a right-click and in the reader. The chip goes at once
// and has:deadline no longer finds the conversation. Seed: "E2E Due form" and "E2E Due report",
// both from Rita Forms (mailr e2e-seed --extras).
import { expect, test } from './fixtures'

test.use({ extras: true })

const row = (page: import('@playwright/test').Page, subject: string) =>
  page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

/** The chip, scrolled into view and settled: a scroll event closes an open menu. */
async function chipIn(page: import('@playwright/test').Page, subject: string, name: string) {
  const chip = row(page, subject).getByRole('button', { name })
  await expect(chip).toBeVisible()
  await chip.scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  return chip
}

async function deadlineHits(page: import('@playwright/test').Page): Promise<string[]> {
  const r = await page.request.get('/api/v1/conversations', { params: { view: 'all', q: 'has:deadline', limit: '20' } })
  return ((await r.json()).items as { subject: string }[]).map((x) => x.subject)
}

test('web.list: "Not a deadline" from the chip menu removes the chip at once', async ({ page }) => {
  await page.goto('/mail/inbox')
  const chip = await chipIn(page, 'E2E Due form', 'Due Dec 3')
  await chip.click()
  await expect(page).toHaveURL(/\/mail\/inbox$/) // the chip's click does not open the email
  await page.getByRole('menuitem', { name: 'Not a deadline', exact: true }).click()
  await expect(row(page, 'E2E Due form').getByTestId('deadline-chip')).toHaveCount(0)
  await expect(page.getByText('Deadline removed.')).toBeVisible()
  await expect(page).toHaveURL(/\/mail\/inbox$/) // choosing does not open the email either
  await expect.poll(() => deadlineHits(page)).not.toContain('E2E Due form')
  expect(await deadlineHits(page)).toContain('E2E Due report')
})

test('web.list: right-click on the chip mutes the sender; the reader shows no chip', async ({ page }) => {
  await page.goto('/mail/inbox')
  await (await chipIn(page, 'E2E Due report', 'Due Dec 10')).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Not a deadline from this sender' }).click()
  await expect(page.getByTestId('deadline-chip')).toHaveCount(0) // both of Rita's conversations
  await expect.poll(() => deadlineHits(page)).toEqual([])
  await row(page, 'E2E Due form').click()
  await expect(page.getByRole('heading', { name: 'E2E Due form' })).toBeVisible()
  await expect(page.locator('.conv-signals')).toHaveCount(0)
})

test('web.reader: the Due chip under the subject has the same menu', async ({ page }) => {
  await page.goto('/mail/inbox')
  await row(page, 'E2E Due form').click()
  await expect(page.getByRole('heading', { name: 'E2E Due form' })).toBeVisible()
  const chip = page.locator('.conv-signals').getByRole('button', { name: 'Due Dec 3' })
  await chip.click()
  await page.getByRole('menuitem', { name: 'Not a deadline', exact: true }).click()
  await expect(page.locator('.conv-signals')).toHaveCount(0)
  await expect(row(page, 'E2E Due form').getByTestId('deadline-chip')).toHaveCount(0)
  await expect.poll(() => deadlineHits(page)).not.toContain('E2E Due form')
})

// A long subject with labels once pushed the chip under the
// line's clip ("Due O"). The subject gives way; a narrow line shows the date alone.
test('web.list: a long subject never clips the Due chip; a narrow line shows the date alone', async ({ page }) => {
  await page.route(/\/api\/v1\/conversations(\?|$)/, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    for (const c of body.items ?? []) if (c.subject === 'E2E Due form') c.subject = `E2E Due form ${'and a very long subject line '.repeat(8)}`
    await route.fulfill({ response: res, json: body })
  })
  const fits = async () => {
    const chip = row(page, 'E2E Due form').getByTestId('deadline-chip')
    await expect(chip).toBeVisible()
    return chip.evaluate((el) => {
      const line = el.closest('.line')!.getBoundingClientRect()
      const r = el.getBoundingClientRect()
      return { inside: r.right <= line.right + 0.5 && r.left >= line.left - 0.5, whole: el.scrollWidth <= el.clientWidth + 1, text: (el as HTMLElement).innerText }
    })
  }
  await page.setViewportSize({ width: 1600, height: 900 })
  await page.goto('/mail/inbox')
  const wide = await fits()
  expect(wide, JSON.stringify(wide)).toMatchObject({ inside: true, whole: true, text: 'Due Dec 3' })
  await page.setViewportSize({ width: 1100, height: 900 }) // the line is about 390 px: the short form
  await expect.poll(async () => (await fits()).text).toBe('Dec 3')
  const narrow = await fits()
  expect(narrow, JSON.stringify(narrow)).toMatchObject({ inside: true, whole: true })
  await expect(row(page, 'E2E Due form').getByRole('button', { name: 'Due Dec 3' })).toBeVisible()
})

// The subject once ended in an ellipsis at 70% of the line while the
// line still had room. A subject that fits shows whole; only the snippet gives up its space.
test('web.list: a subject that fits the line is not cut short', async ({ page }) => {
  const subject = 'E2E Due form: the quarterly registration packet needs a signature from the office'
  await page.route(/\/api\/v1\/conversations(\?|$)/, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    for (const c of body.items ?? []) if (c.subject === 'E2E Due form') { c.subject = subject; c.snippet = '' }
    await route.fulfill({ response: res, json: body })
  })
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/mail/inbox')
  const s = row(page, 'E2E Due form').locator('.subject')
  await expect(s).toHaveText(subject)
  const m = await s.evaluate((el) => ({ cut: el.scrollWidth > el.clientWidth + 1, share: el.clientWidth / el.closest('.line')!.clientWidth }))
  expect(m.cut, JSON.stringify(m)).toBe(false)
  expect(m.share, JSON.stringify(m)).toBeGreaterThan(0.7) // past the old cap
})
