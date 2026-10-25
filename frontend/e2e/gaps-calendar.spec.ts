// ACT-SURFACES: web.panel web.sheet web.editor web.settings web.calendar
// The calendar cells closed after the main build (web gaps): the organizer's cancel with a message
// from the detail and the editor (event.cancel), removing guests (event.uninvite), Email guests
// from the editor (event.email_guests), the series' exact count of changed occurrences in
// the series question and the .ics copy before a delete (event.export_ics), Create → Task in the calendar
// (task.create), the calendar panel's visibility menu (calendar.toggle_visibility) and Settings →
// Calendar & Tasks → Calendars and task lists (calendar.create, calendar.color, calendar.rename,
// calendar.delete, calendar.set_default, list.set_default).
import type { Page } from '@playwright/test'
import { connectCalendar, expect, outbox, shot, tasks, test } from './fixtures'
import { openIn } from './calendar-data'

test.use({ extras: true })

const event = (page: Page, name: string) => page.getByTestId('cal-event').filter({ hasText: name }).first()

/** Tomorrow at an hour, as an instant. */
function tomorrowAt(hour: number, minute = 0): Date {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  d.setHours(hour, minute, 0, 0)
  return d
}
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

interface Ev { uid: string; summary: string; status: string; attendee_list: { email: string }[]; recurring: boolean }

/** An event I organize: the invitations go to the captured outbox (nothing leaves the machine). */
async function withGuests(page: Page, summary: string, hour: number, guests: string[], extra: Record<string, unknown> = {}): Promise<Ev> {
  const s = tomorrowAt(hour)
  const r = await page.request.post('/api/v1/calendar/events', { data: {
    summary, start: s.toISOString(), end: new Date(s.getTime() + 45 * 60_000).toISOString(), attendees: guests, ...extra } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as Ev
}

async function master(page: Page, uid: string): Promise<Ev & { override_count?: number }> {
  return (await (await page.request.get(`/api/v1/calendar/events/${encodeURIComponent(uid)}`)).json()) as Ev & { override_count?: number }
}

test('event.cancel from the detail (web.sheet) and the editor (web.editor), with a message and an .ics copy first', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const a = await withGuests(page, 'E2E Cancel sheet', 9, ['dana@example.org', 'evan@example.org'])
  const b = await withGuests(page, 'E2E Cancel editor', 13, ['dana@example.org'])
  const before = (await outbox(page.request)).length
  await openIn(page, 'classic', `/calendar/week/${isoDay(tomorrowAt(9))}`)

  // The detail: Cancel event asks for a message; the event shows as cancelled at once.
  await event(page, 'E2E Cancel sheet').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Cancel event' }).click()
  const ask = page.getByRole('dialog', { name: 'Cancel “E2E Cancel sheet”?' })
  await expect(ask).toContainText('Your 2 guests get a cancellation')
  await ask.getByRole('textbox', { name: 'Message to guests' }).fill('Sorry, we will meet next month.')
  await ask.getByTestId('cancel-event-ok').click()
  await expect(page.locator('.toast').filter({ hasText: 'Event cancelled · cancellation goes to 2 guests' })).toBeVisible()
  // Sent when the undo window closes: STATUS:CANCELLED and one cancellation to the guests.
  await expect.poll(async () => (await master(page, a.uid)).status).toBe('CANCELLED')
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === 'Cancelled: E2E Cancel sheet').length).toBe(1)

  // The editor: the footer's Cancel event; the prompt offers the .ics copy.
  await event(page, 'E2E Cancel editor').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
  const ed = page.getByTestId('cal-editor')
  await ed.getByRole('button', { name: 'Cancel event' }).click()
  const ask2 = page.getByRole('dialog', { name: 'Cancel “E2E Cancel editor”?' })
  const download = page.waitForEvent('download')
  await ask2.getByRole('button', { name: 'Download a copy (.ics)' }).click()
  expect((await download).suggestedFilename()).toBe('E2E Cancel editor.ics')
  await ask2.getByTestId('cancel-event-ok').click()
  await expect.poll(async () => (await master(page, b.uid)).status).toBe('CANCELLED')
  await expect.poll(async () => (await outbox(page.request)).length - before).toBe(2)
  // A cancelled event offers no second cancel.
  await event(page, 'E2E Cancel editor').click()
  await expect(page.getByTestId('cal-detail').getByRole('button', { name: 'Cancel event' })).toHaveCount(0)
})

