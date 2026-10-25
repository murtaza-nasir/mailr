// Inbox layout tabs: an email opened in one tab does not follow you into another (two
// one-section tabs used to share the held row), and switching tabs closes it.
import { expect, test } from './fixtures'

test('switching tabs closes the open email and does not carry its row along', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'cards'))
  const csrf = (await (await page.request.get('/api/v1/me')).json()).csrf
  const r = await page.request.put('/api/v1/layouts', { headers: csrf ? { 'X-CSRF-Token': csrf } : {}, data: { active: 'two', layouts: [{ name: 'Two', tabs: [
    { name: 'Inbox', icon: 'inbox', sections: [{ title: 'Inbox', base: 'inbox', q: 'subject:"E2E Reply to me"' }] },
    { name: 'Other', icon: 'tag', sections: [{ title: 'Other', base: 'inbox', q: 'subject:"E2E Mute me"' }] },
  ] }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await page.goto('/mail/inbox')
  const rows = page.locator('[data-mail-list] [role="row"]')
  await rows.filter({ hasText: 'E2E Reply to me' }).first().click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
  await page.getByRole('tab', { name: /Other/ }).click()
  await expect(rows.filter({ hasText: 'E2E Mute me' })).toHaveCount(1)
  await expect(rows.filter({ hasText: 'E2E Reply to me' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toHaveCount(0)
})
