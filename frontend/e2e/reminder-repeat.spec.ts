// ACT-SURFACES: web.settings web.notification
// Remind again on the web (reminder.repeat). The server repeats a reminder nobody
// acted on; each repeat arrives like the first fire, with `again` set and the same key. The web
// shows it with the same tag, so it replaces the notification and alerts again; handled anywhere
// closes it; with desktop notifications off, the repeat replaces the in-app toast. Settings →
// Notifications → Remind again saves PUT /reminders/settings {repeat}. The browser's Notification,
// EventSource and service worker are replaced in the page; page.clock moves time on.
import type { Page, Route } from '@playwright/test'
import { expect, test } from './fixtures'

const MIN = 60_000

interface Note { title: string; tag?: string; silent?: boolean; renotify?: boolean; closed: boolean }

async function fakeBrowser(page: Page, opts: { permission: NotificationPermission; worker?: boolean }) {
  await page.addInitScript(({ permission, worker }) => {
    const w = window as unknown as { __notes: Note[]; __es: unknown }
    w.__notes = []
    w.__es = null
    class FakeNotification {
      static permission: NotificationPermission = permission
      static async requestPermission() { return FakeNotification.permission }
      note: Note
      onclick: (() => void) | null = null
      onclose: (() => void) | null = null
      constructor(title: string, o: NotificationOptions = {}) {
        this.note = { title, tag: o.tag, silent: o.silent ?? undefined, closed: false }
        w.__notes.push(this.note)
      }
      close() { this.note.closed = true; this.onclose?.() }
    }
    ;(window as unknown as { Notification: unknown }).Notification = FakeNotification
    class FakeES {
      static OPEN = 1
      readyState = 1
      onmessage: ((m: { data: string }) => void) | null = null
      onopen: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor() { w.__es = this; setTimeout(() => this.onopen?.(), 0) }
      close() {}
    }
    ;(window as unknown as { EventSource: unknown }).EventSource = FakeES
    if (worker) {
      const shown: (Note & { close: () => void })[] = []
      const reg = {
        showNotification: async (title: string, o: NotificationOptions & { renotify?: boolean } = {}) => {
          for (const n of shown) if (n.tag === o.tag) n.closed = true
          const note = { title, tag: o.tag, silent: o.silent ?? undefined, renotify: o.renotify, closed: false, close() { note.closed = true } }
          shown.push(note)
          w.__notes.push(note)
        },
        getNotifications: async (f: { tag?: string } = {}) => shown.filter((n) => !n.closed && (!f.tag || n.tag === f.tag)),
        pushManager: { getSubscription: async () => null },
      }
      Object.defineProperty(navigator, 'serviceWorker', {
        configurable: true,
        value: { getRegistration: async () => reg, ready: Promise.resolve(reg), register: async () => reg, addEventListener() {}, removeEventListener() {} },
      })
    }
  }, { permission: opts.permission, worker: !!opts.worker })
}

const notes = (page: Page) => page.evaluate(() => (window as unknown as { __notes: Note[] }).__notes)

async function push(page: Page, data: Record<string, unknown>) {
  await page.waitForFunction(() => !!(window as unknown as { __es: unknown }).__es)
  await page.evaluate((d) => (window as unknown as { __es: { onmessage: (m: { data: string }) => void } }).__es.onmessage({ data: JSON.stringify(d) }), data)
  await page.clock.runFor(1500) // the host gathers a burst for 1.2 s
}

function reminder(title: string, inMin: number) {
  const start = new Date(Date.now() + inMin * MIN).toISOString()
  const uid = `ev-${title.replace(/\W/g, '')}`
  const item = {
    key: `e|${uid}|${start}|10`, kind: 'event', uid, occurrence: start, minutes: 10, title, detail: 'Room 204', start,
    fire_at: new Date(Date.now() - MIN).toISOString(), fired_at: new Date(Date.now() - MIN).toISOString(), missed: false, state: 'fired', actions: ['snooze', 'dismiss'],
  }
  const sse = (again = false) => ({
    type: 'notification', kind: 'reminder', key: item.key, title, body: item.detail, start, item: { kind: 'event', uid, occurrence: start },
    click: `/calendar/event/${uid}/${start}`, missed: false, actions: item.actions, ...(again ? { again: true } : {}),
  })
  return { item, sse }
}

