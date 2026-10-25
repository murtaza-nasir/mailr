// ACT-SURFACES: web.calendar
// Arrow keys between events, Tab left to the browser, in the Day,
// Week, Month and Schedule views: ← and → step to the previous and next event; ↑ and ↓ step within
// the day column (Day, Week), a week back or on (Month), or to the previous and next row
// (Schedule). Tab and Shift+Tab never step between events: the events hold one Tab stop. A focused
// control keeps its own arrow keys.
import type { Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'
import { createEvent, isoDay, openIn } from './calendar-data'

// A fixed fortnight, so the month grid holds both weeks on any test day.
const day = (d: number, h = 0, m = 0) => new Date(2026, 10, d, h, m)
const iso = (d: number, h: number, m = 0) => day(d, h, m).toISOString()

async function seed(page: Page, seeded: Parameters<typeof connectCalendar>[1]) {
  await connectCalendar(page.request, seeded)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  await createEvent(page.request, { summary: 'Offsite', start: isoDay(day(10)), end: isoDay(day(11)), all_day: true })
  await createEvent(page.request, { tz, summary: 'Alpha', start: iso(10, 9), end: iso(10, 10) })
  await createEvent(page.request, { tz, summary: 'Beta', start: iso(10, 11), end: iso(10, 12) })
  await createEvent(page.request, { tz, summary: 'Delta', start: iso(11, 10), end: iso(11, 11) })
  await createEvent(page.request, { tz, summary: 'Gamma', start: iso(17, 9), end: iso(17, 10) })
}

const ev = (page: Page, name: string) => page.getByTestId('cal-event').filter({ hasText: name }).first()

async function press(page: Page, key: string, expected: string) {
  await page.keyboard.press(key)
  await expect(ev(page, expected)).toBeFocused()
}

/** Tab leaves the events and Shift+Tab comes back to the same one: one Tab stop. */
async function tabLeaves(page: Page, from: string) {
  await ev(page, from).focus()
  await page.keyboard.press('Tab')
  await expect.poll(() => page.evaluate(() => document.activeElement?.getAttribute('data-testid') ?? '')).not.toBe('cal-event')
  await page.keyboard.press('Shift+Tab')
  await expect(ev(page, from)).toBeFocused()
}

test('Day: ← → between events, ↑ ↓ within the day, Tab leaves (calendar.nextEvent)', async ({ page, seeded }) => {
  await seed(page, seeded)
  await openIn(page, 'classic', `/calendar/day/${isoDay(day(10))}`)
  await expect(ev(page, 'Beta')).toBeVisible()
  await ev(page, 'Alpha').focus()
  await press(page, 'ArrowRight', 'Beta')
  await press(page, 'ArrowLeft', 'Alpha')
  await press(page, 'ArrowUp', 'Offsite')
  await press(page, 'ArrowDown', 'Alpha')
  await press(page, 'ArrowDown', 'Beta')
  await tabLeaves(page, 'Beta')
  // Shift+arrows still move the focused event (the keyboard drag), not the focus.
  await page.keyboard.press('Shift+ArrowDown')
  await expect(page.locator('.cal-ghost.kbd')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.cal-ghost.kbd')).toHaveCount(0)
})

test('Week: → crosses to the next day, ↓ stays in the column', async ({ page, seeded }) => {
  await seed(page, seeded)
  await openIn(page, 'classic', `/calendar/week/${isoDay(day(10))}`)
  await expect(ev(page, 'Delta')).toBeVisible()
  await ev(page, 'Alpha').focus()
  await press(page, 'ArrowDown', 'Beta')
  await press(page, 'ArrowRight', 'Delta')
  // Delta is alone in its column: ↓ stays on it.
  await page.keyboard.press('ArrowDown')
  await expect(ev(page, 'Delta')).toBeFocused()
  await press(page, 'ArrowLeft', 'Beta')
  await press(page, 'ArrowUp', 'Alpha')
  await press(page, 'ArrowUp', 'Offsite')
  await tabLeaves(page, 'Alpha')
  // With no event focused, the arrows move the time slot, as before.
  await page.keyboard.press('Escape')
  await page.locator('.cal-view').focus()
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.cal-block.focused')).toHaveCount(0)
})

test('Month: ← → between events, ↓ ↑ a week on and back', async ({ page, seeded }) => {
  await seed(page, seeded)
  await openIn(page, 'classic', `/calendar/month/${isoDay(day(10))}`)
  await expect(ev(page, 'Gamma')).toBeVisible()
  await ev(page, 'Alpha').focus()
  await press(page, 'ArrowRight', 'Beta')
  await press(page, 'ArrowRight', 'Delta')
  await press(page, 'ArrowLeft', 'Beta')
  await press(page, 'ArrowLeft', 'Alpha')
  await press(page, 'ArrowDown', 'Gamma')
  await press(page, 'ArrowUp', 'Offsite')
  await tabLeaves(page, 'Offsite')
})

test('Schedule: every arrow steps through the rows; Tab leaves', async ({ page, seeded }) => {
  await seed(page, seeded)
  await openIn(page, 'classic', `/calendar/schedule/${isoDay(day(10))}`)
  await expect(ev(page, 'Delta')).toBeVisible()
  await ev(page, 'Alpha').focus()
  await press(page, 'ArrowRight', 'Beta')
  await press(page, 'ArrowDown', 'Delta')
  await press(page, 'ArrowUp', 'Beta')
  await press(page, 'ArrowLeft', 'Alpha')
  await tabLeaves(page, 'Alpha')
})

test('a focused control keeps its arrow keys; the help lists the arrows, not Tab', async ({ page, seeded }) => {
  await seed(page, seeded)
  await openIn(page, 'classic', `/calendar/month/${isoDay(day(10))}`)
  await expect(ev(page, 'Alpha')).toBeVisible()
  const today = page.getByRole('button', { name: /^Today/ }).first()
  await today.focus()
  await page.keyboard.press('ArrowRight')
  await expect(today).toBeFocused()
  await expect(page.locator('[data-testid="cal-event"].focused')).toHaveCount(0)

  await page.locator('.cal-view').focus()
  await page.keyboard.press('?')
  const help = page.getByRole('dialog', { name: 'Calendar shortcuts' })
  await expect(help.getByRole('row', { name: /Next event/ })).toContainText('→')
  await expect(help.getByRole('row', { name: /Previous event/ })).toContainText('←')
  await expect(help.getByRole('row', { name: /Event below/ })).toContainText('↓')
  await expect(help.getByRole('row', { name: /Next period/ })).not.toContainText('→')
  await expect(help.getByRole('row', { name: /Next event/ })).not.toContainText('Tab')
})
