// The sidebar numbers come from the server's counts of the lists they open (GET /counts) and
// follow every change without a reload, through the live stream's `counts` event. A draft in
// Trash is not a draft: the Drafts number and the Drafts list agree.
import type { Browser, Page } from '@playwright/test'
import { conv, expect, test, type Seed } from './fixtures'

const nav = (page: Page, name: string) =>
  page.locator('nav a.nav-item', { has: page.locator('.name', { hasText: new RegExp(`^${name}$`) }) }).first()

async function elsewhere(browser: Browser, seeded: Seed) {
  const other = await browser.newContext({ baseURL: process.env.E2E_BASE ?? 'http://localhost:5175' })
  const r = await other.request.post('/api/v1/auth/login', { data: { email: seeded.email, password: seeded.password } })
  const headers = { 'X-CSRF-Token': (await r.json()).csrf }
  return { req: other.request, headers, close: () => other.close() }
}

async function serverCounts(page: Page) {
  return (await (await page.request.get('/api/v1/counts')).json()) as { views: Record<string, { unread: number; total: number }> }
}

test('a draft trashed elsewhere leaves the Drafts number without a reload', async ({ page, browser, seeded }) => {
  await page.goto('/mail/inbox')
  // The app is up (and its live stream with it) before the change is made elsewhere.
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  const o = await elsewhere(browser, seeded)
  const d = await o.req.post('/api/v1/drafts', { data: { payload: { to: [{ email: 'a@example.org' }], subject: 'E2E counted draft', text: 'x' } }, headers: o.headers })
  expect(d.ok(), await d.text()).toBeTruthy()
  const drafts = (await serverCounts(page)).views.drafts.total
  expect(drafts).toBeGreaterThan(0)
  await expect(nav(page, 'Drafts').locator('.count')).toHaveText(String(drafts), { timeout: 8_000 })

  const cid = (await d.json()).draft.conversation_id
  const t = await o.req.post('/api/v1/conversations/actions', { data: { ids: [cid], action: 'trash' }, headers: o.headers })
  expect(t.ok()).toBeTruthy()
  await o.close()
  const after = (await serverCounts(page)).views.drafts.total
  expect(after).toBe(drafts - 1)
  if (after) await expect(nav(page, 'Drafts').locator('.count')).toHaveText(String(after), { timeout: 8_000 })
  else await expect(nav(page, 'Drafts').locator('.count')).toHaveCount(0, { timeout: 8_000 })
  // The Drafts list holds exactly that many.
  const list = await (await page.request.get('/api/v1/conversations', { params: { view: 'drafts' } })).json()
  expect(list.total).toBe(after)
})

test('trashing an unread conversation lowers the Inbox number without a reload', async ({ page }) => {
  await page.request.patch('/api/v1/me/settings', { data: { inbox_count: 'all' } })
  const c = await conv(page.request, 'E2E Delete me')
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'unread' } })
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  const before = (await serverCounts(page)).views.inbox.unread
  await expect(nav(page, 'Inbox').locator('.count')).toHaveText(String(before), { timeout: 8_000 })
  const r = await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'trash' } })
  expect(r.ok()).toBeTruthy()
  const after = (await serverCounts(page)).views.inbox.unread
  expect(after).toBe(before - 1)
  if (after) await expect(nav(page, 'Inbox').locator('.count')).toHaveText(String(after), { timeout: 8_000 })
  else await expect(nav(page, 'Inbox').locator('.count')).toHaveCount(0, { timeout: 8_000 })
})
