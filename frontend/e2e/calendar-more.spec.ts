// ACT-SURFACES: web.calendar
// More of the Calendar app: guests in the editor with the question whether to send updates, the
// changed-elsewhere sheet on a stale write, an event deleted elsewhere while its detail is open,
// RSVP from the detail, tasks on the calendar,
// the offline state, `g` + a typed date, and the month's "+n more".
import type { Page } from '@playwright/test'
import { expect, outbox, test } from './fixtures'
import { agenda, at, createEvent, dayOf, hhmm, isoDay, openIn, seedWeek, slotPoint } from './calendar-data'

const event = (page: Page, name: string) => page.getByTestId('cal-event').filter({ hasText: name }).first()

test('guests: People autocomplete, Save & invite asks the send-updates question; Don\'t send writes without mail', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const before = (await outbox(page.request)).length
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  await page.keyboard.press('c')
  await page.getByRole('textbox', { name: 'Event, in one line' }).fill('Guest test tomorrow 3pm')
  await page.keyboard.press('Control+Enter')
  const ed = page.getByTestId('cal-editor')
  const add = ed.getByRole('combobox', { name: 'Add guests' })
  await add.fill('dana')
  await ed.getByRole('option', { name: /dana@example\.org/ }).click()
  await expect(ed.getByTestId('cal-guest')).toHaveCount(1)
  // ACT event.create with guests: Save & invite… asks first.
  await ed.getByTestId('cal-editor-save').click()
  const ask = page.getByRole('dialog', { name: 'Send invitation to 1 new guest?' })
  await expect(ask).toBeVisible()
  await ask.getByRole('button', { name: "Don't send" }).click()
  await expect(ed).toHaveCount(0)
  await expect.poll(async () => (await agenda(page.request)).find((e) => e.summary === 'Guest test')?.attendee_list.map((a) => a.email)).toEqual(['dana@example.org'])
  expect((await outbox(page.request)).length).toBe(before)
})

test('Edit conflict: a drop on a version changed elsewhere shows both and Use mine wins', async ({ page, seeded }) => {
  const ev = await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await slotPoint(page, dayOf(3), 14)
  const b = (await event(page, 'Budget review').boundingBox())!
  const h = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.cal-tg')!).getPropertyValue('--hour-h')))
  await page.mouse.move(b.x + b.width / 2, b.y + 8)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + 8 + h / 2, { steps: 4 })
  // Meanwhile another device moves it to 13:00.
  const other = await page.request.patch(`/api/v1/calendar/events/${encodeURIComponent(ev.budget.uid)}`, { data: { start: at(3, 13), end: at(3, 14) } })
  expect(other.ok()).toBeTruthy()
  await page.mouse.move(b.x + b.width / 2, b.y + 8 + h * 2, { steps: 6 })
  await page.mouse.up()
  const sheet = page.getByRole('dialog', { name: 'Changed elsewhere' })
  await expect(sheet).toBeVisible()
  await expect(sheet.getByText(/Their version/)).toBeVisible()
  await expect(sheet).toContainText('1:00')
  await sheet.getByRole('button', { name: 'Use mine' }).click()
  await expect.poll(async () => hhmm((await agenda(page.request)).find((e) => e.summary === 'Budget review')!.start)).toBe('16:00')
})

test('deleted elsewhere: the open detail says so and restores as new', async ({ page, seeded }) => {
  const ev = await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await event(page, 'Quick call').click()
  await expect(page.getByTestId('cal-detail')).toBeVisible()
  const del = await page.request.delete(`/api/v1/calendar/events/${encodeURIComponent(ev.quick.uid)}`)
  expect(del.ok()).toBeTruthy()
  // Live updates refresh the range; a sync makes sure of it on a slow stream.
  await page.keyboard.press('r').catch(() => {})
  await expect(page.getByText('This event was deleted elsewhere')).toBeVisible()
  await page.getByRole('button', { name: 'Restore as new' }).click()
  await expect.poll(async () => (await agenda(page.request)).filter((e) => e.summary === 'Quick call').length).toBe(1)
})

