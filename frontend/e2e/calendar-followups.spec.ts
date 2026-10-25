// ACT-SURFACES: web.sheet web.editor web.calendar web.tasks web.pane
// Follow-ups across the Calendar and Tasks apps: Ask assistant with the record attached from
// the event detail, the task pane and the selection bar, through the ask capture (agent.ask_about), and the calendar gaps
// closed after the first build.
import type { Page, Request } from '@playwright/test'
import { apiSettled, connectCalendar, expect, test } from './fixtures'
import { at, dayOf, drag, hhmm, isoDay, openIn, seedWeek, slotPoint } from './calendar-data'

const event = (page: Page, name: string) => page.getByTestId('cal-event').filter({ hasText: name }).first()

/** Answers every chat turn with a short canned reply and keeps the request bodies. */
async function fakeChat(page: Page): Promise<Record<string, unknown>[]> {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    bodies.push((route.request() as Request).postDataJSON() as Record<string, unknown>)
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: 'event: session\ndata: {"session_id":"e2e-ask"}\n\nevent: final\ndata: {"content":"Noted."}\n\nevent: done\ndata: {}\n\n',
    })
  })
  await page.route('**/api/v1/agent/sessions/e2e-ask', (route) => route.fulfill({ json: { id: 'e2e-ask', turns: [] } }))
  return bodies
}

test('Ask assistant from the event detail attaches the occurrence', async ({ page, seeded }) => {
  const ev = await seedWeek(page.request, seeded)
  const bodies = await fakeChat(page)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await event(page, 'Budget review').click()
  // agent.ask_about: the capture opens with the occurrence as its chip; Send goes to the chat.
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Ask Ambrose' }).click()
  const ask = page.getByTestId('ask-capture')
  await expect(ask.getByTestId('ask-chip')).toContainText('Budget review')
  await ask.getByRole('textbox', { name: 'Your words for Ambrose' }).fill('Prepare an agenda')
  await ask.getByRole('button', { name: 'Send' }).click()
  const chip = page.getByTestId('chat-about')
  await expect(chip).toContainText('Budget review')
  await expect(page.getByText('Noted.')).toBeVisible()
  expect(bodies[0].message).toBe('Prepare an agenda')
  const ctx = bodies[0].context as Record<string, unknown>
  expect(ctx.focused_event).toEqual({ uid: ev.budget.uid, occurrence: expect.any(String) })
  // Detach drops it from the next turn.
  await chip.getByRole('button', { name: 'Detach' }).click()
  await page.getByRole('textbox', { name: 'Message Ambrose' }).fill('And now?')
  await page.keyboard.press('Enter')
  await expect.poll(() => bodies.length).toBe(2)
  expect((bodies[1].context as Record<string, unknown>).focused_event).toBeUndefined()
})

test('Ask assistant from the task pane and from the selection bar', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const mk = async (summary: string) => (await (await page.request.post('/api/v1/tasks', { data: { summary, due: dayOf(0) } })).json()) as { uid: string }
  const a = await mk('E2E Ask one')
  const b = await mk('E2E Ask two')
  const bodies = await fakeChat(page)
  await page.goto(`/tasks/all/task/${encodeURIComponent(a.uid)}`)
  const pane = page.getByTestId('task-pane')
  await pane.getByRole('button', { name: 'Ask Ambrose' }).click()
  await expect(page.getByTestId('ask-capture').getByTestId('ask-chip')).toContainText('E2E Ask one')
  await page.getByTestId('ask-capture').getByRole('button', { name: 'Open chat' }).click()
  await expect(page.getByTestId('chat-about')).toContainText('E2E Ask one')
  await page.getByRole('button', { name: 'Split this task into steps' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect((bodies[0].context as Record<string, unknown>).focused_task).toBe(a.uid)

  // The selection bar: several tasks go as ui.tasks.selected.
  await page.goto('/tasks/all')
  await expect(page.getByTestId('task-row').filter({ hasText: 'E2E Ask two' })).toBeVisible()
  await page.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => undefined)
  for (let i = 0; i < 2; i++) {
    await page.keyboard.press('j')
    await page.keyboard.press('x')
  }
  const bulk = page.getByTestId('tasks-bulk')
  await expect(bulk).toBeVisible()
  await bulk.getByRole('button', { name: 'Ask Ambrose' }).click()
  await expect(page.getByTestId('ask-capture').getByTestId('ask-chip')).toContainText('2 selected tasks')
  await page.getByTestId('ask-capture').getByRole('textbox', { name: 'Your words for Ambrose' }).fill('Sort these')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('chat-about')).toContainText('2 selected tasks')
  await expect.poll(() => bodies.length).toBe(2)
  const ui = (bodies[1].context as { ui: { tasks: { selected: string[] } } }).ui
  expect(ui.tasks.selected.sort()).toEqual([a.uid, b.uid].sort())
})

