// ACT-SURFACES: web.tasks web.pane
// Tasks app follow-ups: the series question when the due of a repeating task changes
// (ACT task.reschedule), and the task search URL (/tasks/search?q=) that "See all in
// Tasks" opens.
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'
import { mkTask } from './helpers'

interface T { uid: string; summary: string; due: string | null; rrule?: string | null }

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const day = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return iso(d) }
const WD = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA']

async function one(request: APIRequestContext, summary: string): Promise<T | undefined> {
  const j = await (await request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
  return ((Array.isArray(j) ? j : j.items) as T[]).find((t) => t.summary === summary)
}
const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkTask<T>(request, body)
const row = (page: Page, text: string) => page.getByTestId('task-row').filter({ hasText: text })

test('The series question: a new due on a repeating task asks This occurrence or All', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Weekly report', due: day(0), recurrence: 'FREQ=WEEKLY' })
  await mk(page.request, { summary: 'E2E Weekly plan', due: day(0), recurrence: 'FREQ=WEEKLY;BYDAY=' + WD[new Date().getDay()] })
  await page.goto('/tasks/all')
  await expect(row(page, 'E2E Weekly report')).toBeVisible()

  // ACT task.reschedule from the row's due chip; Esc in the question changes nothing.
  await row(page, 'E2E Weekly report').locator('.tk-due').click()
  await page.getByTestId('when-picker').waitFor()
  await page.keyboard.press('m')
  const dlg = page.getByRole('dialog', { name: 'Change a repeating task' })
  await expect(dlg).toBeVisible()
  await dlg.getByRole('button', { name: 'Cancel' }).click()
  await expect(dlg).toHaveCount(0)
  expect((await one(page.request, 'E2E Weekly report'))?.due).toBe(day(0))

  // "This occurrence" (the default): only this one moves; the rule keeps the old weekday.
  await row(page, 'E2E Weekly report').locator('.tk-due').click()
  await page.getByTestId('when-picker').waitFor()
  await page.keyboard.press('m')
  await expect(dlg.getByRole('button', { name: 'This occurrence' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await one(page.request, 'E2E Weekly report'))?.due).toBe(day(1))
  expect((await one(page.request, 'E2E Weekly report'))?.rrule).toContain(`BYDAY=${WD[new Date().getDay()]}`)

  // "All" from the pane: the series' weekday follows the new due.
  const plan = (await one(page.request, 'E2E Weekly plan'))!
  await page.goto(`/tasks/all/task/${encodeURIComponent(plan.uid)}`)
  await page.getByTestId('pane-due').click()
  await page.getByTestId('when-picker').waitFor()
  await page.keyboard.press('m')
  await dlg.getByRole('button', { name: 'All' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Weekly plan'))?.rrule).toContain(`BYDAY=${WD[(new Date().getDay() + 1) % 7]}`)
  expect((await one(page.request, 'E2E Weekly plan'))?.due).toBe(day(1))
})

test('"See all in Tasks" from mail search opens the task search (ACT task.search)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Budget follow-up', due: day(2) })
  await mk(page.request, { summary: 'E2E Unrelated chore', due: day(2) })
  await page.goto('/mail/search?q=budget')
  const glance = page.getByRole('region', { name: 'Events, tasks and notes' })
  await expect(glance.getByText('E2E Budget follow-up')).toBeVisible()
  await expect(glance.getByRole('link', { name: 'See all in Tasks' })).toHaveAttribute('href', '/tasks/search?q=budget')
  await glance.getByRole('link', { name: 'See all in Tasks' }).click()
  await expect(page).toHaveURL(/\/tasks\/all$/)
  await expect(page.getByRole('combobox', { name: 'Search tasks' })).toHaveValue('budget')
  await expect(row(page, 'E2E Budget follow-up')).toBeVisible()
  await expect(row(page, 'E2E Unrelated chore')).toHaveCount(0)
  // The deep link itself, typed or bookmarked.
  await page.goto('/tasks/search?q=chore')
  await expect(page.getByRole('combobox', { name: 'Search tasks' })).toHaveValue('chore')
  await expect(row(page, 'E2E Unrelated chore')).toBeVisible()
  await expect(row(page, 'E2E Budget follow-up')).toHaveCount(0)
})
