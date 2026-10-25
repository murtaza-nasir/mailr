// ACT-SURFACES: web.sheet web.settings web.notification web.triage
// Reminders on the web: the bell tray in every theme, the quick-action
// sheet and the one-by-one triage queue on reminders, browser notifications from the live stream
// (reminder.fire), a click that opens the sheet and handles nothing (notification.open), more
// than three as one summary (notification.open_stack), a reminder handled elsewhere closing its
// notification (reminder.sync), the quiet missed notice, and Settings → Notifications
// (notification.subscribe).
// The browser's Notification and EventSource are replaced in the page so the test can watch them;
// the tray's list and the reminder actions are answered by the test (task.complete,
// task.snooze, event.snooze, event.remind_before, reminder.dismiss, reminder.dismiss_all).
import type { Page, Route } from '@playwright/test'
import { expect, shotsDir, test } from './fixtures'

const SHOTS = shotsDir('integration')

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'

interface Item {
  key: string; kind: 'event' | 'task'; uid: string; occurrence: string; minutes: number; title: string; detail: string
  start: string | null; fire_at: string; fired_at: string | null; missed: boolean; state: 'fired' | 'snoozed'; actions: string[]
}

const iso = (ms: number) => new Date(Date.now() + ms).toISOString()
const MIN = 60_000

function event(title: string, inMin: number, extra: Partial<Item> = {}): Item {
  const start = iso(inMin * MIN)
  return {
    key: `e|ev-${title.replace(/\W/g, '')}|${start}|10`, kind: 'event', uid: `ev-${title.replace(/\W/g, '')}`, occurrence: start, minutes: 10,
    title, detail: 'Room 204', start, fire_at: iso(-MIN), fired_at: iso(-MIN), missed: false, state: 'fired', actions: ['snooze', 'dismiss'], ...extra,
  }
}

function task(title: string, dueMin: number, extra: Partial<Item> = {}): Item {
  const due = iso(dueMin * MIN)
  return {
    key: `t|tk-${title.replace(/\W/g, '')}|${due}|0`, kind: 'task', uid: `tk-${title.replace(/\W/g, '')}`, occurrence: due, minutes: 0,
    title, detail: '', start: due, fire_at: iso(-2 * MIN), fired_at: iso(-2 * MIN), missed: false, state: 'fired', actions: ['done', 'snooze', 'dismiss'], ...extra,
  }
}

/** Replaces Notification and EventSource so the test sees every notification and can push live events. */
async function fakeBrowser(page: Page, opts: { worker?: boolean; permission?: NotificationPermission } = {}) {
  await page.addInitScript(({ worker, permission }) => {
    type Note = { title: string; tag?: string; body?: string; silent?: boolean; actions?: { action: string; title: string }[]; closed: boolean; data?: unknown }
    const w = window as unknown as { __notes: Note[]; __es: { onmessage?: (m: { data: string }) => void; onopen?: () => void } | null; __click: (tag: string) => void }
    w.__notes = []
    w.__es = null
    const byTag = new Map<string, { onclick?: () => void; close: () => void }>()
    class FakeNotification {
      static permission: NotificationPermission = permission
      static async requestPermission() { FakeNotification.permission = 'granted'; return 'granted' as NotificationPermission }
      title: string
      onclick: (() => void) | null = null
      onclose: (() => void) | null = null
      note: Note
      constructor(title: string, o: NotificationOptions = {}) {
        this.title = title
        this.note = { title, tag: o.tag, body: o.body, silent: o.silent ?? undefined, closed: false, data: o.data }
        w.__notes.push(this.note)
        if (o.tag) byTag.set(o.tag, { onclick: () => this.onclick?.(), close: () => this.close() })
      }
      close() {
        this.note.closed = true
        this.onclose?.()
      }
    }
    ;(window as unknown as { Notification: unknown }).Notification = FakeNotification
    w.__click = (tag: string) => byTag.get(tag)?.onclick?.()
    class FakeES {
      static OPEN = 1
      readyState = 1
      onmessage: ((m: { data: string }) => void) | null = null
      onopen: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor() {
        w.__es = this as unknown as typeof w.__es
        setTimeout(() => this.onopen?.(), 0)
      }
      close() {}
    }
    ;(window as unknown as { EventSource: unknown }).EventSource = FakeES
    if (worker) {
      // A service worker registration that records showNotification and answers getNotifications.
      const shown: (Note & { close: () => void })[] = []
      const reg = {
        showNotification: async (title: string, o: NotificationOptions & { actions?: { action: string; title: string }[] } = {}) => {
          for (const n of shown) if (n.tag === o.tag && !n.closed) n.closed = true
          const note = { title, tag: o.tag, body: o.body, silent: o.silent ?? undefined, actions: o.actions, closed: false, data: o.data, close() { note.closed = true } }
          shown.push(note)
          w.__notes.push(note)
        },
        getNotifications: async (f: { tag?: string } = {}) => shown.filter((n) => !n.closed && (!f.tag || n.tag === f.tag)),
        pushManager: { getSubscription: async () => null },
      }
      // The worker's messages to the page (a notification's click or button) come through here.
      const listeners: ((e: { data: unknown }) => void)[] = []
      ;(window as unknown as { __swPost: (d: unknown) => void }).__swPost = (d) => listeners.forEach((fn) => fn({ data: d }))
      Object.defineProperty(navigator, 'serviceWorker', {
        configurable: true,
        value: {
          getRegistration: async () => reg, ready: Promise.resolve(reg), register: async () => reg,
          addEventListener: (_t: string, fn: (e: { data: unknown }) => void) => listeners.push(fn), removeEventListener() {},
        },
      })
    }
  }, { worker: !!opts.worker, permission: opts.permission ?? 'granted' })
}

