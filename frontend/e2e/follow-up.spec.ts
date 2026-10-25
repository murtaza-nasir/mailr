// ACT-SURFACES: web.list web.reader
// mail.follow_up on web.reader and web.list: "Remind me if no reply" from the reader, the
// "No reply" chip on a row that came back, and the reader's banner (Dismiss ends the mark).
// The attachments view is here too: every file across the mailbox.
import { conv, expect, test } from './fixtures'

const row = (page: import('@playwright/test').Page, subject: string) =>
  page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

async function full(page: import('@playwright/test').Page, subject: string) {
  const r = await page.request.get('/api/v1/conversations', { params: { view: 'all', q: `subject:"${subject}"`, limit: '5' } })
  return ((await r.json()).items as { subject: string; follow_up_at: string | null; nudged_at: string | null }[])
    .find((x) => x.subject === subject)!
}

test('web.reader: set a follow-up reminder, then cancel it', async ({ page }) => {
  await page.goto('/mail/inbox')
  await row(page, 'E2E Follow me').click()
  await expect(page.getByRole('heading', { name: 'E2E Follow me' })).toBeVisible()
  await page.getByRole('button', { name: 'Remind me if no reply' }).click()
  await page.getByRole('menuitem', { name: /In 3 days/ }).click()
  await expect.poll(async () => (await full(page, 'E2E Follow me')).follow_up_at).not.toBeNull()
  const at = new Date((await full(page, 'E2E Follow me')).follow_up_at!)
  expect(Math.round((at.getTime() - Date.now()) / 86_400_000)).toBeGreaterThanOrEqual(2)
  expect(at.getHours()).toBe(9)

  await page.getByRole('button', { name: 'Remind me if no reply' }).click()
  await page.getByRole('menuitem', { name: 'Cancel reminder' }).click()
  await expect.poll(async () => (await full(page, 'E2E Follow me')).follow_up_at).toBeNull()
})

test('web.list and web.reader: a conversation that came back shows No reply; Dismiss ends it', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Nudged me').locator('.nudge-chip')).toHaveText('No reply')
  await expect(row(page, 'E2E Follow me').locator('.nudge-chip')).toHaveCount(0)
  await row(page, 'E2E Nudged me').click()
  const banner = page.locator('.nudge-banner')
  await expect(banner).toContainText('No reply since you wrote')
  await banner.getByRole('button', { name: 'Write a follow-up' }).click()
  await expect(page.locator('[data-inline-reply]')).toContainText('dana@example.org')
  await banner.getByRole('button', { name: 'Dismiss' }).click()
  await expect.poll(async () => (await full(page, 'E2E Nudged me')).nudged_at).toBeNull()
  await expect(banner).toHaveCount(0)
})

test('the attachments view lists files, filters by kind and opens the email', async ({ page }) => {
  await page.goto('/mail/attachments')
  const card = (name: string) => page.locator('.files-card').filter({ hasText: name })
  await expect(card('e2e-contract.pdf')).toBeVisible()
  await expect(card('e2e-budget.xlsx')).toBeVisible()
  await page.getByRole('radio', { name: 'Spreadsheets' }).click()
  await expect(card('e2e-budget.xlsx')).toBeVisible()
  await expect(card('e2e-contract.pdf')).toHaveCount(0)
  await page.getByRole('radio', { name: 'All' }).click()
  await page.getByLabel('File name').fill('contract')
  await expect(page.locator('.files-card')).toHaveCount(1)
  await expect(card('e2e-contract.pdf').getByRole('link', { name: 'e2e-contract.pdf', exact: true })).toHaveAttribute('href', /\/attachments\/\d+$/)
  await card('e2e-contract.pdf').getByRole('button', { name: /E2E Files/ }).click()
  await expect(page.getByRole('heading', { name: 'E2E Files' })).toBeVisible()
  expect((await conv(page.request, 'E2E Files')).subject).toBe('E2E Files')
})
