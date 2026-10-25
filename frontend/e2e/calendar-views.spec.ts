// ACT-SURFACES: web.calendar
// The Calendar app's views and frame in every theme: Day, N days,
// Work week, Week, Month and Schedule with their keys; period navigation and the month picker; the
// event detail as each theme presents it (popover, pane, card, sheet); visibility toggles with
// Show only and the all-hidden state; search with highlighted results; the phone layout.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { themes } from './matrix'
import { dayOf, openIn, pinNow, seedWeek } from './calendar-data'

const app = (page: Page) => page.getByTestId('calendar-app')
const event = (page: Page, name: string) => page.getByTestId('cal-event').filter({ hasText: name }).first()

// The views are shared; the event detail is a popover (Classic, Desk), a pane (Office), a card
// (Focus) or a sheet (Cards). Nightly every theme (e2e/matrix.ts); otherwise one of each.
for (const theme of themes({ release: ['classic', 'office', 'focus', 'cards'] })) {
  test.describe(`${theme} theme`, () => {
    test('every view, by key and by the switcher, with navigation', async ({ page, seeded }) => {
      await seedWeek(page.request, seeded)
      await openIn(page, theme, `/calendar/week/${dayOf(3)}`)
      await expect(app(page)).toHaveAttribute('data-view', 'week')
      await expect(event(page, 'Budget review')).toBeVisible()
      await expect(event(page, 'Analytics conference')).toBeVisible()
      await expect(page.getByTestId('cal-timegrid')).toHaveAttribute('data-days', '7')

      // d, x, o, m, a, w: Day, N days, Work week, Month, Schedule, Week.
      await page.keyboard.press('d')
      await expect(app(page)).toHaveAttribute('data-view', 'day')
      await expect(page.getByTestId('cal-timegrid')).toHaveAttribute('data-days', '1')
      await expect(event(page, 'Budget review')).toBeVisible()
      await page.keyboard.press('x')
      await expect(app(page)).toHaveAttribute('data-view', 'ndays')
      await expect(page.getByTestId('cal-timegrid')).toHaveAttribute('data-days', '3')
      await page.keyboard.press('o')
      await expect(page.getByTestId('cal-timegrid')).toHaveAttribute('data-days', '5')
      await page.keyboard.press('m')
      await expect(page.getByTestId('cal-month')).toBeVisible()
      await expect(page.getByTestId('cal-title')).toContainText(/2026|2027/)
      await expect(event(page, 'Team sync')).toBeVisible()
      await page.keyboard.press('a')
      await expect(page.getByTestId('cal-schedule')).toBeVisible()
      await expect(page.getByTestId('cal-schedule').getByText('Day 2 of 3').first()).toBeVisible()
      await page.keyboard.press('w')
      await expect(app(page)).toHaveAttribute('data-view', 'week')

      // j / k move by a period; t comes back.
      const title = await page.getByTestId('cal-title').innerText()
      await page.keyboard.press('j')
      await expect(page.getByTestId('cal-title')).not.toHaveText(title)
      await page.keyboard.press('k')
      await expect(page.getByTestId('cal-title')).toHaveText(title)
      await page.keyboard.press('j')
      await page.keyboard.press('t')
      await expect(page).toHaveURL(/\/calendar\/week$/)

      // The header's switcher (a menu when the app is narrow) reaches the same views.
      // Office leaves them to its ribbon.
      const tabs = page.getByTestId('cal-views')
      if (theme === 'office') await page.locator('.ribbon').getByRole('button', { name: /^Month/ }).first().click()
      else if (await tabs.isVisible()) await tabs.getByRole('tab', { name: 'Month' }).click()
      else {
        await page.locator('.cal-viewchip').click()
        await page.getByRole('menuitemcheckbox', { name: /Month/ }).click()
      }
      await expect(app(page)).toHaveAttribute('data-view', 'month')

      // The title opens a month picker.
      await page.getByTestId('cal-title').click()
      await page.getByRole('dialog', { name: 'Go to month' }).getByRole('button', { name: 'Dec' }).click()
      await expect(page).toHaveURL(/\/calendar\/month\/\d{4}-12-01/)
    })

    test('the event detail as the theme shows it', async ({ page, seeded }) => {
      await seedWeek(page.request, seeded)
      await openIn(page, theme, `/calendar/week/${dayOf(3)}`)
      await event(page, 'Budget review').click()
      const detail = page.getByTestId('cal-detail')
      await expect(detail).toBeVisible()
      await expect(detail.getByRole('heading', { name: 'Budget review' })).toBeVisible()
      await expect(detail.getByRole('link', { name: 'Join' })).toHaveAttribute('href', /meet\.jit\.si/)
      await expect(detail.getByRole('link', { name: 'https://example.org/agenda' })).toBeVisible()
      await expect(page).toHaveURL(/\/calendar\/event\//)
      const where = { classic: '.cal-floating', desk: '.cal-floating', office: '.cal-pane', focus: '.cal-card', cards: '.cal-sheet' }[theme]
      await expect(page.locator(where).getByTestId('cal-detail')).toBeVisible()
      // The detail takes focus, so its own keys work (e edits; Esc closes the editor again).
      if (theme !== 'office') {
        await expect(page.getByTestId('cal-detail')).toBeFocused()
        await page.keyboard.press('e')
        await expect(page.getByTestId('cal-editor')).toBeVisible()
        await page.getByTestId('cal-editor').getByRole('button', { name: 'Cancel' }).click()
        await event(page, 'Budget review').click()
      }
      // The deep link reloads to the same detail.
      await page.reload()
      await expect(page.getByTestId('cal-detail').getByRole('heading', { name: 'Budget review' })).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(page.getByTestId('cal-detail')).toHaveCount(0)
      await expect(page).toHaveURL(/\/calendar\/week/)
    })
  })
}

test('visibility: toggle, Alt+click shows only one, all hidden, Show all', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  // A second calendar, when the server can make one; otherwise the one calendar is enough.
  await page.request.post('/api/v1/calendar/collections', { data: { name: 'Work', kind: 'events' } }).catch(() => null)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  const personal = page.getByRole('checkbox', { name: 'Show Personal' })
  // ACT calendar.toggle_visibility
  await personal.uncheck()
  await expect(event(page, 'Budget review')).toHaveCount(0)
  const calendars = await page.getByTestId('cal-calendar').count()
  if (calendars === 1) await expect(page.getByTestId('cal-all-hidden')).toBeVisible()
  // Each change saves on the server; the next one waits for it, or a late reply shows the old state.
  const hiddenSaved = (hidden: boolean) => page.waitForResponse((r) => r.request().method() === 'PATCH'
    && r.url().includes('/calendar/collections/') && r.request().postDataJSON()?.hidden === hidden, { timeout: 30_000 }).catch(() => null)
  let saved = hiddenSaved(false)
  await personal.check()
  await expect(event(page, 'Budget review')).toBeVisible()
  await saved
  // ACT calendar.show_only: Alt+click keeps only this calendar. It saves each calendar in turn and
  // ends by showing this one.
  saved = hiddenSaved(false)
  await personal.click({ modifiers: ['Alt'] })
  await expect(personal).toBeChecked()
  await expect(event(page, 'Budget review')).toBeVisible()
  await saved
  // The setting survives a reload (this device, or the server when it keeps visibility).
  saved = hiddenSaved(true)
  // A refetch of the calendars that started before the click can still land once and show the old state.
  await expect(async () => {
    if (await personal.isChecked()) await personal.uncheck({ timeout: 2000 })
    await expect(personal).not.toBeChecked({ timeout: 1000 })
  }).toPass()
  await saved
  await page.reload()
  await expect(page.getByRole('checkbox', { name: 'Show Personal' })).not.toBeChecked()
  if (calendars === 1) {
    await page.getByTestId('cal-all-hidden').getByRole('button', { name: 'Show all' }).click()
    await expect(event(page, 'Budget review')).toBeVisible()
  }
})