async function serve(page: Page, items: unknown[]) {
  const state = { items }
  await page.route('**/api/v1/reminders/active*', (r: Route) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: state.items }) }))
  return state
}

test('reminder.repeat: Settings → Notifications → Remind again saves the server setting, worded as on the phone', async ({ page }) => {
  await page.goto('/settings/notifications')
  const pick = page.getByRole('combobox', { name: 'Remind again' })
  await expect(pick).toHaveValue('0')
  await expect(page.getByText("Off. Reminders for an event stop 15 minutes after it starts; missed reminders don't repeat.")).toBeVisible()
  expect(await pick.locator('option').allTextContents()).toEqual(['Off', 'Every 5 minutes', 'Every 10 minutes', 'Every 15 minutes', 'Every 30 minutes'])
  await pick.selectOption('15')
  await expect(page.getByText('Every 15 minutes, up to three times, until you act on it.', { exact: false })).toBeVisible()
  await expect.poll(async () => (await (await page.request.get('/api/v1/reminders/settings')).json()).repeat)
    .toEqual({ enabled: true, every_min: 15, max: 3 })
  await page.reload()
  await expect(page.getByRole('combobox', { name: 'Remind again' })).toHaveValue('15')
  await page.getByRole('combobox', { name: 'Remind again' }).selectOption('0')
  await expect.poll(async () => (await (await page.request.get('/api/v1/reminders/settings')).json()).repeat)
    .toEqual({ enabled: false, every_min: 15, max: 3 })
})

test('reminder.repeat: each repeat replaces the notification and alerts again; handled elsewhere closes it', async ({ page }) => {
  await page.clock.install()
  await fakeBrowser(page, { permission: 'granted', worker: true })
  const r = reminder('Budget review', 10)
  const state = await serve(page, [])
  await page.goto('/mail/inbox')
  state.items = [r.item]
  await push(page, r.sse())
  await expect.poll(async () => (await notes(page)).filter((n) => n.tag === r.item.key && !n.closed).length).toBe(1)
  // Three repeats, five minutes apart: still one notification for the key, never silent, renotify on.
  for (let i = 0; i < 3; i++) {
    await page.clock.fastForward(5 * MIN)
    await push(page, r.sse(true))
  }
  await expect.poll(async () => (await notes(page)).filter((n) => n.tag === r.item.key).length).toBe(4)
  const mine = (await notes(page)).filter((n) => n.tag === r.item.key)
  expect(mine.filter((n) => !n.closed)).toHaveLength(1)
  expect(mine.every((n) => n.renotify === true && !n.silent)).toBe(true)
  // No summary: a repeat of a showing reminder does not count twice.
  expect((await notes(page)).map((n) => n.title)).not.toContain('4 reminders')
  // Handled on the phone: the change arrives, the tray empties and the notification closes.
  state.items = []
  await push(page, { type: 'changes', entity: 'reminder' })
  await page.clock.runFor(16_000)
  await expect.poll(async () => (await notes(page)).filter((n) => n.tag === r.item.key && !n.closed).length, { timeout: 5000 }).toBe(0)
})

test('reminder.repeat: with desktop notifications off, a repeat replaces the in-app toast', async ({ page }) => {
  await page.clock.install()
  await fakeBrowser(page, { permission: 'default' })
  const r = reminder('Design review', 10)
  await serve(page, [r.item])
  await page.goto('/mail/inbox')
  await push(page, r.sse())
  const toasts = page.locator('.toast').filter({ hasText: 'Design review' })
  await expect(toasts).toHaveCount(1)
  // A repeat while the first toast still shows (a toast lasts 15 s): one toast, not two.
  await page.clock.runFor(5_000)
  await push(page, r.sse(true))
  await expect(toasts).toHaveCount(1)
  await expect(toasts.getByRole('button', { name: 'Open' })).toBeVisible()
})