const notes = (page: Page) => page.evaluate(() => (window as unknown as { __notes: { title: string; tag?: string; closed: boolean; silent?: boolean; actions?: { title: string }[] }[] }).__notes)

async function push(page: Page, data: Record<string, unknown>) {
  await page.waitForFunction(() => !!(window as unknown as { __es: unknown }).__es)
  await page.evaluate((d) => (window as unknown as { __es: { onmessage: (m: { data: string }) => void } }).__es.onmessage({ data: JSON.stringify(d) }), data)
}

function sse(r: Item) {
  return { type: 'notification', kind: 'reminder', key: r.key, title: r.title, body: r.detail, start: r.start, item: { kind: r.kind, uid: r.uid, occurrence: r.occurrence }, click: `/calendar/event/${r.uid}/${r.occurrence}`, missed: r.missed, actions: r.actions }
}

/** The tray's list and the reminder actions, answered by the test. */
async function serve(page: Page, initial: Item[]) {
  const state = { items: [...initial], gone: [] as Item[], calls: [] as { what: string; body: unknown }[] }
  const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/api/v1/reminders/active*', (r) => json(r, { items: state.items }))
  await page.route('**/api/v1/reminders/handle', (r) => {
    const b = r.request().postDataJSON() as { keys: string[]; undo?: boolean }
    state.calls.push({ what: 'handle', body: b })
    if (!b.undo) {
      state.gone.push(...state.items.filter((i) => b.keys.includes(i.key)))
      state.items = state.items.filter((i) => !b.keys.includes(i.key))
    } else {
      state.items.push(...state.gone.filter((i) => b.keys.includes(i.key)))
      state.gone = state.gone.filter((i) => !b.keys.includes(i.key))
    }
    return json(r, { ok: true, count: b.keys.length })
  })
  await page.route('**/api/v1/reminders/snooze', (r) => {
    const b = r.request().postDataJSON() as { key: string; until: string }
    state.calls.push({ what: 'snooze', body: b })
    state.items = state.items.filter((i) => i.key !== b.key)
    return json(r, { key: b.key, state: 'snoozed', snoozed_until: b.until })
  })
  await page.route('**/api/v1/reminders/add', (r) => {
    const b = r.request().postDataJSON() as { handle: string[] }
    state.calls.push({ what: 'add', body: b })
    state.items = state.items.filter((i) => !b.handle.includes(i.key))
    return json(r, { ok: true })
  })
  await page.route(/\/api\/v1\/tasks\/tk-[^/?]+$/, (r) => {
    state.calls.push({ what: `task ${r.request().method()}`, body: r.request().postDataJSON() })
    return json(r, { uid: 'tk', summary: 'x', status: 'completed' })
  })
  return state
}