// ACT calendar.toggle_visibility under a slow network (the calendar-views race, 10 Oct 2026): the
// first save is held on its way to the server while the person clicks again. The box follows the
// last click at once and stays, and the saves reach the server in click order, so it ends on the
// last click.
test('visibility: the last click wins while an earlier save is slow', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  const personal = page.getByRole('checkbox', { name: 'Show Personal' })
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
  await personal.uncheck()
  await firstOut
  // check() fails when the click does not change the box in the same event.
  await personal.check()
  await expect(event(page, 'Budget review')).toBeVisible()
  const lastSaved = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/calendar/collections/')
    && r.request().postDataJSON()?.hidden === false)
  release()
  await lastSaved
  // The refetch after the saves shows the server's state: the last click.
  await page.waitForResponse((r) => r.request().method() === 'GET' && r.url().includes('/calendar/calendars'))
  await expect(personal).toBeChecked()
  expect(sent).toEqual([true, false])
  const cals = await (await page.request.get('/api/v1/calendar/calendars')).json() as { name: string; hidden?: boolean }[] | { items: { name: string; hidden?: boolean }[] }
  const list = Array.isArray(cals) ? cals : cals.items
  expect(list.find((c) => c.name === 'Personal')?.hidden ?? false).toBe(false)
  await page.reload()
  await expect(page.getByRole('checkbox', { name: 'Show Personal' })).toBeChecked()
})

