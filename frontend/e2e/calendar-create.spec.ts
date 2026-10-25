// ACT-SURFACES: web.editor web.calendar
// Creating events ("Creating events", the full editor): a click on an
// empty slot and a drag over empty space open quick create at that time; the grammar fills the
// chips (date, time, length, place, calendar, guests as suggestions), a chip's × keeps its words as
// text, Enter saves; Ctrl+Enter carries everything into the editor, whose typed fields, repeat
// and reminders save; Esc keeps the typed line for the next open.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { themes } from './matrix'
import { agenda, dayOf, drag, hhmm, openIn, pinNow, seedWeek, slotPoint } from './calendar-data'

const quick = (page: Page) => page.getByTestId('cal-quick')
const line = (page: Page) => page.getByRole('textbox', { name: 'Event, in one line' })

// The grid's create logic is shared; the editor is a modal (Classic), a pane (Office) or a sheet
// (Cards). Nightly every theme (e2e/matrix.ts).
for (const theme of themes({ release: ['classic', 'office', 'cards'] })) {
  test(`${theme}: click an empty slot, then drag over empty space, to create`, async ({ page, seeded }) => {
    await seedWeek(page.request, seeded)
    await openIn(page, theme, `/calendar/week/${dayOf(3)}`)
    await expect(page.getByTestId('cal-event').first()).toBeVisible()
    // ACT event.create: a click at Wed 8:00 opens quick create with the default length.
    const p = await slotPoint(page, dayOf(2), 8, 10)
    await page.mouse.click(p.x, p.y)
    await expect(quick(page)).toBeVisible()
    await expect(quick(page)).toContainText('8:00')
    await expect(quick(page)).toContainText('(1 h)')
    await line(page).fill(`Slot click ${theme}`)
    await line(page).press('Enter')
    await expect(quick(page)).toHaveCount(0)
    await expect(page.getByTestId('cal-event').filter({ hasText: `Slot click ${theme}` })).toBeVisible()

    // A drag from Fri 7:00 to 8:30 gives that range, snapped to 15 minutes.
    const a = await slotPoint(page, dayOf(4), 7, 2)
    const b = { x: a.x, y: a.y + ((await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.cal-tg')!).getPropertyValue('--hour-h'))))) * 1.5 }
    await drag(page, a, b)
    await expect(quick(page)).toContainText('(1 h 30 min)')
    await line(page).fill(`Dragged ${theme}`)
    await page.getByTestId('cal-quick-save').click()
    await expect(page.getByTestId('cal-event').filter({ hasText: `Dragged ${theme}` })).toBeVisible()

    const got = await agenda(page.request)
    const click = got.find((e) => e.summary === `Slot click ${theme}`)!
    expect([hhmm(click.start), hhmm(click.end)]).toEqual(['08:00', '09:00'])
    const dragged = got.find((e) => e.summary === `Dragged ${theme}`)!
    expect([hhmm(dragged.start), hhmm(dragged.end)]).toEqual(['07:00', '08:30'])
  })
}

// The line has the focus as soon as quick create opens, from `c`, from Create → Event and from
// a slot click, so the first words typed land in it.
test('quick create takes the focus at once: c, Create → Event, a slot click', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(page.getByTestId('cal-event').first()).toBeVisible()
  await page.keyboard.press('c')
  await expect(line(page)).toBeFocused()
  await page.keyboard.type('Typed at once')
  await expect(line(page)).toHaveValue('Typed at once')
  await line(page).fill('')
  await page.keyboard.press('Escape')
  await expect(quick(page)).toHaveCount(0)

  await page.getByTestId('cal-left').getByRole('button', { name: 'Create', exact: true }).click()
  await page.getByRole('menuitem', { name: /Event/ }).click()
  await expect(quick(page)).toBeVisible()
  await expect(line(page)).toBeFocused()
  // Still there after the menu's own focus restore.
  await page.waitForTimeout(300)
  await expect(line(page)).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(quick(page)).toHaveCount(0)

  const p = await slotPoint(page, dayOf(2), 8, 10)
  await page.mouse.click(p.x, p.y)
  await expect(line(page)).toBeFocused()
  await page.keyboard.type('Slot typed')
  await expect(line(page)).toHaveValue('Slot typed')
})

test('quick create grammar: chips, keep as text, guests stay suggestions, conflict line', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(page.getByTestId('cal-event').first()).toBeVisible()
  // ACT event.create via `c`.
  await page.keyboard.press('c')
  await expect(quick(page)).toBeVisible()
  await line(page).fill('Standup tomorrow 9:15 for 15 min at Room 5 with Dana')
  const chips = quick(page).locator('.cal-qc-chips')
  await expect(chips).toContainText('9:15')
  await expect(chips).toContainText('(15 min)')
  await expect(chips).toContainText('Room 5')
  await expect(chips.locator('.cal-chip.pending').first()).toContainText('Dana')
  await expect(quick(page).locator('mark.cal-tok.k-location')).toHaveText('at Room 5')
  // × on the place chip turns its words back into title text.
  await chips.getByRole('button', { name: /Keep “at Room 5” as text/ }).click()
  await expect(quick(page).locator('mark.cal-tok.k-location')).toHaveCount(0)
  // Esc keeps the text for the next open.
  await page.keyboard.press('Escape')
  await expect(quick(page)).toHaveCount(0)
  await page.keyboard.press('c')
  await expect(line(page)).toHaveValue('Standup tomorrow 9:15 for 15 min at Room 5 with Dana')
  await expect(page.getByTestId('cal-quick-save')).toHaveText('Save')
  await line(page).press('Enter')
  await expect(quick(page)).toHaveCount(0)
  await expect.poll(async () => (await agenda(page.request)).some((e) => e.summary.startsWith('Standup'))).toBe(true)
  const ev = (await agenda(page.request)).find((e) => e.summary.startsWith('Standup'))!
  expect(ev.summary).toBe('Standup at Room 5 with Dana')
  expect([hhmm(ev.start), hhmm(ev.end)]).toEqual(['09:15', '09:30'])
  expect(ev.attendee_list ?? []).toHaveLength(0)

  // A typed time on a clicked day: that day at that time, and the conflict line.
  const p = await slotPoint(page, dayOf(3), 20)
  await page.mouse.click(p.x, p.y)
  await line(page).fill('Prep 2pm')
  await expect(quick(page).locator('.cal-clash')).toContainText('Overlaps Budget review')
  await line(page).press('Enter')
  // The save is a request of its own: wait for the event, not only for the Enter.
  await expect(quick(page)).toHaveCount(0)
  await expect.poll(async () => (await agenda(page.request)).some((e) => e.summary === 'Prep')).toBe(true)
  const prep = (await agenda(page.request)).find((e) => e.summary === 'Prep')!
  expect(hhmm(prep.start)).toBe('14:00')
  expect(prep.start.slice(0, 10) <= dayOf(4)).toBe(true)
})

test('Ctrl+Enter opens the editor with everything; typed fields, repeat and reminders save', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  // "tom" below means Friday: the browser's day is Thursday whatever day the run falls on.
  await pinNow(page, 3, 12)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(page.getByTestId('cal-event').first()).toBeVisible()
  await page.keyboard.press('c')
  await line(page).fill('Planning next fri 10am at Café Luna')
  await line(page).press('Control+Enter')
  const ed = page.getByTestId('cal-editor')
  await expect(ed).toBeVisible()
  await expect(ed.getByRole('textbox', { name: 'Title' })).toHaveValue('Planning')
  await expect(ed.getByLabel('Location')).toHaveValue('Café Luna')
  await expect(ed.getByRole('textbox', { name: 'Start time' })).toHaveValue(/10:00/)

  // The typed when fields: "3p", ↑ moves by the snap, the end follows and keeps the length.
  const start = ed.getByRole('textbox', { name: 'Start time' })
  await start.fill('3p')
  await start.press('Enter')
  await expect(start).toHaveValue(/3:00/)
  await expect(ed.getByRole('textbox', { name: 'End time' })).toHaveValue(/4:00/)
  await start.press('ArrowDown')
  await expect(start).toHaveValue(/3:15/)
  // An invalid date turns red and is not taken.
  const startDate = ed.getByRole('textbox', { name: 'Start date' })
  await startDate.fill('blorp')
  await startDate.press('Enter')
  await expect(startDate).toHaveAttribute('aria-invalid', 'true')
  await startDate.fill('tom')
  await startDate.press('Enter')
  await expect(startDate).not.toHaveAttribute('aria-invalid', 'true')

  // The repeat options: weekly, with a live summary.
  const repeat = ed.getByRole('combobox', { name: 'Repeat' })
  const options = await repeat.locator('option').allTextContents()
  await repeat.selectOption({ label: options.find((o) => o.startsWith('Weekly on'))! })
  await expect(ed.locator('.cal-repeat-summary')).toContainText('next:')
  // Reminders as offsets before the start. A new event starts with the user's default (10 minutes before, as
  // nobody changed it); a second one is added to it.
  await expect(ed.getByRole('button', { name: '10 min before' })).toHaveAttribute('aria-pressed', 'true')
  await ed.getByRole('button', { name: '1 day before' }).click()
  await ed.getByTestId('cal-editor-save').click()
  await expect(ed).toHaveCount(0)
  const ev = (await agenda(page.request, dayOf(0), dayOf(30))).find((e) => e.summary === 'Planning')!
  expect(hhmm(ev.start)).toBe('15:15')
  expect(ev.location).toBe('Café Luna')
  expect(ev.recurring).toBe(true)
  expect([...ev.reminders].sort((a, b) => a - b)).toEqual([10, 1440])
  expect(ev.start.slice(0, 10)).toBe(dayOf(4))
})