test('a task chip dragged on the grid moves its due (ACT task.reschedule)', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const r = await page.request.post('/api/v1/tasks', { data: { summary: 'Chip to move', due: at(3, 11) } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const uid = ((await r.json()) as { uid: string }).uid
  const dueOf = async () => {
    const j = await (await page.request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
    return ((Array.isArray(j) ? j : j.items) as { uid: string; due: string }[]).find((t) => t.uid === uid)!.due
  }
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await page.getByText('Show tasks on calendar').click()
  const chip = page.getByTestId('cal-task').filter({ hasText: 'Chip to move' })
  await expect(chip).toBeVisible()
  // To Friday 12:00 in the grid.
  const to = await slotPoint(page, dayOf(4), 12)
  const b = (await chip.locator('.cal-task-title').boundingBox())!
  await drag(page, { x: b.x + 20, y: b.y + b.height / 2 }, { x: to.x, y: to.y + (b.height / 2) + 3 })
  await expect(page.locator('.toast').filter({ hasText: 'Due' })).toBeVisible()
  await expect.poll(async () => { const d = await dueOf(); return `${isoDay(new Date(d))} ${hhmm(d)}` }).toBe(`${dayOf(4)} 12:00`)
  // Onto the all-day strip: a date only. The chip is where the page drew it after the move, once
  // the page has reloaded the tasks.
  await apiSettled(page)
  await expect(chip).toBeVisible()
  const b2 = (await chip.locator('.cal-task-title').boundingBox())!
  const strip = (await page.locator('.cal-strip').boundingBox())!
  await drag(page, { x: b2.x + 20, y: b2.y + b2.height / 2 }, { x: b2.x + 20, y: strip.y + strip.height - 4 })
  await expect.poll(dueOf).toBe(dayOf(4))
})

test('The series question: All on a series with a changed occurrence says it keeps its own time', async ({ page, seeded }) => {
  const ev = await seedWeek(page.request, seeded)
  // This week's Team sync moved to 11:00 (an override).
  const occ = new Date(at(0, 9)).toISOString().replace('.000Z', '+00:00')
  const p = await page.request.patch(`/api/v1/calendar/events/${encodeURIComponent(ev.sync.uid)}`, { data: { start: at(0, 11), end: at(0, 12), scope: 'this', occurrence: occ } })
  expect(p.ok(), await p.text()).toBeTruthy()
  await openIn(page, 'classic', `/calendar/week/${dayOf(0)}`)
  await expect(event(page, 'Team sync')).toBeVisible()
  // Next week's occurrence, dragged an hour later.
  await page.getByRole('button', { name: 'Next period (j)' }).click()
  const from = await slotPoint(page, dayOf(7), 9)
  const b = (await event(page, 'Team sync').boundingBox())!
  const hourH = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.cal-tg')!).getPropertyValue('--hour-h')))
  await drag(page, { x: b.x + b.width / 2, y: b.y + 8 }, { x: b.x + b.width / 2, y: b.y + 8 + hourH })
  expect(from.x).toBeGreaterThan(0)
  const dlg = page.getByRole('dialog', { name: 'Move recurring event' })
  await expect(dlg).toBeVisible()
  await expect(dlg.getByText('will keep its own time')).toHaveCount(0)
  await dlg.getByText('All events').click()
  await expect(dlg.getByText('1 changed occurrence will keep its own time.')).toBeVisible()
  await dlg.getByRole('button', { name: 'Cancel' }).click()
})

test('event detail: Snooze and Remind before while its reminder is up (ACT event.snooze, ACT event.remind_before)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const start = new Date(Date.now() + 40 * 60_000)
  start.setSeconds(0, 0)
  const end = new Date(start.getTime() + 30 * 60_000)
  const created = await (await page.request.post('/api/v1/calendar/events', { data: { summary: 'Reminded meeting', start: start.toISOString(), end: end.toISOString() } })).json() as { uid: string }
  const occ = start.toISOString().replace('.000Z', '+00:00')
  const item = {
    key: `e|${created.uid}|${occ}|30`, kind: 'event', uid: created.uid, occurrence: occ, minutes: 30, title: 'Reminded meeting', detail: 'in 40 min',
    start: start.toISOString(), fire_at: new Date().toISOString(), fired_at: new Date().toISOString(), missed: false, state: 'fired', actions: ['snooze', 'dismiss'],
  }
  let items = [item]
  await page.route('**/api/v1/reminders/active*', (r) => r.fulfill({ json: { items } }))
  const posts: { url: string; body: Record<string, unknown> }[] = []
  for (const path of ['snooze', 'add', 'handle']) {
    await page.route(`**/api/v1/reminders/${path}`, async (r) => {
      posts.push({ url: path, body: r.request().postDataJSON() as Record<string, unknown> })
      items = []
      await r.fulfill({ json: { ok: true } })
    })
  }
  await openIn(page, 'classic', `/calendar/day/${isoDay(start)}`)
  await event(page, 'Reminded meeting').click()
  const box = page.getByTestId('cal-detail-reminder')
  await expect(box).toContainText('Reminder: in 40 min')
  await box.getByRole('button', { name: 'Snooze 10 min' }).click()
  await expect.poll(() => posts.map((p) => p.url)).toEqual(['snooze'])
  expect(posts[0].body.key).toBe(item.key)
  await expect(box).toHaveCount(0)

  // Up again: remind 15 minutes before instead.
  items = [item]
  await page.keyboard.press('Escape')
  await page.reload()
  await event(page, 'Reminded meeting').click()
  await page.getByTestId('cal-detail-reminder').getByRole('button', { name: '15 min before' }).click()
  await expect.poll(() => posts.map((p) => p.url)).toEqual(['snooze', 'add'])
  expect(posts[1].body).toMatchObject({ kind: 'e', uid: created.uid, minutes: 15, handle: [item.key] })
})

test('editor: From picks the organizer identity; a guest outside the organisation is named (ACT event.create)', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const ids = (await (await page.request.get('/api/v1/identities')).json()) as { id: number; connector_id: number | null }[]
  const second = await (await page.request.post('/api/v1/identities', { data: { email: 'office@example.org', name: 'Office', connector_id: ids[0].connector_id } })).json() as { id: number }
  const sent: Record<string, unknown>[] = []
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/api/v1/calendar/events')) sent.push(r.postDataJSON() as Record<string, unknown>) })
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  await page.keyboard.press('c')
  await page.getByRole('textbox', { name: 'Event, in one line' }).fill('Office hours tomorrow 2pm')
  await page.keyboard.press('Control+Enter')
  const ed = page.getByTestId('cal-editor')
  const from = ed.getByRole('combobox', { name: 'From' })
  await expect(from).toHaveValue(String(ids[0].id))
  await from.selectOption(String(second.id))
  // The guest field: a guest from another domain is outside the organisation.
  const add = ed.getByRole('combobox', { name: 'Add guests' })
  await add.fill('dana@elsewhere.example')
  await add.press('Enter')
  await expect(ed.getByTestId('cal-guests-outside')).toHaveText('dana@elsewhere.example is outside your organisation')
  await ed.getByTestId('cal-editor-save').click()
  await page.getByRole('dialog', { name: 'Send invitation to 1 new guest?' }).getByRole('button', { name: "Don't send" }).click()
  await expect(ed).toHaveCount(0)
  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0].identity_id).toBe(second.id)
})