// ACT calendar.show_only with a slow save (release e2e, 10 Oct 2026): both boxes change at once, and
// the saves hide the other calendars first and end by showing this one, also when hiding is slow.
test('visibility: Show only saves the others first and ends by showing this one', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const made = await page.request.post('/api/v1/calendar/collections', { data: { name: 'Work', kind: 'events' } })
  test.skip(!made.ok(), 'this server cannot make a second calendar')
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  const personal = page.getByRole('checkbox', { name: 'Show Personal' })
  const work = page.getByRole('checkbox', { name: 'Show Work' })
  // Personal starts hidden, so Show only both hides Work and shows Personal.
  await personal.uncheck()
  await expect.poll(async () => {
    const cals = await (await page.request.get('/api/v1/calendar/calendars')).json() as { name: string; hidden?: boolean }[] | { items: { name: string; hidden?: boolean }[] }
    return (Array.isArray(cals) ? cals : cals.items).find((c) => c.name === 'Personal')?.hidden
  }).toBe(true)
  const log: string[] = []
  await page.route('**/api/v1/calendar/collections/*', async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    const hidden = route.request().postDataJSON()?.hidden
    log.push(`sent ${hidden}`)
    // Hiding is slow.
    if (hidden === true) await new Promise((r) => setTimeout(r, 800))
    await route.continue()
  })
  page.on('response', (r) => {
    if (r.request().method() === 'PATCH' && r.url().includes('/calendar/collections/')) log.push(`saved ${r.request().postDataJSON()?.hidden}`)
  })
  const shown = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/calendar/collections/') && r.request().postDataJSON()?.hidden === false)
  await personal.click({ modifiers: ['Alt'] })
  await expect(personal).toBeChecked()
  await expect(work).not.toBeChecked()
  await shown
  expect(log).toEqual(['sent true', 'saved true', 'sent false', 'saved false'])
  await page.reload()
  await expect(page.getByRole('checkbox', { name: 'Show Personal' })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: 'Show Work' })).not.toBeChecked()
})

test('search: / focuses the top box on Calendar, results show live with highlights, a result opens', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  // Thursday noon: Budget review (Thursday 2 pm) is upcoming whatever day the run falls on.
  await pinNow(page, 3, 12)
  await openIn(page, 'classic', `/calendar/week/${dayOf(3)}`)
  await expect(event(page, 'Budget review')).toBeVisible()
  // ACT event.search
  await page.keyboard.press('/')
  await expect(page.getByTestId('search-input')).toBeFocused()
  await expect(page.getByTestId('search-scope')).toHaveText('Calendar')
  await page.keyboard.type('review')
  await expect(page).toHaveURL(/\/calendar\/search\?q=review/)
  const results = page.getByTestId('cal-search')
  await expect(results.getByText('Budget')).toBeVisible()
  await expect(results.locator('mark').first()).toHaveText(/review/i)
  await expect(results.getByRole('heading', { name: 'Upcoming' })).toBeVisible()
  // Back in the box ends the search.
  await page.getByRole('search').getByRole('button', { name: 'Back' }).click()
  await expect(page).not.toHaveURL(/search/)
  // Again, and a result opens its detail.
  await page.keyboard.press('/')
  await page.keyboard.type('review')
  await expect(page).toHaveURL(/\/calendar\/search\?q=review/)
  await page.getByTestId('cal-search').getByText('Budget').first().click()
  await expect(page.getByTestId('cal-detail')).toBeVisible()
})

test('the phone layout: 3 days, the drawer, a bottom sheet for details', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  await page.setViewportSize({ width: 390, height: 844 })
  await openIn(page, 'classic', `/calendar/ndays/${dayOf(3)}`)
  await expect(page.getByTestId('cal-timegrid')).toHaveAttribute('data-days', '3')
  await expect(page.getByTestId('cal-left')).toHaveCount(0)
  await page.getByRole('button', { name: 'Calendars and mini-month' }).click()
  await expect(page.getByTestId('cal-left')).toBeVisible()
  await page.locator('.cal-drawer-scrim').click({ position: { x: 370, y: 400 } })
  await expect(page.getByTestId('cal-left')).toHaveCount(0)
  await event(page, 'Budget review').click()
  await expect(page.locator('.cal-sheet').getByTestId('cal-detail')).toBeVisible()
  // Nothing scrolls sideways.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})

test('not connected: a full-page prompt to connect', async ({ page }) => {
  await openIn(page, 'classic', '/calendar/week')
  await expect(page.getByTestId('cal-not-connected')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Connect a calendar' })).toHaveAttribute('href', '/settings/calendar?add=1')
})