test('event.uninvite and event.email_guests in the editor', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const ev = await withGuests(page, 'E2E Guest list', 11, ['dana@example.org', 'evan@example.org', 'fay@example.org'])
  const before = (await outbox(page.request)).length
  await openIn(page, 'classic', `/calendar/week/${isoDay(tomorrowAt(11))}`)
  await event(page, 'E2E Guest list').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
  const ed = page.getByTestId('cal-editor')

  // ACT event.email_guests: the groups say how many each reaches; none has answered yet.
  await ed.getByRole('button', { name: 'Email guests' }).click()
  await expect(page.getByRole('menuitem', { name: 'Guests who said yes · 0' })).toBeDisabled()
  await page.getByRole('menuitem', { name: "Guests who haven't answered · 3" }).click()
  await expect(page.getByRole('textbox', { name: 'Subject' })).toHaveValue('E2E Guest list')
  const win = page.getByRole('dialog', { name: 'E2E Guest list' })
  await expect(win.getByRole('button', { name: 'Remove evan@example.org' })).toBeVisible()
  await win.getByRole('button', { name: 'Discard draft' }).click()

  // ACT event.uninvite: one guest removed; only that guest gets a cancellation, with the message.
  await event(page, 'E2E Guest list').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
  await ed.getByRole('button', { name: 'Remove fay@example.org' }).click()
  await ed.getByTestId('cal-editor-save').click()
  const ask = page.getByRole('dialog', { name: 'Send cancellation to 1 removed guest?' })
  await expect(ask).toContainText("Others aren't emailed")
  await ask.getByRole('button', { name: 'Add a message' }).click()
  await ask.getByRole('textbox', { name: 'Message to guests' }).fill('Fay, you are off the hook.')
  await ask.getByRole('button', { name: 'Send', exact: true }).click()
  await expect.poll(async () => (await master(page, ev.uid)).attendee_list.map((g) => g.email).sort()).not.toContain('fay@example.org')
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === 'Cancelled: E2E Guest list').length).toBe(1)
  expect((await outbox(page.request)).length).toBe(before + 1)
})

test('The series question counts every changed occurrence of the series; a series delete offers the .ics copy (event.export_ics)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const s = tomorrowAt(8)
  const r = await page.request.post('/api/v1/calendar/events', { data: {
    summary: 'E2E Daily sync', start: s.toISOString(), end: new Date(s.getTime() + 15 * 60_000).toISOString(), recurrence: 'FREQ=DAILY;COUNT=40' } })
  const ev = (await r.json()) as Ev
  // Two changed occurrences, one of them weeks away (outside the loaded range).
  for (const days of [1, 30]) {
    const occ = new Date(s.getTime() + days * 86_400_000)
    const moved = new Date(occ.getTime() + 60 * 60_000)
    const p = await page.request.patch(`/api/v1/calendar/events/${encodeURIComponent(ev.uid)}`, { data: {
      start: moved.toISOString(), end: new Date(moved.getTime() + 15 * 60_000).toISOString(), scope: 'this', occurrence: occ.toISOString().replace(/\.\d+Z$/, '+00:00') } })
    expect(p.ok(), await p.text()).toBeTruthy()
  }
  expect((await master(page, ev.uid)).override_count).toBe(2)
  await openIn(page, 'classic', `/calendar/week/${isoDay(s)}`)
  await event(page, 'E2E Daily sync').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
  const ed = page.getByTestId('cal-editor')
  await ed.getByRole('textbox', { name: 'Title' }).fill('E2E Daily standup')
  await ed.getByTestId('cal-editor-save').click()
  const scope = page.getByRole('dialog', { name: 'Change recurring event' })
  await scope.getByText('All events').click()
  await expect(scope.getByTestId('scope-exceptions')).toHaveText('2 changed occurrences will keep their own time.')
  await scope.getByRole('button', { name: 'Cancel' }).click()
  await ed.getByRole('button', { name: 'Close' }).click()
  await page.getByRole('dialog', { name: 'Discard changes?' }).getByRole('button', { name: 'Discard' }).click()

  // ACT event.delete of a series: the scope prompt offers the stored copy first.
  await event(page, 'E2E Daily sync').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Delete (#)' }).click()
  const del = page.getByRole('dialog', { name: 'Delete recurring event' })
  const download = page.waitForEvent('download')
  await del.getByRole('button', { name: 'Download a copy (.ics)' }).click()
  const file = await download
  expect(file.suggestedFilename()).toBe('E2E Daily sync.ics')
  await del.getByRole('button', { name: 'Cancel' }).click()
})

