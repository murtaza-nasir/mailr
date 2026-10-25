// ACT-SURFACES: web.reader web.settings
// mail.reply_suggestions on web.reader: chips under the last personal email start the reply with
// their text; the setting turns them off. The model is stubbed (backend/tests/test_reply_suggestions.py
// covers the endpoint).
import { expect, test } from './fixtures'

const open = async (page: import('@playwright/test').Page) => {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Reply to me' }).first().click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
}

test('mail.reply_suggestions: a chip starts the reply with its text', async ({ page }) => {
  await page.route('**/api/v1/messages/*/reply-suggestions', (r) =>
    r.fulfill({ json: { status: 'ready', replies: ['Sounds good, see you then.', 'Can we do Friday?', 'Let me check.'] } }))
  await open(page)
  const chips = page.getByRole('group', { name: 'Suggested replies' })
  await expect(chips.getByRole('button')).toHaveCount(3)
  await chips.getByRole('button', { name: 'Can we do Friday?' }).click()
  const reply = page.locator('[data-inline-reply]')
  await expect(reply).toBeVisible()
  await expect(reply.locator('[contenteditable="true"]').first()).toContainText('Can we do Friday?')
})

test('mail.reply_suggestions: off in Settings, no chips and no request', async ({ page }) => {
  let asked = 0
  await page.route('**/api/v1/messages/*/reply-suggestions', (r) => { asked++; return r.fulfill({ json: { status: 'ready', replies: ['Yes'] } }) })
  expect((await page.request.patch('/api/v1/me/settings', { data: { reply_suggestions: false } })).ok()).toBeTruthy()
  await open(page)
  await expect(page.getByRole('group', { name: 'Suggested replies' })).toHaveCount(0)
  expect(asked).toBe(0)
})