async function theme(page: Page, t: Theme) {
  await page.addInitScript((x) => localStorage.setItem('mailr.uiTheme', x), t)
}

test('web.tray: the bell counts fired and missed reminders; a row opens the reminder sheet; task.complete and event.snooze', async ({ page }) => {
  await fakeBrowser(page)
  const items = [event('Budget review', 25), task('Pay the rent', -120), event('Morning run', -300, { missed: true, fired_at: iso(-60 * MIN) })]
  const state = await serve(page, items)
  await page.goto('/mail/inbox')
  const bell = page.getByTestId('waiting-bell')
  await expect(bell).toHaveAccessibleName(/3 reminders \(1 missed\)/)
  await bell.click()
  const tray = page.getByRole('dialog', { name: 'Waiting for you' })
  await expect(tray.getByText('Missed', { exact: true })).toBeVisible()
  await expect(tray.getByTestId('reminder-row')).toHaveCount(3)
  await expect(tray.getByTestId('reminder-row').filter({ hasText: 'Budget review' })).toContainText(/Starts in 2\d min/)
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/tray-classic.png` })

  // A task: Done completes it and handles the reminder (ACT task.complete).
  await tray.getByTestId('reminder-row').filter({ hasText: 'Pay the rent' }).click()
  const sheet = tray.getByTestId('reminder-sheet')
  await expect(sheet).toContainText('Overdue 2 h')
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/tray-sheet-task.png` })
  await sheet.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByText('Done: Pay the rent')).toBeVisible()
  expect(state.calls.map((c) => c.what)).toEqual(['task PATCH', 'handle'])
  expect(state.calls[0].body).toMatchObject({ completed: true })

  // An event before its start: Snooze offers 5 min, 10 min, At start time (event.snooze).
  await tray.getByTestId('reminder-row').filter({ hasText: 'Budget review' }).click()
  await sheet.getByRole('button', { name: 'Snooze' }).click()
  await expect(sheet.getByRole('group', { name: 'Snooze until' }).getByRole('button')).toHaveCount(3)
  await sheet.getByRole('button', { name: /^In 10 min/ }).click()
  await expect(page.getByText(/^Snoozed until/)).toBeVisible()
  expect(state.calls.at(-1)).toMatchObject({ what: 'snooze', body: { key: items[0].key } })
  await expect(bell).toHaveAccessibleName(/1 reminder \(1 missed\)/)
})

test('web.tray: the snooze sheet numbered options answer their number keys, wherever the focus is (event.snooze)', async ({ page }) => {
  await fakeBrowser(page)
  const items = [event('Budget review', 25)]
  const state = await serve(page, items)
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  const tray = page.getByRole('dialog', { name: 'Waiting for you' })
  await tray.getByTestId('reminder-row').filter({ hasText: 'Budget review' }).click()
  const sheet = tray.getByTestId('reminder-sheet')
  await sheet.getByRole('button', { name: 'Snooze' }).click()
  const second = sheet.getByRole('group', { name: 'Snooze until' }).getByRole('button').nth(1)
  await expect(second).toHaveAttribute('aria-keyshortcuts', '2')
  // The focus is not inside the options (the button that opened them is gone): 2 still picks the second.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('2')
  await expect(page.getByText(/^Snoozed until/)).toBeVisible()
  expect(state.calls.at(-1)).toMatchObject({ what: 'snooze', body: { key: items[0].key } })
})