test('a new event: untouched default chips write the default; removing them all saves it silent', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await pinNow(page, 3, 12)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(page.getByTestId('cal-event').first()).toBeVisible()
  const ed = page.getByTestId('cal-editor')
  for (const [title, touch] of [['Default chips', false], ['Silent chips', true]] as const) {
    await page.keyboard.press('c')
    await line(page).fill(`${title} tomorrow 3pm`)
    await line(page).press('Control+Enter')
    await expect(ed).toBeVisible()
    // The user's default (10 minutes before) shows as a chip.
    const chip = ed.getByRole('button', { name: '10 min before' })
    await expect(chip).toHaveAttribute('aria-pressed', 'true')
    if (touch) {
      await chip.click()
      await expect(chip).toHaveAttribute('aria-pressed', 'false')
    }
    await ed.getByTestId('cal-editor-save').click()
    await expect(ed).toHaveCount(0)
  }
  const got = await agenda(page.request, dayOf(0), dayOf(30))
  const plain = got.find((e) => e.summary === 'Default chips')!
  expect(plain.reminders).toEqual([10])
  expect(plain.no_reminders).toBe(false)
  // [] from the editor marks the event "no reminders": not even the default fires.
  const silent = got.find((e) => e.summary === 'Silent chips')!
  expect(silent.reminders).toEqual([])
  expect(silent.no_reminders).toBe(true)
})

