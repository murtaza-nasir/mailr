// ACT-SURFACES: web.panel web.settings web.palette
// Calendar and Tasks outside their apps: the side panels as quick glances with
// the apps' rows, detail and quick add (web.panel); the command palette's "New event: …" and "New
// task: …" with the grammar, "Go to <date>", views, calendars, lists and tags (web.palette);
// mail search showing events and tasks above the mail; and the settings that feed the apps
// (web.settings). Writes go to the seeded user's dev-Radicale calendar and are checked through
// the API. Covers: event.create, task.create, task.complete (web.panel); event.create, task.create,
// event.search, event.find_time, calendar.toggle_visibility (web.palette); invite.auto_add (web.settings).
import type { Page } from '@playwright/test'
import { connectCalendar, events, expect, shotsDir, tasks, test } from './fixtures'

const SHOTS = shotsDir('integration')
type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'

async function openPanel(page: Page, name: 'Tasks' | 'Calendar') {
  await page.getByRole('complementary', { name: 'Open beside' }).getByRole('button', { name: `${name} beside` }).click()
  return page.getByRole('complementary', { name, exact: true })
}

const at = (h: number, m = 0, days = 0) => {
  const d = new Date()
  d.setDate(d.getDate() + days)
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}
const isoDay = (days = 0) => {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

async function palette(page: Page) {
  await expect(page.getByTestId('waiting-bell')).toBeVisible()
  await page.keyboard.press('Control+k')
  return page.getByRole('combobox', { name: 'Command' })
}

test('web.panel: the calendar panel shows the day with the app rows, opens the app detail, and quick add creates an event (event.create)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Planning', start: at(23, 0), end: at(23, 30), location: 'Room 4', tz } })
  await page.goto('/mail/inbox')
  const panel = await openPanel(page, 'Calendar')
  await expect(panel.getByTestId('cal-event').filter({ hasText: 'E2E Planning' })).toBeVisible()
  await panel.getByTestId('cal-event').filter({ hasText: 'E2E Planning' }).click()
  await expect(page.getByText('Room 4').first()).toBeVisible()
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/panel-calendar-detail.png` })
  await page.keyboard.press('Escape')

  const line = panel.getByRole('textbox', { name: 'New event' })
  await line.fill('E2E Coffee with Dana tomorrow 3pm for 45 min')
  await expect(panel.locator('.ql-chip').first()).toContainText(/3:00|15:00/)
  await page.screenshot({ path: `${SHOTS}/panel-calendar-quickadd.png` })
  await line.press('Enter')
  await expect.poll(async () => (await events(page.request)).find((e) => e.summary === 'E2E Coffee with Dana')).toBeTruthy()
  const ev = (await events(page.request)).find((e) => e.summary === 'E2E Coffee with Dana')!
  expect(new Date(ev.end!).getTime() - new Date(ev.start).getTime()).toBe(45 * 60_000)
  expect(new Date(ev.start).getHours()).toBe(15)
  await expect(line).toHaveValue('')
})

test('web.panel: the tasks panel adds with the grammar and completes with the app row (task.create, task.complete)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Water plants', due: isoDay(0) } })
  await page.goto('/mail/inbox')
  const panel = await openPanel(page, 'Tasks')
  await expect(panel.getByTestId('task-row').filter({ hasText: 'E2E Water plants' })).toBeVisible()
  const add = panel.getByRole('textbox').first()
  await add.fill('E2E Call the bank tomorrow')
  await add.press('Enter')
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Call the bank')?.due?.slice(0, 10)).toBe(isoDay(1))
  await expect(panel.getByTestId('task-row').filter({ hasText: 'E2E Call the bank' })).toBeVisible()
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/panel-tasks.png` })
  await panel.getByTestId('task-row').filter({ hasText: 'E2E Water plants' }).getByRole('checkbox', { name: 'Complete E2E Water plants' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Water plants')?.status).toBe('completed')
})

test('web.palette: "New task: …" shows the parse and creates; "New event: …" creates (task.create, event.create)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.goto('/mail/inbox')
  let input = await palette(page)
  await input.fill('New task: E2E Pay rent fri')
  const item = page.getByRole('option', { name: /New task: E2E Pay rent/ })
  await expect(item).toBeVisible()
  await expect(item.locator('.pi-chip').first()).toContainText('Due Fri')
  await page.screenshot({ path: `${SHOTS}/palette-new-task.png` })
  await input.press('Enter')
  await expect.poll(async () => (await tasks(page.request)).some((t) => t.summary === 'E2E Pay rent')).toBe(true)

  input = await palette(page)
  await input.fill('New event: E2E Board meeting tomorrow 10am')
  await expect(page.getByRole('option', { name: /New event: E2E Board meeting/ })).toBeVisible()
  await input.press('Enter')
  await expect.poll(async () => (await events(page.request)).find((e) => e.summary === 'E2E Board meeting')?.start).toBeTruthy()
  const ev = (await events(page.request)).find((e) => e.summary === 'E2E Board meeting')!
  expect(new Date(ev.start).getHours()).toBe(10)
})

