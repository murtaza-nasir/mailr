// ACT-SURFACES: web.editor web.calendar
// Changing events on the grid: drag a block to move it (across
// days), drag its edge to resize, a series asks for the scope, Shift+arrows is the keyboard form,
// Alt-drag duplicates, delete is delayed with an undo, `z` undoes, and Find a time offers slots.
import type { Page } from '@playwright/test'
import { apiSettled, expect, test } from './fixtures'
import { themes } from './matrix'
import { agenda, dayOf, drag, hhmm, isoDay, openIn, seedWeek, slotPoint } from './calendar-data'

const event = (page: Page, name: string) => page.getByTestId('cal-event').filter({ hasText: name }).first()
const hourH = (page: Page) => page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.cal-tg')!).getPropertyValue('--hour-h')))

async function blockTop(page: Page, name: string) {
  const b = (await event(page, name).boundingBox())!
  return { x: b.x + b.width / 2, y: b.y + Math.min(8, b.height / 3), box: b }
}

// The grid's drag logic is shared; the editor is a modal (Classic), a pane (Office) or a sheet
// (Cards). Nightly every theme (e2e/matrix.ts).
for (const theme of themes({ release: ['classic', 'office', 'cards'] })) {
  test(`${theme}: drag moves an event to another day and time, with undo`, async ({ page, seeded }) => {
    await seedWeek(page.request, seeded)
    await openIn(page, theme, `/calendar/week/${dayOf(3)}`)
    // The target first: placing it scrolls the grid, and 14:00 stays in view.
    const to = await slotPoint(page, dayOf(4), 10)
    const from = await blockTop(page, 'Budget review')
    // ACT event.reschedule: Thu 14:00 → Fri 10:00 (the press point is 8 px into the block).
    await drag(page, from, { x: to.x, y: to.y + 8 })
    await expect(page.locator('.toast').filter({ hasText: 'Moved to' })).toBeVisible()
    await expect.poll(async () => {
      const e = (await agenda(page.request)).find((x) => x.summary === 'Budget review')!
      return `${isoDay(new Date(e.start))} ${hhmm(e.start)}-${hhmm(e.end)}`
    }).toBe(`${dayOf(4)} 10:00-11:00`)
    // Undo puts it back.
    await page.locator('.toast').filter({ hasText: 'Moved to' }).getByRole('button', { name: 'Undo' }).click()
    await expect.poll(async () => hhmm((await agenda(page.request)).find((x) => x.summary === 'Budget review')!.start)).toBe('14:00')
  })
}

test('resize by the bottom edge, at least five minutes, snapped', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await slotPoint(page, dayOf(2), 14)
  const b = (await event(page, 'Writing block').boundingBox())!
  const h = await hourH(page)
  // ACT event.resize: 13:00–16:00 → 13:00–17:00.
  await drag(page, { x: b.x + b.width / 2, y: b.y + b.height - 3 }, { x: b.x + b.width / 2, y: b.y + b.height - 3 + h })
  await expect.poll(async () => {
    const e = (await agenda(page.request)).find((x) => x.summary === 'Writing block')!
    return `${hhmm(e.start)}-${hhmm(e.end)}`
  }).toBe('13:00-17:00')
})

test('a series asks for the scope; "This event" moves one occurrence', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await slotPoint(page, dayOf(0), 9)
  const from = await blockTop(page, 'Team sync')
  const h = await hourH(page)
  await drag(page, from, { x: from.x, y: from.y + h * 2 })
  const scope = page.getByRole('dialog', { name: 'Move recurring event' })
  await expect(scope).toBeVisible()
  await expect(scope.getByRole('radio', { name: 'This event' })).toBeChecked()
  await scope.getByTestId('scope-ok').click()
  await expect.poll(async () => {
    const all = (await agenda(page.request, dayOf(0), dayOf(14))).filter((x) => x.summary === 'Team sync').map((x) => hhmm(x.start))
    return all.slice(0, 2).join(',')
  }).toBe('11:00,09:00')
})