test('task.create in the calendar: Create → Task opens the task editor for the day in view', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  const day = isoDay(tomorrowAt(9))
  await openIn(page, 'classic', `/calendar/day/${day}`)
  await page.getByRole('navigation', { name: 'Calendar navigation' }).getByRole('button', { name: 'Create', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Task' }).click()
  const editor = page.getByRole('dialog', { name: 'New task' })
  await expect(editor.getByLabel('Due')).toHaveValue(day)
  await editor.getByLabel('Title').fill('E2E Calendar task')
  await editor.getByLabel('List').selectOption({ label: 'E2E Errands' })
  await editor.getByRole('button', { name: 'Add task' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Calendar task')?.due).toBe(day)
  const made = (await tasks(page.request)).find((t) => t.summary === 'E2E Calendar task') as unknown as { list: string }
  expect(made.list.replace(/\/$/, '')).toBe(x.task_list!.url.replace(/\/$/, ''))
})

test('calendar.toggle_visibility from the calendar panel (web.panel)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await page.goto('/mail/inbox')
  await page.getByRole('complementary', { name: 'Open beside' }).getByRole('button', { name: 'Calendar beside' }).click()
  const panel = page.getByRole('complementary', { name: 'Calendar', exact: true })
  await panel.getByRole('button', { name: 'Calendars shown' }).click()
  const menu = page.getByRole('dialog', { name: 'Calendars shown' })
  await menu.getByRole('checkbox', { name: 'Show E2E Work' }).uncheck()
  const hidden = async () => ((await (await page.request.get('/api/v1/calendar/calendars')).json()) as { name: string; hidden?: boolean }[]).find((c) => c.name === 'E2E Work')?.hidden
  await expect.poll(hidden).toBe(true)
  await page.waitForTimeout(300)
  await page.screenshot({ path: shot('panel-calendars.png') })
  await menu.getByRole('button', { name: 'Show all' }).click()
  await expect.poll(hidden).toBe(false)
})

// ACT calendar.toggle_visibility @web.panel under a slow network (the panel race, 10 Oct 2026): the
// panel's box follows each click at once, also while the first save is held on its way, and the
// saves reach the server in click order, so it ends on the last click.
test('calendar panel: each click shows at once and the last click wins while an earlier save is slow', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  const sent: boolean[] = []
  let release!: () => void
  const held = new Promise<void>((r) => (release = r))
  let reached!: () => void
  const firstOut = new Promise<void>((r) => (reached = r))
  await page.route('**/api/v1/calendar/collections/*', async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    sent.push(route.request().postDataJSON()?.hidden)
    if (sent.length === 1) {
      reached()
      await held
    }
    await route.continue()
  })
  await page.goto('/mail/inbox')
  await page.getByRole('complementary', { name: 'Open beside' }).getByRole('button', { name: 'Calendar beside' }).click()
  const panel = page.getByRole('complementary', { name: 'Calendar', exact: true })
  await panel.getByRole('button', { name: 'Calendars shown' }).click()
  const box = page.getByRole('dialog', { name: 'Calendars shown' }).getByRole('checkbox', { name: 'Show E2E Work' })
  // uncheck() and check() fail when the click does not change the box in the same event.
  await box.uncheck()
  await firstOut
  await box.check()
  const lastSaved = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/calendar/collections/')
    && r.request().postDataJSON()?.hidden === false)
  release()
  await lastSaved
  await page.waitForResponse((r) => r.request().method() === 'GET' && r.url().includes('/calendar/calendars'))
  await expect(box).toBeChecked()
  expect(sent).toEqual([true, false])
  const hidden = async () => ((await (await page.request.get('/api/v1/calendar/calendars')).json()) as { name: string; hidden?: boolean }[]).find((c) => c.name === 'E2E Work')?.hidden
  expect(await hidden()).toBe(false)
})