test('the editor keeps a draft until it is saved or discarded', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
  const ed = page.getByTestId('cal-editor')
  await ed.getByRole('textbox', { name: 'Title' }).fill('Budget review (draft)')
  await page.waitForTimeout(600)
  await page.reload()
  await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).first().waitFor()
  await page.keyboard.press('Escape')
  await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
  await expect(ed.getByRole('textbox', { name: 'Title' })).toHaveValue('Budget review (draft)')
  await expect(ed.getByText('Draft restored')).toBeVisible()
  // Cancel asks first, because something changed.
  await ed.getByRole('button', { name: 'Cancel' }).click()
  await page.getByRole('dialog', { name: 'Discard changes?' }).getByRole('button', { name: 'Discard' }).click()
  await expect(ed).toHaveCount(0)
})

// A click on empty space while quick create is open only closes it; the next click creates.
test('a click away from quick create closes it without opening another', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(page.getByTestId('cal-event').first()).toBeVisible()
  const p = await slotPoint(page, dayOf(2), 8, 10)
  await page.mouse.click(p.x, p.y)
  await expect(quick(page)).toBeVisible()
  const q = await slotPoint(page, dayOf(0), 15, 10)
  expect(await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest('.cal-floating'), [q.x, q.y])).toBe(false)
  await page.mouse.click(q.x, q.y)
  await expect(quick(page)).toHaveCount(0)
  await page.mouse.click(q.x, q.y)
  await expect(quick(page)).toBeVisible()
  await expect(quick(page)).toContainText('3:00')
})