test('web.palette: Go to a date, switch views, hide a calendar, search events (event.search, calendar.toggle_visibility)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.goto('/mail/inbox')
  let input = await palette(page)
  await input.fill('Go to 12 Dec')
  await expect(page.getByRole('option', { name: /^Go to .*12 Dec/ })).toBeVisible()
  await input.press('Enter')
  await expect(page).toHaveURL(/\/calendar\/(day|week)\/\d{4}-12-12/)

  input = await palette(page)
  await input.fill('month view')
  await page.getByRole('option', { name: /Month/ }).first().click()
  await expect(page).toHaveURL(/\/calendar\/month/)

  input = await palette(page)
  await input.fill('hide calendar')
  await page.getByRole('option', { name: /Hide calendar: Personal/ }).click()
  input = await palette(page)
  await input.fill('calendar: personal')
  await expect(page.getByRole('option', { name: /Show calendar: Personal/ })).toBeVisible()
  await page.getByRole('option', { name: /Show calendar: Personal/ }).click()

  input = await palette(page)
  await input.fill('Invitation')
  await page.getByRole('option', { name: /Search events for “Invitation”/ }).click()
  await expect(page).toHaveURL(/\/calendar\/search\?q=Invitation/)
})

test('web.palette: Find a time opens the Calendar app on its finder (event.find_time)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.goto('/mail/inbox')
  const input = await palette(page)
  await input.fill('Find a time')
  await page.getByRole('option', { name: /^Find a time/ }).click()
  await expect(page).toHaveURL(/\/calendar/)
  const dialog = page.getByRole('dialog', { name: 'Find a time' })
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('.cal-ft-slot').first()).toBeVisible()
})

// ACT event.search @web.list, ACT task.search @web.list
test('web.list: mail search shows the top events and tasks above the mail (event.search, task.search)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Reply to Dana about budget', due: isoDay(2) } })
  await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Budget review', start: at(14, 0, 3), end: at(15, 0, 3) } })
  await page.goto('/mail/search?q=budget')
  const glance = page.getByRole('region', { name: 'Events, tasks and notes' })
  await expect(glance.getByText('E2E Budget review')).toBeVisible()
  await expect(glance.getByText('E2E Reply to Dana about budget')).toBeVisible()
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/search-glance.png` })
  await glance.getByRole('link', { name: 'See all in Calendar' }).click()
  await expect(page).toHaveURL(/\/calendar\/search\?q=budget/)
})

test('web.settings: Calendar & Tasks preferences save to the account (invite.auto_add, calendar.default_reminders)', async ({ page }) => {
  await page.goto('/settings/you/region')
  await page.getByLabel('Week starts on').selectOption('1')
  await page.goto('/settings/calendar')
  await page.getByLabel('Working hours start').fill('08:30')
  await page.getByLabel('Working hours start').blur()
  await page.getByRole('radio', { name: /Add to my calendar at once/ }).click()
  await expect(page.getByRole('radio', { name: /Add to my calendar at once/ })).toBeChecked()
  await page.getByLabel('Default reminder for timed events').selectOption('15')
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.calendar_add_new_invites).toBe('needs_action')
  const s = (await (await page.request.get('/api/v1/me')).json()).settings
  expect(s.week_start).toBe(1)
  expect(s.calendar_working_hours.start).toBe('08:30')
  await expect.poll(async () => (await (await page.request.get('/api/v1/reminders/settings')).json()).default_timed_event).toBe(15)
  await page.screenshot({ path: `${SHOTS}/settings-calendar.png`, fullPage: true })
})

for (const t of ['office', 'focus', 'desk', 'cards'] as Theme[]) {
  test(`web.panel in ${t}: the calendar panel and the palette`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Theme event', start: at(22, 0), end: at(22, 30) } })
    await page.addInitScript((x) => localStorage.setItem('mailr.uiTheme', x), t)
    await page.goto('/mail/inbox')
    const input = await palette(page)
    await input.fill('Calendar beside')
    await input.press('Enter')
    const panel = page.getByRole('complementary', { name: 'Calendar', exact: true })
    await expect(panel.getByTestId('cal-event').filter({ hasText: 'E2E Theme event' })).toBeVisible()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/panel-${t}.png` })
  })
}
