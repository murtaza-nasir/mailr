// ACT-SURFACES: web.sheet web.editor web.calendar web.palette
// The Calendar app against the richer seed (mailr e2e-seed --extras): several calendars, a
// read-only one that nothing can move, and a repeating task on the grid that asks the series
// question when its chip is dragged (ACT task.reschedule, ACT calendar.toggle_visibility).
import type { Page } from '@playwright/test'
import { apiSettled, connectCalendar, expect, test } from './fixtures'
import { drag, openIn } from './calendar-data'

test.use({ extras: true })

const event = (page: Page, name: string) => page.getByTestId('cal-event').filter({ hasText: name }).first()

async function agendaStart(page: Page, uid: string): Promise<string | undefined> {
  const from = new Date(Date.now() - 14 * 86400_000).toISOString().slice(0, 10)
  const to = new Date(Date.now() + 21 * 86400_000).toISOString().slice(0, 10)
  const j = await (await page.request.get('/api/v1/calendar/agenda', { params: { from, to } })).json()
  return ((j.events ?? []) as { uid: string; start: string }[]).find((e) => e.uid === uid)?.start
}

test('several calendars; the read-only one shows a lock and nothing in it moves', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  const holidayUid = x.events!['E2E Holiday']
  const before = await agendaStart(page, holidayUid)
  await openIn(page, 'classic', `/calendar/week/${before!.slice(0, 10)}`)
  // Every calendar has its own visibility box.
  for (const name of ['Personal', 'E2E Work', 'E2E Holidays']) await expect(page.getByRole('checkbox', { name: `Show ${name}` })).toBeVisible()
  const holiday = event(page, 'E2E Holiday')
  await expect(holiday).toBeVisible()
  // A drag of the read-only all-day event to another day changes nothing.
  const b = (await holiday.boundingBox())!
  await drag(page, { x: b.x + b.width / 2, y: b.y + b.height / 2 }, { x: b.x - b.width, y: b.y + b.height / 2 })
  await page.waitForTimeout(500)
  expect(await agendaStart(page, holidayUid)).toBe(before)
  // Its detail says so and offers no edit.
  await holiday.click()
  const detail = page.getByTestId('cal-detail')
  await expect(detail).toContainText('Read-only calendar')
  await expect(detail.getByRole('button', { name: 'Edit (e)' })).toHaveCount(0)
  // ACT calendar.toggle_visibility: hiding E2E Work hides the client call.
  await page.keyboard.press('Escape')
  await page.getByRole('checkbox', { name: 'Show E2E Work' }).uncheck()
  await expect(event(page, 'E2E Client call')).toHaveCount(0)
})

test('a repeating task chip dragged on the grid asks This occurrence or All', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  const uid = x.tasks!['E2E Water plants']
  const taskOf = async () => {
    const j = await (await page.request.get(`/api/v1/tasks/${encodeURIComponent(uid)}`)).json()
    return j as { due: string; rrule: string | null }
  }
  const due = (await taskOf()).due
  await openIn(page, 'classic', `/calendar/week/${due}`)
  await page.getByText('Show tasks on calendar').click()
  const chip = page.getByTestId('cal-task').filter({ hasText: 'E2E Water plants' })
  await expect(chip).toBeVisible()
  // The column next to the due, in the same week.
  const days = await page.locator('.cal-col[data-day]').evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.day!))
  const i = days.indexOf(due)
  const target = days[i > 0 ? i - 1 : i + 1]
  const col = (await page.locator(`.cal-col[data-day="${target}"]`).boundingBox())!
  const c = (await chip.locator('.cal-task-title').boundingBox())!
  const strip = (await page.locator('.cal-strip').boundingBox())!
  await drag(page, { x: c.x + 10, y: c.y + c.height / 2 }, { x: col.x + col.width / 2, y: strip.y + strip.height - 4 })
  const dlg = page.getByRole('dialog', { name: 'Change a repeating task' })
  await expect(dlg).toBeVisible()
  await dlg.getByRole('button', { name: 'This occurrence' }).click()
  await expect.poll(async () => (await taskOf()).due).toBe(target)
  expect((await taskOf()).rrule).toMatch(/BYDAY=/)
})

