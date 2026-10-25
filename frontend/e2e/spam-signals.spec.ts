// Spam and triage signals: a conversation in Spam shows no Reply needed / Waiting /
// Due chip in the Spam list or the reader, even when a row the client holds still carries one, and
// "Not spam" brings the chip back. Seed: "E2E Due form" with a deadline (mailr e2e-seed --extras).
import { conv, expect, test } from './fixtures'

test.use({ extras: true })

const row = (page: import('@playwright/test').Page, subject: string) =>
  page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

test('web.list and web.reader: no signal chips in Spam; Not spam brings them back', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Due form').getByTestId('deadline-chip')).toBeVisible()
  const c = await conv(page.request, 'E2E Due form')
  expect((await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'spam' } })).ok()).toBeTruthy()

  await page.goto('/mail/spam')
  await expect(row(page, 'E2E Due form')).toBeVisible()
  await expect(row(page, 'E2E Due form').locator('.signal-chip, [data-testid="deadline-chip"]')).toHaveCount(0)
  await row(page, 'E2E Due form').click()
  await expect(page.getByRole('heading', { name: 'E2E Due form' })).toBeVisible()
  await expect(page.locator('.conv-signals')).toHaveCount(0)

  // A row that still carries signals (a cached page, an older server) shows none in Spam either.
  await page.route('**/api/v1/conversations?*', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    for (const it of body.items ?? []) it.signals = [{ name: 'deadline', date: '2027-12-03' }, { name: 'needs_reply' }]
    await route.fulfill({ response: res, json: body })
  })
  await page.goto('/mail/spam')
  await expect(row(page, 'E2E Due form')).toBeVisible()
  await expect(row(page, 'E2E Due form').locator('.signal-chip, [data-testid="deadline-chip"]')).toHaveCount(0)
  await page.unroute('**/api/v1/conversations?*')

  expect((await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'not_spam' } })).ok()).toBeTruthy()
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Due form').getByTestId('deadline-chip')).toBeVisible()
})