test('RSVP from the detail and with y', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/schedule/${dayOf(3)}`)
  await event(page, 'E2E Invitation').click()
  const detail = page.getByTestId('cal-detail')
  await expect(detail.getByRole('group', { name: 'Going?' })).toBeVisible()
  // ACT event.rsvp
  await detail.getByRole('button', { name: 'Maybe', exact: true }).click()
  await expect.poll(async () => {
    const j = await (await page.request.get('/api/v1/calendar/agenda', { params: { from: dayOf(0), to: dayOf(14) } })).json()
    return (j.events as { summary: string; my_status: string }[]).find((e) => e.summary === 'E2E Invitation')?.my_status
  }).toBe('TENTATIVE')
  // Exact: once answered, the guest line reads "guest · 1 maybe" and is a button too.
  await expect(detail.getByRole('button', { name: 'Maybe', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await detail.press('y')
  await expect.poll(async () => {
    const j = await (await page.request.get('/api/v1/calendar/agenda', { params: { from: dayOf(0), to: dayOf(14) } })).json()
    return (j.events as { summary: string; my_status: string }[]).find((e) => e.summary === 'E2E Invitation')?.my_status
  }).toBe('ACCEPTED')
})

test('tasks on the calendar: a chip at its time that completes', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const t = await page.request.post('/api/v1/tasks', { data: { summary: 'File the report', due: at(3, 11) } })
  expect(t.ok(), await t.text()).toBeTruthy()
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  await page.getByText('Show tasks on calendar').click()
  const chip = page.getByTestId('cal-task').filter({ hasText: 'File the report' })
  await expect(chip).toBeVisible()
  // ACT task.complete
  await chip.getByRole('checkbox', { name: 'Complete File the report' }).click()
  await expect.poll(async () => {
    const j = await (await page.request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
    return ((Array.isArray(j) ? j : j.items) as { summary: string; status: string }[]).find((x) => x.summary === 'File the report')?.status
  }).toBe('completed')
})

test('offline: a banner, and writes are off', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  await page.context().setOffline(true)
  await expect(page.locator('.cal-banner.offline')).toBeVisible()
  await expect(page.locator('.cal-create')).toBeDisabled()
  await event(page, 'Budget review').focus()
  await page.keyboard.press('Shift+ArrowDown')
  await expect(page.locator('.toast').filter({ hasText: /read-only/i })).toBeVisible()
  await page.context().setOffline(false)
  await expect(page.locator('.cal-banner.offline')).toHaveCount(0)
})

test('g and a typed date go there; the month shows +n more', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  for (let i = 0; i < 6; i++) await createEvent(page.request, { summary: `Busy ${i}`, start: at(2, 8 + i), end: at(2, 8 + i, 30) })
  await openIn(page, 'classic', `/calendar/month/${dayOf(3)}`)
  await expect(event(page, 'Team sync')).toBeVisible()
  const more = page.locator(`[data-mday="${dayOf(2)}"] .cal-mmore`)
  await expect(more).toBeVisible()
  await more.click()
  const pop = page.getByRole('dialog').filter({ has: page.locator('.cal-day-pop-head') })
  await expect(pop.getByText('Busy 5')).toBeVisible()
  await pop.getByText('Busy 5').click()
  await expect(page.getByTestId('cal-detail')).toBeVisible()
  await page.keyboard.press('Escape')

  // g then "12 dec", from the view. The closed detail and popover hand the focus back to what
  // opened them first, and a focused button keeps Space, which "12 dec" types.
  await expect(page.getByTestId('cal-detail')).toHaveCount(0)
  await expect(page.locator('.cal-day-pop-head')).toHaveCount(0)
  await page.waitForTimeout(150)
  await page.locator('.cal-view').focus()
  await expect(page.locator('.cal-view')).toBeFocused()
  await page.keyboard.press('g')
  await page.keyboard.type('12 dec')
  await expect(page.getByRole('dialog', { name: 'Go to date' })).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/calendar\/month\/\d{4}-12-12/)
})

test('a month chip dragged to another day keeps its time', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/month/${dayOf(3)}`)
  const chip = page.locator(`[data-mday="${dayOf(2)}"] .cal-mchip`).filter({ hasText: 'Writing block' })
  await expect(chip).toBeVisible()
  const from = (await chip.boundingBox())!
  const to = (await page.locator(`[data-mday="${dayOf(9)}"]`).boundingBox())!
  // ACT event.reschedule
  await page.mouse.move(from.x + 20, from.y + from.height / 2)
  await page.mouse.down()
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 10 })
  await page.mouse.up()
  await expect.poll(async () => {
    const e = (await agenda(page.request, dayOf(0), dayOf(21))).find((x) => x.summary === 'Writing block')!
    return `${isoDay(new Date(e.start))} ${hhmm(e.start)}-${hhmm(e.end)}`
  }).toBe(`${dayOf(9)} 13:00-16:00`)
})