test('RSVP to a series asks which occurrences (ACT event.rsvp)', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  // A weekly invitation from Dana, written on the dev Radicale as her server would deliver it.
  const cols = (await (await page.request.get('/api/v1/calendar/calendars')).json()) as { url: string; events?: boolean }[] | { items: { url: string; events?: boolean }[] }
  const list = Array.isArray(cols) ? cols : cols.items
  const href = list.find((c) => c.events !== false)!.url
  const me = ((await (await page.request.get('/api/v1/identities')).json()) as { email: string }[])[0].email
  const start = new Date(at(2, 15)).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
  const end = new Date(at(2, 16)).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
  const uid = `series-${Date.now()}@e2e`
  const user = seeded.caldav!.username
  const put = await fetch(new URL(`${uid}.ics`, href.endsWith('/') ? href : `${href}/`).toString(), {
    method: 'PUT', headers: { Authorization: `Basic ${Buffer.from(`${user}:x`).toString('base64')}`, 'Content-Type': 'text/calendar' },
    body: ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//e2e//EN', 'BEGIN:VEVENT', `UID:${uid}`, `DTSTAMP:${start}`, `DTSTART:${start}`, `DTEND:${end}`,
      'RRULE:FREQ=WEEKLY;COUNT=4', 'SUMMARY:Weekly with Dana', 'ORGANIZER;CN=Dana:mailto:dana-0@example.org',
      `ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:${me}`, 'END:VEVENT', 'END:VCALENDAR', ''].join('\r\n'),
  })
  expect(put.status).toBeLessThan(300)
  await page.request.post('/api/v1/calendar/sync')
  // The server's answer (POST …/rsvp with scope and occurrence); the REPLY only queues.
  const bodies: Record<string, unknown>[] = []
  const answers: Record<string, unknown>[] = []
  page.on('response', async (r) => {
    if (r.url().endsWith('/rsvp') && r.request().method() === 'POST') {
      bodies.push(r.request().postDataJSON() as Record<string, unknown>)
      answers.push(await r.json().catch(() => ({ status: r.status() })))
    }
  })
  await openIn(page, 'classic', `/calendar/week/${dayOf(2)}`)
  await expect(event(page, 'Weekly with Dana')).toBeVisible()
  await event(page, 'Weekly with Dana').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Yes' }).click()
  const dlg = page.getByRole('dialog', { name: 'Reply to recurring event' })
  await expect(dlg).toBeVisible()
  await dlg.getByTestId('scope-ok').click()
  await expect.poll(() => bodies.length).toBe(1)
  const occ = new Date(at(2, 15)).toISOString().replace('.000Z', '+00:00')
  expect(bodies[0]).toMatchObject({ response: 'accepted', scope: 'this', occurrence: occ })
  await expect.poll(() => answers.length).toBe(1)
  expect(answers[0]).toMatchObject({ response: 'accepted', scope: 'this', occurrence: occ })
  await expect(page.locator('.toast').filter({ hasText: 'Replied Yes to this event' })).toBeVisible()
})