test('web.tray: Clear all dismisses every reminder in one request; one Undo brings them all back (reminder.dismiss_all)', async ({ page }) => {
  await fakeBrowser(page)
  const items = [event('Budget review', 25), task('Pay the rent', -120), event('Morning run', -300, { missed: true, fired_at: iso(-60 * MIN) })]
  const state = await serve(page, items)
  await page.goto('/mail/inbox')
  const bell = page.getByTestId('waiting-bell')
  await expect(bell).toHaveAccessibleName(/3 reminders \(1 missed\)/)
  await bell.click()
  const tray = page.getByRole('dialog', { name: 'Waiting for you' })
  const clear = tray.getByTestId('reminder-clear-all')
  await expect(clear).toHaveAccessibleName('Clear all 3 reminders')
  // Keyboard: the button takes the focus and Enter runs it.
  await clear.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('3 reminders cleared')).toBeVisible()
  const all = items.map((i) => i.key).sort()
  expect(state.calls).toHaveLength(1)
  expect((state.calls[0].body as { keys: string[] }).keys.toSorted()).toEqual(all)
  await expect(tray.getByTestId('reminder-row')).toHaveCount(0)
  // Nothing to clear: the button is gone.
  await expect(clear).toHaveCount(0)
  await expect(bell).not.toHaveAccessibleName(/reminder/)

  await page.getByRole('button', { name: 'Undo' }).click()
  await expect.poll(() => state.calls.length).toBe(2)
  expect(state.calls[1].body).toMatchObject({ undo: true })
  expect((state.calls[1].body as { keys: string[] }).keys.toSorted()).toEqual(all)
  await expect(bell).toHaveAccessibleName(/3 reminders \(1 missed\)/)
  // The click on the toast closed the tray; open it again.
  if (!(await tray.isVisible())) await bell.click()
  await expect(tray.getByTestId('reminder-row')).toHaveCount(3)
  await expect(clear).toBeVisible()
})

