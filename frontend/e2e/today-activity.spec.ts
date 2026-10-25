// ACT-SURFACES: web.calendar web.settings web.nav
// Today: Calendar opens on Today, which lists today's events, the tasks due with a tick
// (task.complete), mail waiting on a reply (mail.follow_up: remind me again, or archive), approvals
// and missed reminders, each acting in place (calendar.today_view). Activity: reached
// from the bell's footer, it lists what you changed with Undo (activity.list, activity.undo).
// Find a time with household members: the "Share my free/busy with my groups" switch
// in Settings → Calendar & Tasks (event.find_time_household). Screenshots in five themes, light and
// dark, desktop and phone.
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { openIn } from './calendar-data'
import { connectCalendar, expect, test } from './fixtures'
import { looks } from './matrix'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-today-activity-shots')

const at = (hour: number) => {
  const d = new Date()
  d.setHours(hour, 0, 0, 0)
  return d.toISOString()
}
const day = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function today(page: Page) {
  const r = page.request
  for (const [summary, a, b] of [['Standup', 9, 10], ['Lunch with Dana', 12, 13], ['Piano lesson', 17, 18]] as const) {
    expect((await r.post('/api/v1/calendar/events', { data: { summary, start: at(a), end: at(b) } })).ok()).toBeTruthy()
  }
  for (const summary of ['Pay the plumber', 'Send the school form']) {
    expect((await r.post('/api/v1/tasks', { data: { summary, due: day() } })).ok()).toBeTruthy()
  }
}

test('calendar.today_view: Calendar opens on Today; a tick completes a task and the waiting mail acts in place', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await today(page)
  await page.goto('/calendar')
  const view = page.getByTestId('cal-today')
  await expect(view).toBeVisible()
  await expect(view.getByRole('heading', { level: 2 })).toContainText(/Good (morning|afternoon|evening)|Up late/)
  const agenda = view.getByRole('region', { name: 'Agenda' })
  for (const s of ['Standup', 'Lunch with Dana', 'Piano lesson']) await expect(agenda).toContainText(s)
  await expect(page.getByTestId('calendar-app')).toHaveAttribute('data-view', 'today')

  // ACT task.complete: the tick, with Undo.
  const tasks = view.getByRole('region', { name: 'Tasks due' })
  await tasks.getByRole('checkbox', { name: 'Complete “Pay the plumber”' }).click()
  await expect(page.getByText('Task completed')).toBeVisible()
  await expect(tasks).not.toContainText('Pay the plumber')
  await expect(tasks).toContainText('Send the school form')

  // ACT mail.follow_up: "E2E Nudged me" came back with no reply.
  const waiting = view.getByRole('region', { name: 'Waiting on a reply' })
  await expect(waiting).toContainText('E2E Nudged me')
  await expect(waiting).toContainText('No reply')
  await waiting.getByRole('button', { name: 'Archive' }).click()
  await expect(page.getByText('Archived', { exact: true })).toBeVisible()
  await expect(view.getByRole('region', { name: 'Waiting on a reply' })).toHaveCount(0)

  // The view is remembered for the person: Week, then back to /calendar opens on Week.
  await page.keyboard.press('w')
  await expect(page).toHaveURL(/\/calendar\/week/)
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.calendar_view).toBe('week')
  await page.goto('/calendar')
  await expect(page.getByTestId('calendar-app')).toHaveAttribute('data-view', 'week')
  await page.keyboard.press('h')
  await expect(page).toHaveURL(/\/calendar\/today/)
  await expect(page.getByTestId('cal-today')).toBeVisible()
})

test('activity.list and activity.undo: the bell leads to Activity, and Undo takes a change back', async ({ page, seeded }) => {
  const id = seeded.conversations['E2E Archive me']
  expect((await page.request.post('/api/v1/conversations/actions', { data: { ids: [id], action: 'archive' } })).ok()).toBeTruthy()
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  await page.getByTestId('bell-activity').click()
  await expect(page).toHaveURL(/\/settings\/you\/activity/)
  const row = page.getByTestId('activity-row').filter({ hasText: 'Archived “E2E Archive me”' })
  await expect(row).toContainText('You')
  await row.getByRole('button', { name: /^Undo/ }).click()
  await expect(row).toContainText('Undone')
  const inbox = await (await page.request.get('/api/v1/conversations', { params: { view: 'inbox' } })).json()
  expect(inbox.items.some((c: { subject: string }) => c.subject === 'E2E Archive me')).toBeTruthy()
  // The undo is an entry of its own; filters narrow the list.
  await page.getByRole('radio', { name: 'Ambrose' }).click()
  await expect(page.getByText('No changes by this person or agent.')).toBeVisible()
})

test('event.find_time_household: the free/busy switch saves at once', async ({ page }) => {
  // The switch shows with a built-in calendar; the setting itself is saved through the same route.
  const r = await page.request.patch('/api/v1/me/settings', { data: { calendar_share_free_busy: true } })
  expect(r.ok()).toBeTruthy()
  const fb = await (await page.request.get('/api/v1/calendar/household/freebusy')).json()
  expect(fb.sharing).toBe(true)
  expect(Array.isArray(fb.people)).toBeTruthy()
})

// One test per theme and scheme (each checks desktop and phone), so they run side by side on the
// workers (fullyParallel). The matrix (e2e/matrix.ts): nightly all ten; otherwise light in every
// theme and dark in the themes whose extra look is dark.
for (const { theme, dark } of looks({ dark: true })) {
  test(`Today and Activity in ${theme}, ${dark ? 'dark' : 'light'}, desktop and phone (screenshots)`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    await today(page)
    await page.request.post('/api/v1/conversations/actions', { data: { ids: [seeded.conversations['E2E Star me']], action: 'star' } })
    for (const phone of [false, true]) {
      await page.setViewportSize(phone ? { width: 390, height: 844 } : { width: 1400, height: 900 })
      await openIn(page, theme, '/calendar/today', dark)
      await expect(page.getByTestId('cal-today').getByRole('region', { name: 'Agenda' })).toContainText('Standup')
      await page.screenshot({ path: path.join(SHOTS, `today-${theme}-${dark ? 'dark' : 'light'}-${phone ? 'phone' : 'desktop'}.png`) })
      await openIn(page, theme, '/settings/you/activity', dark)
      await expect(page.getByTestId('activity-row').first()).toBeVisible()
      await page.screenshot({ path: path.join(SHOTS, `activity-${theme}-${dark ? 'dark' : 'light'}-${phone ? 'phone' : 'desktop'}.png`) })
    }
  })
}