interface AgendaEvent { uid: string; start: string; calendar: string; color: string | null; url: string | null; reminders: number[]; attendee_list: { email: string }[] }
async function agendaEvent(page: Page, uid: string): Promise<AgendaEvent | undefined> {
  const from = new Date(Date.now() - 14 * 86400_000).toISOString().slice(0, 10)
  const to = new Date(Date.now() + 21 * 86400_000).toISOString().slice(0, 10)
  const j = await (await page.request.get('/api/v1/calendar/agenda', { params: { from, to } })).json()
  return ((j.events ?? []) as AgendaEvent[]).find((e) => e.uid === uid)
}

test('detail: Colour, Move to another calendar, Email guests, Join; editor: invite, calendar, colour, video', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  const planning = x.events!['E2E Planning']
  const client = x.events!['E2E Client call']
  const work = x.calendars!['E2E Work']
  const personal = x.calendar!
  // A call that is on now, with a video link.
  const now = Date.now()
  await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Video now', start: new Date(now - 60_000).toISOString(), end: new Date(now + 45 * 60_000).toISOString(), url: 'https://meet.jit.si/mailr-e2e-now' } })
  const day = (await agendaEvent(page, planning))!.start.slice(0, 10)
  await openIn(page, 'classic', `/calendar/week/${day}`)

  // ACT event.set_color from the detail, with its Undo (the colour it had comes back).
  await event(page, 'E2E Planning').click()
  const detail = page.getByTestId('cal-detail')
  const before = await agendaEvent(page, planning)
  await detail.getByRole('button', { name: 'Colour' }).click()
  await detail.getByRole('radio', { name: 'Tomato' }).click()
  await expect.poll(async () => (await agendaEvent(page, planning))?.color?.toLowerCase()).toBe('#d50000')
  await page.locator('.toast').filter({ hasText: 'Colour changed' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await agendaEvent(page, planning))?.color ?? null).toBe(before?.color ?? null)
  await apiSettled(page, /\/api\/v1\/calendar\//)
  await detail.getByRole('radio', { name: 'Tomato' }).click()
  await expect.poll(async () => (await agendaEvent(page, planning))?.color?.toLowerCase()).toBe('#d50000')
  // ACT event.remind_before from the detail: a reminder added, and the Undo puts the old set back.
  await apiSettled(page, /\/api\/v1\/calendar\//)
  const reminders = [...(before?.reminders ?? [])].sort((a, b) => a - b)
  await detail.locator('.cal-reminders').getByRole('button', { name: 'Edit' }).click()
  await detail.getByRole('button', { name: '2 hours before' }).click()
  await expect.poll(async () => (await agendaEvent(page, planning))?.reminders).toContain(120)
  await page.locator('.toast').filter({ hasText: 'Reminders changed' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => [...((await agendaEvent(page, planning))?.reminders ?? [])].sort((a, b) => a - b)).toEqual(reminders)
  await apiSettled(page, /\/api\/v1\/calendar\//)
  // ACT event.email_guests: a new message to the guests, about the event.
  await detail.getByRole('button', { name: 'Email guests' }).click()
  await expect(page.getByRole('textbox', { name: 'Subject' })).toHaveValue('E2E Planning')
  const win = page.getByRole('dialog', { name: 'E2E Planning' })
  await expect(win.getByRole('button', { name: 'Remove dana-0@example.org' })).toBeVisible()
  await expect(win.getByRole('button', { name: `Remove ${seeded.email}` })).toHaveCount(0)
  await win.getByRole('button', { name: 'Discard draft' }).click()
  // ACT event.move_calendar from the detail. The move sends the version the page holds: wait until
  // the page has reloaded the event after the colour change, or the server refuses an old version.
  await apiSettled(page, /\/api\/v1\/calendar\//)
  await event(page, 'E2E Planning').click()
  await detail.getByRole('button', { name: 'Move to…' }).click()
  await detail.getByRole('option', { name: 'E2E Work' }).click()
  await expect.poll(async () => (await agendaEvent(page, planning))?.calendar).toBe(work)

  // ACT event.invite: one guest added in the editor asks before inviting. The page reloads the
  // moved event first (an editor opened on the old copy starts again when the new one arrives).
  await apiSettled(page, /\/api\/v1\/calendar\//)
  await event(page, 'E2E Planning').click()
  await detail.getByRole('button', { name: 'Edit (e)' }).click()
  const ed = page.getByTestId('cal-editor')
  await expect(ed.getByRole('textbox', { name: 'Title' })).toHaveValue('E2E Planning')
  const add = ed.getByRole('combobox', { name: 'Add guests' })
  const guests = await ed.getByTestId('cal-guest').count()
  await add.fill('priya@example.org')
  await add.press('Enter')
  // Enter typed before the guest suggestions arrive acts when they do: wait for the new chip, or
  // Save finds no new guest and closes without asking.
  await expect(ed.getByTestId('cal-guest')).toHaveCount(guests + 1)
  await expect(ed.getByRole('textbox', { name: 'Title' })).toHaveValue('E2E Planning')
  await ed.getByTestId('cal-editor-save').click()
  await page.getByRole('dialog', { name: 'Send invitation to 1 new guest?' }).getByRole('button', { name: "Don't send" }).click()
  await expect.poll(async () => (await agendaEvent(page, planning))?.attendee_list.map((a) => a.email)).toContain('priya@example.org')

  // The editor: ACT event.move_calendar, ACT event.set_color, ACT event.add_video.
  await event(page, 'E2E Client call').click()
  await detail.getByRole('button', { name: 'Edit (e)' }).click()
  await ed.getByRole('radiogroup', { name: 'Calendar' }).getByText('Personal').click()
  await ed.getByRole('radio', { name: 'Sage' }).click()
  await ed.getByRole('button', { name: 'Add video link' }).click()
  await ed.getByTestId('cal-editor-save').click()
  await expect(ed).toHaveCount(0)
  await expect.poll(async () => (await agendaEvent(page, client))?.calendar).toBe(personal)
  const saved = (await agendaEvent(page, client))!
  expect(saved.url).toMatch(/^https:\/\//)
  expect(saved.color).not.toBeNull()

  // ACT event.join: the block on the grid and the detail both offer Join while it is on.
  // Today in the browser's zone (toISOString is UTC's day, tomorrow in the evening west of Greenwich).
  const local = new Date(now)
  await page.goto(`/calendar/day/${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')}`)
  const block = event(page, 'E2E Video now')
  await expect(block.getByRole('link', { name: 'Join' })).toHaveAttribute('href', 'https://meet.jit.si/mailr-e2e-now')
  await block.click({ position: { x: 8, y: 8 } })
  await expect(detail.getByRole('link', { name: 'Join' })).toHaveAttribute('href', 'https://meet.jit.si/mailr-e2e-now')
})

test('calendar menu: Set as default and Refresh (ACT calendar.set_default, ACT calendar.sync)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await openIn(page, 'classic', '/calendar/week')
  await page.getByRole('button', { name: 'Options for E2E Work' }).click()
  await page.getByRole('menuitem', { name: 'Set as default' }).click()
  await expect(page.locator('.toast').filter({ hasText: 'E2E Work is the default calendar' })).toBeVisible()
  await expect.poll(async () => JSON.stringify(await (await page.request.get('/api/v1/calendar/defaults')).json())).toContain(x.calendars!['E2E Work'])
  const sync = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/api/v1/calendar/sync'))
  await page.getByRole('button', { name: 'Options for E2E Work' }).click()
  await page.getByRole('menuitem', { name: 'Refresh' }).click()
  await sync
})

test('palette: Show only one calendar (ACT calendar.show_only on web.palette)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  const planning = x.events!['E2E Planning']
  const j = await (await page.request.get('/api/v1/calendar/agenda', { params: { from: new Date(Date.now() - 14 * 86400_000).toISOString().slice(0, 10), to: new Date(Date.now() + 21 * 86400_000).toISOString().slice(0, 10) } })).json()
  const day = ((j.events ?? []) as { uid: string; start: string }[]).find((e) => e.uid === planning)!.start.slice(0, 10)
  await openIn(page, 'classic', `/calendar/week/${day}`)
  await expect(event(page, 'E2E Planning')).toBeVisible()
  await expect(page.getByTestId('waiting-bell')).toBeVisible()
  await page.keyboard.press('Control+k')
  const input = page.getByRole('combobox', { name: 'Command' })
  await input.fill('show only')
  await page.getByRole('option', { name: /Show only calendar: E2E Work/ }).click()
  await expect(event(page, 'E2E Client call')).toBeVisible()
  await expect(event(page, 'E2E Planning')).toHaveCount(0)
})
