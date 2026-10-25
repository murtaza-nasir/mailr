// Lists follow changes made elsewhere (another device, the assistant, an approved request)
// without a reload: through the live stream, and, when a proxy breaks the stream, through the
// change-cursor poll (api/events.ts watchdog).
import type { Browser, Page } from '@playwright/test'
import { conv, expect, test, type Seed } from './fixtures'

async function archiveElsewhere(browser: Browser, seeded: Seed, subject: string) {
  const other = await browser.newContext({ baseURL: process.env.E2E_BASE ?? 'http://localhost:5175' })
  const r = await other.request.post('/api/v1/auth/login', { data: { email: seeded.email, password: seeded.password } })
  const csrf = (await r.json()).csrf
  const c = await conv(other.request, subject)
  const a = await other.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'archive' }, headers: { 'X-CSRF-Token': csrf } })
  expect(a.ok()).toBeTruthy()
  await other.close()
}

const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject })

test('a change made elsewhere leaves the open list (live stream)', async ({ page, browser, seeded }) => {
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Archive me')).toHaveCount(1)
  await archiveElsewhere(browser, seeded, 'E2E Archive me')
  await expect(row(page, 'E2E Archive me')).toHaveCount(0, { timeout: 8_000 })
})

// The watchdog looks every 10 s and polls when its last poll is more than 20 s old (api/events.ts):
// the page's clock is moved on instead of waiting for it. The polls themselves are real requests.
test('with the live stream broken, the list still catches up (poll)', async ({ page, browser, seeded }) => {
  await page.clock.install()
  await page.route('**/api/v1/events', (r) => r.fulfill({ status: 502, body: 'Bad Gateway' }))
  // The first poll asks without a cursor (since=), the next ones with it.
  const poll = (first: boolean) => page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/changes' &&
    !new URL(r.url()).searchParams.get('since') === first)
  const first = poll(true)
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Delete me')).toHaveCount(1)
  // The first poll sets the cursor.
  await page.clock.runFor(10_500)
  await first
  await archiveElsewhere(browser, seeded, 'E2E Delete me')
  // A later poll brings the change.
  const next = poll(false)
  await page.clock.runFor(30_000)
  expect((await next).ok()).toBeTruthy()
  await expect(row(page, 'E2E Delete me')).toHaveCount(0)
})
