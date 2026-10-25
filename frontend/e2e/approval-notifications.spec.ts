// ACT-SURFACES: web.notification
// approval.sync on the web: an approval decided anywhere closes its browser notification. The
// notifications the service worker showed (Web Push, tag a<id>) are kept by a stand-in
// registration so the test can see them; the event stream is the real one, so the server's
// approval.decided event is what closes them. One decision closes one notification; Allow all
// closes the whole batch with one event; the other notifications stay.
import { request as pwRequest, type APIRequestContext, type Page } from '@playwright/test'
import { expect, test } from './fixtures'

async function labelRequests(request: APIRequestContext, names: string[]): Promise<number[]> {
  const tok = await (await request.post('/api/v1/tokens', { data: { name: 'claude-code', scopes: ['read', 'label'] } })).json()
  const agent = await pwRequest.newContext({
    baseURL: process.env.E2E_BASE ?? 'http://localhost:5173',
    extraHTTPHeaders: { Authorization: `Bearer ${tok.token as string}` },
  })
  const ids: number[] = []
  for (const name of names) {
    const r = await agent.post('/api/v1/labels', { data: { name } })
    expect(r.status()).toBe(202)
    ids.push((await r.json()).approval.id as number)
  }
  await agent.dispose()
  return ids
}

/** A service worker registration that keeps what it shows (the worker's notifications), and a
 *  granted Notification permission. The event stream stays the browser's own. */
async function fakeWorker(page: Page) {
  await page.addInitScript(() => {
    type Shown = { title: string; tag?: string; closed: boolean; close: () => void }
    const shown: Shown[] = []
    ;(window as unknown as { __shown: Shown[] }).__shown = shown
    class FakeNotification {
      static permission: NotificationPermission = 'granted'
      static async requestPermission() { return 'granted' as NotificationPermission }
      close() {}
    }
    ;(window as unknown as { Notification: unknown }).Notification = FakeNotification
    const reg = {
      showNotification: async (title: string, o: NotificationOptions = {}) => {
        const note: Shown = { title, tag: o.tag, closed: false, close() { note.closed = true } }
        shown.push(note)
      },
      getNotifications: async () => shown.filter((n) => !n.closed),
      pushManager: { getSubscription: async () => null },
    }
    // The browser's own event stream, watched only to know when it is open.
    const Native = window.EventSource
    ;(window as unknown as { EventSource: unknown }).EventSource = class extends Native {
      constructor(url: string | URL, init?: EventSourceInit) {
        super(url, init)
        this.addEventListener('open', () => { (window as unknown as { __streamOpen: boolean }).__streamOpen = true })
      }
    }
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        getRegistration: async () => reg, ready: Promise.resolve(reg), register: async () => reg,
        addEventListener() {}, removeEventListener() {},
      },
    })
  })
}

/** What a Web Push for each approval left on the screen (public/sw.js showMail: tag a<id>). */
async function showPushed(page: Page, ids: (number | string)[], others: string[] = []) {
  await page.evaluate(async ({ ids, others }) => {
    const reg = await navigator.serviceWorker.getRegistration()
    for (const id of ids) await reg!.showNotification(`Approval ${id}`, { tag: `a${id}` })
    for (const tag of others) await reg!.showNotification(tag, { tag })
  }, { ids, others })
}

const open = (page: Page) =>
  page.evaluate(() => (window as unknown as { __shown: { tag?: string; closed: boolean }[] }).__shown.filter((n) => !n.closed).map((n) => n.tag))

test('approval.sync: a decision made elsewhere closes that approval notification', async ({ page }) => {
  await fakeWorker(page)
  const [one, two] = await labelRequests(page.request, ['E2E Notify one', 'E2E Notify two'])
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('waiting-bell')).toBeVisible()
  await page.waitForFunction(() => (window as unknown as { __streamOpen?: boolean }).__streamOpen === true)
  await showPushed(page, [one, two], ['c12345'])
  // The decision comes from somewhere other than this page (another device, the phone, an agent link).
  const r = await page.request.post(`/api/v1/approvals/${one}/decide`, { data: { decision: 'deny' } })
  expect(r.status()).toBe(200)
  await expect.poll(() => open(page)).toEqual([`a${two}`, 'c12345'])
})

test('approval.sync: Allow all closes every notification of the batch at once', async ({ page }) => {
  await fakeWorker(page)
  const ids = await labelRequests(page.request, ['E2E Batch A', 'E2E Batch B', 'E2E Batch C'])
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('waiting-bell')).toBeVisible()
  await page.waitForFunction(() => (window as unknown as { __streamOpen?: boolean }).__streamOpen === true)
  await showPushed(page, ids, ['c777'])
  const r = await page.request.post('/api/v1/approvals/decide-many', { data: { ids, decision: 'approve' } })
  expect(r.status()).toBe(200)
  await expect.poll(() => open(page)).toEqual(['c777'])
})