test('web.settings: calendars and task lists (create, colour, rename, defaults, delete with an .ics copy)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await page.goto('/settings/calendar')
  const rows = page.getByTestId('settings-collection')
  await expect(rows.filter({ hasText: 'E2E Work' })).toBeVisible()
  const cols = async () => (await (await page.request.get('/api/v1/calendar/calendars')).json()) as { id: string; name: string; color?: string | null; tasks?: boolean }[]

  // ACT calendar.create: a task list.
  await page.getByRole('button', { name: 'New calendar or list' }).click()
  const dlg = page.getByRole('dialog', { name: 'New calendar or task list' })
  await dlg.getByLabel('Name').fill('E2E Settings list')
  await dlg.getByLabel('Holds').selectOption('tasks')
  await dlg.getByRole('button', { name: 'Create' }).click()
  await expect(rows.filter({ hasText: 'E2E Settings list' })).toBeVisible()
  await expect.poll(async () => (await cols()).find((c) => c.name === 'E2E Settings list')?.tasks).toBe(true)

  // ACT calendar.color
  await page.getByRole('button', { name: 'Colour of E2E Settings list' }).click()
  await page.getByRole('radio', { name: 'Sage' }).click()
  await expect.poll(async () => (await cols()).find((c) => c.name === 'E2E Settings list')?.color?.toLowerCase()).toBe('#33b679')

  // ACT calendar.rename
  await page.getByRole('button', { name: 'Rename E2E Settings list' }).click()
  const ren = page.getByRole('dialog', { name: 'Rename “E2E Settings list”' })
  await ren.getByRole('textbox', { name: 'Name' }).fill('E2E Renamed list')
  await ren.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await cols()).some((c) => c.name === 'E2E Renamed list')).toBe(true)

  // ACT calendar.set_default, ACT list.set_default
  const id = (await cols()).find((c) => c.name === 'E2E Renamed list')!.id
  await page.getByLabel('Default task list').selectOption(id)
  await expect.poll(async () => (await (await page.request.get('/api/v1/calendar/defaults')).json()).task_list).toBe(id)
  await expect(page.getByRole('button', { name: 'Delete E2E Renamed list' })).toBeDisabled()
  await page.getByLabel('Default calendar').selectOption(x.calendars!['E2E Work'])
  await expect.poll(async () => (await (await page.request.get('/api/v1/calendar/defaults')).json()).calendar).toBe(x.calendars!['E2E Work'])
  await page.getByLabel('Default task list').selectOption(x.task_list!.url)
  await expect.poll(async () => (await (await page.request.get('/api/v1/calendar/defaults')).json()).task_list).toBe(x.task_list!.url)
  await page.waitForTimeout(300)
  await page.screenshot({ path: shot('settings-collections.png'), fullPage: true })

  // ACT calendar.delete: confirmed, with the .ics copy offered first.
  await page.getByRole('button', { name: 'Delete E2E Renamed list' }).click()
  const del = page.getByRole('dialog', { name: 'Delete “E2E Renamed list”?' })
  const download = page.waitForEvent('download')
  await del.getByRole('button', { name: 'Download .ics first' }).click()
  await download
  await del.getByRole('button', { name: 'Delete list' }).click()
  await expect(rows.filter({ hasText: 'E2E Renamed list' })).toHaveCount(0)
  await expect.poll(async () => (await cols()).some((c) => c.name === 'E2E Renamed list')).toBe(false)
})