test('web.triage: Review all pages through the reminders; acting moves on; leaving keeps the rest (notification.open_stack, reminder.dismiss, event.remind_before)', async ({ page }) => {
  await fakeBrowser(page)
  const items = [event('Design review', 40), task('Send invoice', -30), event('Old standup', -600, { missed: true })]
  const state = await serve(page, items)
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  await page.getByRole('button', { name: 'Review all' }).click()
  const q = page.getByRole('dialog', { name: 'Review reminders' })
  // Missed first, then fired oldest first.
  await expect(q.getByText('1 of 3')).toBeVisible()
  await expect(q.getByTestId('reminder-sheet')).toContainText('Old standup')
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/triage.png` })
  await q.getByRole('button', { name: 'Next reminder' }).click()
  await expect(q.getByText('2 of 3')).toBeVisible()
  await q.getByRole('button', { name: 'Previous reminder' }).click()
  // In progress: Snooze and Dismiss.
  await q.getByRole('button', { name: 'Dismiss' }).click()
  await expect(q.getByText('1 of 2')).toBeVisible()
  expect(state.calls.at(-1)).toMatchObject({ what: 'handle', body: { keys: [items[2].key] } })
  // An event 40 minutes away: Remind before offers the offsets still ahead.
  await q.getByTestId('reminder-sheet').filter({ hasText: 'Design review' }).or(q.getByTestId('reminder-sheet')).first().waitFor()
  if (!(await q.getByTestId('reminder-sheet').textContent())?.includes('Design review')) await q.getByRole('button', { name: 'Next reminder' }).click()
  await q.getByRole('button', { name: 'Remind before' }).click()
  await q.getByRole('button', { name: /^10 min before/ }).click()
  expect(state.calls.at(-1)).toMatchObject({ what: 'add', body: { kind: 'e', minutes: 10, handle: [items[0].key] } })
  await expect(q.getByText('1 of 1')).toBeVisible()
  // Esc leaves; the task is still in the tray.
  await page.keyboard.press('Escape')
  await expect(q).toHaveCount(0)
  await expect(page.getByTestId('waiting-bell')).toHaveAccessibleName(/1 reminder$/)
})

test('web.triage: "All caught up" comes after the last action\'s own message, not before it (event.snooze)', async ({ page }) => {
  await fakeBrowser(page)
  const items = [event('Design review', 40), event('Team sync', 50)]
  await serve(page, items)
  // The snooze request answers late, as a slow server would.
  await page.route('**/api/v1/reminders/snooze', async (route) => {
    await new Promise((r) => setTimeout(r, 1200))
    await route.fallback()
  })
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  await page.getByRole('button', { name: 'Review all' }).click()
  const q = page.getByRole('dialog', { name: 'Review reminders' })
  for (const n of [2, 1]) {
    await expect(q.getByText(`1 of ${n}`)).toBeVisible()
    await q.getByRole('button', { name: 'Snooze' }).click()
    await q.getByRole('group', { name: 'Snooze until' }).getByRole('button').first().click()
  }
  await expect(q).toHaveCount(0)
  // While the snooze is on its way, nothing says "All caught up" yet.
  await expect(page.getByText('All caught up')).toHaveCount(0)
  await expect(page.getByText(/^Snoozed until/)).toHaveCount(2)
  await expect(page.getByText('All caught up')).toBeVisible()
  // In the stack of messages, "All caught up" is the newest one.
  const texts = await page.locator('.toast').allInnerTexts()
  expect(texts.findIndex((x) => x.includes('All caught up'))).toBeGreaterThan(Math.max(...texts.map((x, n) => (x.includes('Snoozed until') ? n : -1))))
})

test('web.notification: a fired reminder shows a notification tagged with its key; a click opens the reminder sheet and handles nothing; handled elsewhere closes it (reminder.fire, notification.open, reminder.sync)', async ({ page }) => {
  await fakeBrowser(page)
  const r = event('Budget review', 10)
  const state = await serve(page, [])
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('waiting-bell')).not.toHaveAccessibleName(/reminder/)
  state.items = [r]
  await push(page, sse(r))
  await expect.poll(async () => (await notes(page)).filter((n) => n.tag === r.key).length, { timeout: 5000 }).toBe(1)
  const n = (await notes(page)).find((x) => x.tag === r.key)!
  expect(n.title).toBe('Budget review')
  await expect(page.getByTestId('waiting-bell')).toHaveAccessibleName(/1 reminder$/)

  // The click opens the sheet; nothing is handled.
  await page.evaluate((tag) => (window as unknown as { __click: (t: string) => void }).__click(tag), r.key)
  const sheet = page.getByRole('dialog', { name: 'Budget review' })
  await expect(sheet.getByTestId('reminder-sheet')).toBeVisible()
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/notification-sheet.png` })
  expect(state.calls).toEqual([])
  await page.keyboard.press('Escape')

  // Handled on another device: the change arrives and the notification closes.
  state.items = []
  await push(page, { type: 'changes', entity: 'reminder' })
  await expect.poll(async () => (await notes(page)).find((x) => x.tag === r.key)?.closed, { timeout: 5000 }).toBe(true)
  await expect(page.getByTestId('waiting-bell')).not.toHaveAccessibleName(/reminder/)
})

test('web.notification: more than three at once become one summary that opens triage (notification.open_stack)', async ({ page }) => {
  await fakeBrowser(page)
  const rs = [event('A meeting', 10), event('B meeting', 11), task('C task', -5), task('D task', -6)]
  const state = await serve(page, [])
  await page.goto('/mail/inbox')
  state.items = rs
  for (const r of rs) await push(page, sse(r))
  await expect.poll(async () => (await notes(page)).map((n) => n.title), { timeout: 5000 }).toContain('4 reminders')
  expect((await notes(page)).filter((n) => n.tag?.includes('|') && !n.closed)).toHaveLength(0)
  await page.evaluate(() => (window as unknown as { __click: (t: string) => void }).__click('mailr-reminders'))
  await expect(page.getByRole('dialog', { name: 'Review reminders' }).getByText('1 of 4')).toBeVisible()
})

