// ACT-SURFACES: web.list web.settings
// "Hide empty sections" (ACT layout.hide_empty): a layout can leave out a section with no conversations
// instead of showing "No … conversations" under its header. The main section always stays, so an
// empty tab still says so. Turned on and off from the layout editor.
import { expect, test } from './fixtures'

const head = (page: import('@playwright/test').Page, title: string) =>
  page.locator('[data-mail-list] .section-head').filter({ has: page.locator('.title', { hasText: title }) })

test('an empty section is left out when the layout hides empty sections', async ({ page }) => {
  const lay = { id: 'tidy', name: 'Tidy', tabs: [{ name: 'Inbox', icon: 'inbox', badge: 'unread', sections: [
    { title: 'Financial', base: 'inbox', q: 'subject:"no-such-subject-anywhere-4711"', limit: 5 },
    { title: 'Everything else', base: 'inbox', exclude_above: false }] }] }
  expect((await page.request.put('/api/v1/layouts', { data: { layouts: [lay], active: 'tidy' } })).ok()).toBeTruthy()

  await page.goto('/mail/inbox')
  await expect(head(page, 'Everything else')).toHaveCount(1)
  await expect(head(page, 'Financial')).toHaveCount(1)
  await expect(page.locator('[data-mail-list]')).toContainText('No financial conversations')

  await page.goto('/settings/inbox')
  await page.getByRole('button', { name: 'Edit Tidy' }).click()
  await page.getByRole('checkbox', { name: 'Hide empty sections' }).check()
  await page.getByRole('button', { name: 'Save layout' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.goto('/mail/inbox')
  await expect(head(page, 'Everything else')).toHaveCount(1)
  await expect(head(page, 'Financial')).toHaveCount(0)
  await expect(page.locator('[data-mail-list]')).not.toContainText('No financial conversations')
  const saved = (await (await page.request.get('/api/v1/layouts')).json()) as { layouts: { id: string; hide_empty?: boolean }[] }
  expect(saved.layouts.find((l) => l.id === 'tidy')?.hide_empty).toBe(true)
})