test('keyboard drag: arrows to an event, Shift+↓ twice, Enter', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/day/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  // The events hold one Tab stop; → walks them in time order (calendar.nextEvent).
  await page.locator('[data-testid="cal-event"][tabindex="0"]').first().focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.locator('.cal-block.focused')).toHaveCount(1)
  while (!(await page.locator('.cal-block.focused').innerText()).includes('Budget review')) await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Shift+ArrowDown')
  await page.keyboard.press('Shift+ArrowDown')
  await expect(page.locator('.cal-ghost.kbd')).toContainText('2:30–3:30 PM')
  await page.keyboard.press('Enter')
  await expect.poll(async () => hhmm((await agenda(page.request)).find((x) => x.summary === 'Budget review')!.start)).toBe('14:30')
  // Esc cancels a keyboard drag.
  await event(page, 'Quick call').focus()
  await page.keyboard.press('Shift+ArrowUp')
  await expect(page.locator('.cal-ghost.kbd')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.cal-ghost.kbd')).toHaveCount(0)
})

test('delete waits 7 s with Undo; z undoes too; then it is gone', async ({ page, seeded }) => {
  // The page's own clock: the 7 s undo windows pass at once (runFor), on the page's timers.
  await page.clock.install()
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  // ACT event.delete from the detail.
  await event(page, 'Quick call').click()
  await page.getByTestId('cal-detail').getByRole('button', { name: 'Delete (#)' }).click()
  await expect(event(page, 'Quick call')).toHaveCount(0)
  await page.locator('.toast').filter({ hasText: 'Event deleted' }).getByRole('button', { name: 'Undo' }).click()
  await expect(event(page, 'Quick call')).toBeVisible()
  // The undo window passes; a delete it still sent would be in flight now.
  await page.clock.runFor(7500)
  await apiSettled(page)
  expect((await agenda(page.request)).some((e) => e.summary === 'Quick call')).toBe(true)

  // # on the focused event, then z.
  await event(page, 'Quick call').focus()
  await page.keyboard.press('#')
  await expect(event(page, 'Quick call')).toHaveCount(0)
  await page.keyboard.press('z')
  await expect(event(page, 'Quick call')).toBeVisible()

  // Deleting for real.
  await event(page, 'Quick call').focus()
  await page.keyboard.press('Delete')
  await expect(event(page, 'Quick call')).toHaveCount(0)
  await page.clock.runFor(7500)
  await expect.poll(async () => (await agenda(page.request)).some((e) => e.summary === 'Quick call')).toBe(false)
})

test('Alt-drag duplicates; timed into the all-day strip becomes all-day', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await slotPoint(page, dayOf(3), 14)
  const from = await blockTop(page, 'Budget review')
  const h = await hourH(page)
  // ACT event.duplicate
  await drag(page, from, { x: from.x, y: from.y + h * 3 }, { alt: true })
  await expect.poll(async () => (await agenda(page.request)).filter((e) => e.summary === 'Budget review').map((e) => hhmm(e.start)).sort().join(',')).toBe('14:00,17:00')

  // ACT event.edit: dragging into the all-day strip makes it all-day.
  await slotPoint(page, dayOf(3), 16)
  const quickCall = await blockTop(page, 'Quick call')
  const strip = (await page.locator('.cal-strip').boundingBox())!
  await drag(page, quickCall, { x: quickCall.x, y: strip.y + strip.height / 2 })
  await expect.poll(async () => (await agenda(page.request)).find((e) => e.summary === 'Quick call')?.all_day).toBe(true)
})

test('Find a time (f) offers free slots; one opens quick create there', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  // ACT event.find_time
  await page.keyboard.press('f')
  const dialog = page.getByRole('dialog', { name: 'Find a time' })
  await expect(dialog).toBeVisible()
  const slot = dialog.locator('.cal-ft-slot').first()
  await expect(slot).toBeVisible()
  await expect(dialog.locator('.cal-ft-row').first()).toBeVisible()
  await slot.click()
  await expect(page.getByTestId('cal-quick')).toBeVisible()
})