test('web.notification: with a service worker, buttons ride on the notification (task: Done, Snooze 1 h; event: Snooze 5 min, Remind before…, Dismiss) (reminder.fire, event.remind_before)', async ({ page }) => {
  await fakeBrowser(page, { worker: true })
  const t = task('Water the plants', -1)
  const e = event('Dentist', 30)
  const state = await serve(page, [])
  await page.goto('/mail/inbox')
  state.items = [t, e]
  await push(page, sse(t))
  await push(page, sse(e))
  await expect.poll(async () => (await notes(page)).length, { timeout: 5000 }).toBe(2)
  const shown = await notes(page)
  expect(shown.find((n) => n.tag === t.key)!.actions!.map((a) => a.title)).toEqual(['Done', 'Snooze 1 h'])
  expect(shown.find((n) => n.tag === e.key)!.actions!.map((a) => a.title)).toEqual(['Snooze 5 min', 'Remind before…', 'Dismiss'])
  // Acting in the tray closes that notification at once.
  await page.getByTestId('waiting-bell').click()
  await page.getByTestId('reminder-row').filter({ hasText: 'Water the plants' }).click()
  await page.getByTestId('reminder-sheet').getByRole('button', { name: 'Dismiss' }).click()
  await expect.poll(async () => (await notes(page)).find((n) => n.tag === t.key)?.closed).toBe(true)
  expect((await notes(page)).find((n) => n.tag === e.key)?.closed).toBe(false)
})

test('web.notification: Remind before… opens the sheet on its offsets; picking one handles the reminder (event.remind_before)', async ({ page }) => {
  await fakeBrowser(page, { worker: true })
  const e = event('Quarterly review', 40)
  const state = await serve(page, [e])
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('waiting-bell')).toHaveAccessibleName(/1 reminder$/)
  // The worker passes the button on to the open tab; nothing is handled yet.
  await page.evaluate((key) => (window as unknown as { __swPost: (d: unknown) => void }).__swPost({ type: 'mailr:open-reminder', key, expand: 'before' }), e.key)
  const sheet = page.getByRole('dialog', { name: 'Quarterly review' })
  const offsets = sheet.getByRole('group', { name: 'Remind me before it starts' })
  await expect(offsets).toBeVisible()
  expect(state.calls).toEqual([])
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/notification-remind-before.png` })
  await offsets.getByRole('button', { name: /^15 min before/ }).click()
  expect(state.calls.at(-1)).toMatchObject({ what: 'add', body: { kind: 'e', minutes: 15, handle: [e.key] } })
  // From a closed tab the link carries the same request (?reminder=<key>&expand=before).
  state.items = [e]
  await page.goto(`/mail/inbox?reminder=${encodeURIComponent(e.key)}&expand=before`)
  await expect(page.getByRole('dialog', { name: 'Quarterly review' }).getByRole('group', { name: 'Remind me before it starts' })).toBeVisible()
})

test('web.triage: the open queue stays live: handled elsewhere drops out, a new reminder joins at the end (reminder.sync, reminder.fire)', async ({ page }) => {
  await fakeBrowser(page)
  const a = task('Book flights', -20)
  const b = event('Team lunch', 50)
  const state = await serve(page, [a, b])
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  await page.getByRole('button', { name: 'Review all' }).click()
  const q = page.getByRole('dialog', { name: 'Review reminders' })
  await expect(q.getByText('1 of 2')).toBeVisible()
  // ACT reminder.sync: the task is done on the phone; the change arrives and the queue shrinks.
  state.items = [b]
  await push(page, { type: 'changes', entity: 'reminder' })
  await expect(q.getByText('1 of 1')).toBeVisible()
  await expect(q.getByTestId('reminder-sheet')).toContainText('Team lunch')
  // ACT reminder.fire: a reminder that fires now joins the queue at the end.
  const c = task('Call the bank', -1, { fired_at: new Date(Date.now() + 1000).toISOString() })
  state.items = [b, c]
  await push(page, sse(c))
  await expect(q.getByText('1 of 2')).toBeVisible()
  await q.getByRole('button', { name: 'Next reminder' }).click()
  await expect(q.getByTestId('reminder-sheet')).toContainText('Call the bank')
})

test('web.notification: missed reminders are listed quietly with one notice on load', async ({ page }) => {
  await fakeBrowser(page)
  await serve(page, [event('Yoga', -200, { missed: true }), task('Call mum', -400, { missed: true })])
  await page.goto('/mail/inbox')
  await expect.poll(async () => (await notes(page)).map((n) => [n.title, n.silent]), { timeout: 5000 }).toEqual([['2 missed reminders', true]])
  await page.reload()
  await expect(page.getByTestId('waiting-bell')).toHaveAccessibleName(/2 reminders \(2 missed\)/)
  await page.waitForTimeout(500)
  expect(await notes(page)).toEqual([])
})

test('web.settings: Settings → Notifications switches and the test notification (notification.subscribe)', async ({ page }) => {
  await fakeBrowser(page, { permission: 'default' })
  await page.goto('/settings/notifications')
  await expect(page.getByTestId('notif-permission')).toContainText('not requested yet')
  // The switch is the user gesture that asks for permission.
  const desktop = page.getByRole('switch', { name: 'Desktop notifications' })
  await desktop.click()
  await expect(desktop).toHaveAttribute('aria-checked', 'true')
  const tasks = page.getByRole('switch', { name: 'Task reminders' })
  await expect(tasks).toHaveAttribute('aria-checked', 'true')
  await tasks.click()
  await expect(tasks).toHaveAttribute('aria-checked', 'false')
  await expect.poll(async () => (await (await page.request.get('/api/v1/reminders/settings')).json()).tasks).toBe(false)
  await page.getByRole('button', { name: 'Send test notification' }).click()
  await expect.poll(async () => (await notes(page)).map((n) => n.title)).toContain('Reminders work on this computer')
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/settings-notifications.png`, fullPage: true })
  await tasks.click()
  await expect.poll(async () => (await (await page.request.get('/api/v1/reminders/settings')).json()).tasks).toBe(true)
})