test('Download .ics first saves the calendar as one file (ACT event.export_ics)', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const made = await page.request.post('/api/v1/calendar/collections', { data: { name: 'Export me', kind: 'events' } })
  expect(made.ok(), await made.text()).toBeTruthy()
  const cols = (await (await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })).json()) as { url: string; name: string }[] | { items: { url: string; name: string }[] }
  const url = (Array.isArray(cols) ? cols : cols.items).find((c) => c.name === 'Export me')!.url
  await page.request.post('/api/v1/calendar/events', { data: { summary: 'Kept in the copy', start: at(2, 13), end: at(2, 14), calendar: url } })
  await openIn(page, 'classic', `/calendar/week/${dayOf(2)}`)
  await page.getByRole('button', { name: 'Options for Export me' }).click()
  await page.getByRole('menuitem', { name: 'Delete…' }).click()
  const confirm = page.getByRole('dialog', { name: 'Delete “Export me”?' })
  const [download] = await Promise.all([page.waitForEvent('download'), confirm.getByRole('button', { name: 'Download .ics first' }).click()])
  expect(download.suggestedFilename()).toBe('Export me.ics')
  const text = await (await download.createReadStream()).toArray().then((b) => Buffer.concat(b).toString('utf8'))
  expect(text).toContain('BEGIN:VCALENDAR')
  expect(text).toContain('SUMMARY:Kept in the copy')
  await confirm.getByRole('button', { name: 'Cancel' }).click()
})

test('Duplicate from the detail and from the editor (ACT event.duplicate)', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const count = async (name: string) => {
    const j = await (await page.request.get('/api/v1/calendar/agenda', { params: { from: dayOf(-7), to: dayOf(21) } })).json()
    return ((j.events ?? []) as { summary: string }[]).filter((e) => e.summary === name).length
  }
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await event(page, 'Budget review').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Duplicate' }).click()
  const ed = page.getByRole('dialog', { name: 'Duplicate event' })
  await expect(ed).toBeVisible()
  await ed.getByTestId('cal-editor-save').click()
  await expect.poll(() => count('Budget review')).toBe(2)
  // From inside the editor of another event.
  await event(page, 'Quick call').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
  await page.getByRole('dialog', { name: 'Edit event' }).getByRole('button', { name: 'Duplicate' }).click()
  await expect(ed).toBeVisible()
  await ed.getByTestId('cal-editor-save').click()
  await expect.poll(() => count('Quick call')).toBe(2)
})
