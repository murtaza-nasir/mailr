// The Tasks app in every theme: the left column,
// quick add, a row's checkbox and the detail pane where the theme puts it (Classic and Office: a
// pane on the right; Desk: below a sortable table; Focus: a centred card; Cards: a bottom sheet).
// With TASKS_SHOTS=<dir> each smart list and the pane are also saved as screenshots, light and dark.
// Covers: ACT task.create, ACT task.complete, ACT task.edit (web.pane) in each theme.
import type { APIRequestContext, Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { connectCalendar, conv, expect, test } from './fixtures'
import { THEMES } from './matrix'
import { mkTask } from './helpers'

interface T { uid: string; summary: string; due: string | null; priority?: number; status: string }
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const day = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return iso(d) }
const row = (page: Page, text: string) => page.getByTestId('task-row').filter({ hasText: text })
const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkTask<T>(request, body)
async function all(request: APIRequestContext): Promise<T[]> {
  const j = await (await request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
  return (Array.isArray(j) ? j : j.items) as T[]
}
async function one(request: APIRequestContext, summary: string): Promise<T | undefined> {
  return (await all(request)).find((t) => t.summary === summary)
}

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'
const PLACEMENT: Record<Theme, string> = { classic: 'pane', office: 'pane', focus: 'card', desk: 'below', cards: 'sheet' }
const SHOTS = process.env.TASKS_SHOTS
const LISTS = ['today', 'upcoming', 'scheduled', 'nodate', 'high', 'email', 'waiting', 'all', 'completed']

async function seedTasks(page: Page) {
  const source = await conv(page.request, 'E2E Reply to me')
  const offsite = await mk(page.request, { summary: 'Plan the team offsite', due: day(12), notes: 'Budget **2k**\n- venue\n- food', categories: ['work'] })
  for (const s of ['Pick dates', 'Shortlist venues']) await mk(page.request, { summary: s, parent: offsite.uid })
  await mk(page.request, { summary: 'Send the budget to Priya', due: day(-2), priority: 1, categories: ['finance'] })
  await mk(page.request, { summary: 'Review pull request #482', due: new Date(new Date().setHours(15, 0, 0, 0)).toISOString(), reminders: [15], categories: ['code'] })
  await mk(page.request, { summary: 'Water the plants', due: day(0), recurrence: 'FREQ=DAILY;INTERVAL=3', repeat_from: 'completion' })
  await mk(page.request, { summary: 'Draft the quarterly report', start: day(-1), priority: 5, notes: 'Outline first' })
  await mk(page.request, { summary: 'Call Dana about the venue', due: day(1), categories: ['phone'] })
  await mk(page.request, { summary: 'Reply to the conference committee', due: day(2), conversation_id: source.id })
  await mk(page.request, { summary: 'Renew passport', due: day(5), priority: 9 })
  await mk(page.request, { summary: 'Book flights for December', due: day(45), categories: ['travel'] })
  await mk(page.request, { summary: 'Fix the squeaky door', categories: ['errand'] })
  await mk(page.request, { summary: 'Prepare the slides', start: day(3), due: day(6) })
  const done = await mk(page.request, { summary: 'Pay the electricity bill', due: day(-1) })
  await page.request.patch(`/api/v1/tasks/${encodeURIComponent(done.uid)}`, { data: { completed: true } })
  const c = await conv(page.request, 'E2E Follow me')
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'follow_up', until: new Date(Date.now() + 3 * 3600_000).toISOString() } })
}

for (const theme of THEMES) {
  test(`${theme}: lists, quick add, complete and the pane`, async ({ page, seeded }) => {
    test.setTimeout(SHOTS ? 240_000 : 120_000)
    await connectCalendar(page.request, seeded)
    await seedTasks(page)
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/tasks/today')
    const app = page.getByTestId('tasks-app')
    await expect(app).toBeVisible()
    await expect(app).toHaveClass(new RegExp(`tk-theme-${theme}`))
    await expect(page.getByTestId('tasks-sidebar')).toBeVisible()
    await expect(row(page, 'Send the budget to Priya')).toBeVisible()
    if (theme === 'desk') await expect(page.locator('.tk-table-head')).toContainText('Priority')

    // ACT task.create
    const input = page.getByRole('textbox', { name: 'Add a task' })
    await input.fill(`E2E ${theme} task tom !!!`)
    await input.press('Enter')
    await expect.poll(async () => (await one(page.request, `E2E ${theme} task`))?.priority).toBe(1)

    // ACT task.complete
    await row(page, 'Water the plants').getByRole('checkbox').click()
    // A repeating task advances; a completed history copy stays behind.
    await expect.poll(async () => (await all(page.request)).find((t) => t.summary === 'Water the plants' && t.status !== 'completed')?.due).not.toBe(day(0))

    // ACT task.edit in the theme's pane.
    await row(page, 'Send the budget to Priya').locator('.tk-title').click()
    const pane = page.getByTestId('task-pane')
    await expect(pane).toBeVisible()
    await expect(pane).toHaveClass(new RegExp(`tk-pane-${PLACEMENT[theme]}`))
    await pane.getByRole('radio', { name: 'Medium' }).click()
    await expect.poll(async () => (await one(page.request, 'Send the budget to Priya'))?.priority).toBe(5)
    await page.keyboard.press('Escape')
    await expect(pane).toHaveCount(0)

    if (!SHOTS) return
    mkdirSync(SHOTS, { recursive: true })
    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme })
      for (const id of LISTS) {
        await page.goto(`/tasks/${id}`)
        await expect(page.getByTestId('tasks-header')).toBeVisible()
        await page.waitForTimeout(700)
        await page.screenshot({ path: `${SHOTS}/${theme}-${scheme}-${id}.png` })
      }
      await page.goto('/tasks/all')
      await row(page, 'Plan the team offsite').locator('.tk-title').click()
      await expect(page.getByTestId('task-pane')).toBeVisible()
      await page.waitForTimeout(500)
      await page.screenshot({ path: `${SHOTS}/${theme}-${scheme}-pane.png` })
      await page.keyboard.press('Escape')
      await page.setViewportSize({ width: 390, height: 844 })
      await page.goto('/tasks/today')
      await expect(row(page, 'Review pull request')).toBeVisible()
      await page.screenshot({ path: `${SHOTS}/${theme}-${scheme}-phone-today.png` })
      await row(page, 'Review pull request').locator('.tk-title').click()
      await page.waitForTimeout(500)
      await page.screenshot({ path: `${SHOTS}/${theme}-${scheme}-phone-pane.png` })
      await page.setViewportSize({ width: 1440, height: 900 })
    }
  })
}