test('web.settings: one notification choice per kind for every device (notification.choose)', async ({ page }) => {
  await fakeBrowser(page)
  await page.goto('/settings/notifications')
  const what = page.locator('#what')
  // The account's mail level: the same for this browser, other browsers and the phone app.
  const mail = what.getByTestId('notify-mail')
  await expect(mail).toHaveValue('important')
  await mail.selectOption('all')
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.mail_notifications).toBe('all')
  const approvals = what.getByRole('switch', { name: 'Approvals and failed sends' })
  await expect(approvals).toHaveAttribute('aria-checked', 'true')
  await approvals.click()
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.notify_approvals).toBe(false)
  await expect(what.getByRole('switch', { name: 'Mail account password required' })).toHaveAttribute('aria-checked', 'true')
  // Mail → Reading keeps no second mail switch.
  await page.goto('/settings/mail/reading')
  await expect(page.getByRole('heading', { name: 'Reading' })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'Mail notifications off' })).toHaveCount(0)
})

test('web.tray: the one-time banner asks for permission from its own click', async ({ page }) => {
  await fakeBrowser(page, { permission: 'default' })
  await serve(page, [task('File taxes', -10)])
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  const banner = page.getByRole('region', { name: 'Get reminders on this computer' })
  await expect(banner).toBeVisible()
  await banner.getByRole('button', { name: 'Turn on' }).click()
  await expect(page.getByText('Reminders will show on this computer.')).toBeVisible()
  await expect(banner).toHaveCount(0)
})

for (const t of ['office', 'focus', 'desk', 'cards'] as Theme[]) {
  test(`web.tray in ${t}: the bell sits in the top bar and opens the tray`, async ({ page }) => {
    await fakeBrowser(page)
    await theme(page, t)
    await serve(page, [event('Budget review', 25), task('Pay the rent', -120)])
    await page.goto('/mail/inbox')
    const bell = page.getByTestId('waiting-bell')
    await expect(bell).toHaveAccessibleName(/2 reminders$/)
    await bell.click()
    await expect(page.getByRole('dialog', { name: 'Waiting for you' }).getByTestId('reminder-row')).toHaveCount(2)
    await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/tray-${t}.png` })
  })
}

test('web.tray on a phone: the sheet and triage fit the screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await fakeBrowser(page)
  await serve(page, [event('Budget review', 25), task('Pay the rent', -120)])
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/tray-phone.png` })
  await page.getByRole('button', { name: 'Review all' }).click()
  const q = page.getByRole('dialog', { name: 'Review reminders' })
  const box = await q.boundingBox()
  expect(box!.width).toBeLessThanOrEqual(390)
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/triage-phone.png` })
})