test('the calendar menu: rename, colour, default reminders; a new calendar and its delete', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  const cals = async () => (await (await page.request.get('/api/v1/calendar/calendars')).json()) as { name: string; color: string | null; default_reminders: unknown; calendar_id: number }[]

  // ACT calendar.create
  await page.getByRole('button', { name: 'New calendar' }).first().click()
  const dlg = page.getByRole('dialog', { name: 'New calendar' })
  await dlg.getByRole('textbox').first().fill('Side project')
  await dlg.getByRole('radio', { name: 'Sage' }).click()
  await dlg.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('checkbox', { name: 'Show Side project' })).toBeVisible()

  // ACT calendar.rename, ACT calendar.color, ACT calendar.default_reminders
  await page.getByRole('button', { name: 'Options for Side project' }).click()
  await page.getByRole('menuitem', { name: 'Rename…' }).click()
  await page.getByRole('dialog', { name: 'Rename calendar' }).getByRole('textbox').fill('Side quest')
  await page.getByRole('dialog', { name: 'Rename calendar' }).getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('checkbox', { name: 'Show Side quest' })).toBeVisible()
  await page.getByRole('button', { name: 'Options for Side quest' }).click()
  await page.getByRole('radio', { name: 'Grape' }).click()
  await expect.poll(async () => (await cals()).find((c) => c.name === 'Side quest')?.color?.toLowerCase()).toBe('#8e24aa')
  await page.getByRole('button', { name: 'Options for Side quest' }).click()
  await page.getByRole('menuitem', { name: 'Default reminders…' }).click()
  const rem = page.getByRole('dialog', { name: /Default reminders/ })
  await rem.getByRole('button', { name: '10 min before' }).click()
  await rem.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => JSON.stringify((await cals()).find((c) => c.name === 'Side quest')?.default_reminders)).toContain('10')

  // The default calendar cannot be deleted; the new one can, after a confirmation.
  await page.getByRole('button', { name: 'Options for Personal' }).click()
  await expect(page.getByRole('menuitem', { name: 'Delete…' })).toBeDisabled()
  await page.keyboard.press('Escape')
  // ACT calendar.delete
  await page.getByRole('button', { name: 'Options for Side quest' }).click()
  await page.getByRole('menuitem', { name: 'Delete…' }).click()
  const confirm = page.getByRole('dialog', { name: 'Delete “Side quest”?' })
  await expect(confirm.getByRole('button', { name: 'Download .ics first' })).toBeVisible()
  await confirm.getByRole('button', { name: 'Delete calendar' }).click()
  await expect(page.getByRole('checkbox', { name: 'Show Side quest' })).toHaveCount(0)
  await expect.poll(async () => (await cals()).some((c) => c.name === 'Side quest')).toBe(false)
})
