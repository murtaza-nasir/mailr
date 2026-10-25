// ACT-SURFACES: web.tasks
// Tasks on the phone layout: quick add sits at the bottom, above the tab
// bar, as Ambrose's message box, with its chips (the view's "adds to" hint, parsed tokens) above the line.
// It never covers the last rows: scrolled to the end, the last task shows above it. The task it adds is
// checked through the API.
import type { APIRequestContext } from '@playwright/test'
import { connectCalendar, expect, test } from '../fixtures'
import { mkTask } from '../helpers'

const SHOTS = process.env.AMBROSE_SHOTS

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkTask(request, body)

for (const theme of ['classic', 'cards'] as const) {
  test(`${theme}: quick add at the bottom, above the tab bar, never over the last task (ACT task.create)`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    const today = iso(new Date())
    for (let i = 1; i <= 14; i++) await mk(page.request, { summary: `E2E Phone task ${String(i).padStart(2, '0')}`, due: today })
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/tasks/today')
    const list = page.getByTestId('tasks-list')
    await expect(list.getByText('E2E Phone task 01')).toBeVisible()
    const bar = page.getByTestId('tasks-quick-add')
    const tabs = page.getByTestId('phone-tabs')
    const barBox = (await bar.boundingBox())!
    const tabsBox = (await tabs.boundingBox())!
    // Right above the tab bar, below the list.
    expect(Math.abs(barBox.y + barBox.height - tabsBox.y)).toBeLessThanOrEqual(2)
    expect((await list.boundingBox())!.y).toBeLessThan(barBox.y)
    // The chips (here the view's hint) above the line.
    await page.getByLabel('Add a task').fill('E2E Bottom task fri !!')
    const chips = bar.locator('.tk-quick-chips')
    await expect(chips).toBeVisible()
    expect((await chips.boundingBox())!.y + (await chips.boundingBox())!.height).toBeLessThanOrEqual((await bar.locator('.tk-quick-line').boundingBox())!.y + 1)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/tasks-${theme}.png` })
    await page.getByLabel('Add a task').press('Enter')
    await expect.poll(async () => {
      const j = await (await page.request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
      return ((Array.isArray(j) ? j : j.items) as { summary: string }[]).some((t) => t.summary === 'E2E Bottom task')
    }).toBe(true)
    // Scrolled to the end, the last task is above the bar, not under it.
    const last = list.getByText('E2E Phone task 14')
    await last.scrollIntoViewIfNeeded()
    const lastBox = (await last.boundingBox())!
    const barNow = (await bar.boundingBox())!
    expect(lastBox.y + lastBox.height).toBeLessThanOrEqual(barNow.y + 1)
  })
}
