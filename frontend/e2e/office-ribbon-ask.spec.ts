// ACT-SURFACES: web.reader web.calendar web.pane
// The Office ribbon's Ask button (ACT agent.ask_about): on the mail Home tab and on the Calendar
// and Tasks tabs, it opens the ask capture about what is open, as Shift+A does: the reading
// pane's conversation, an event's detail, the task pane. With nothing open, no item is attached.
// Screenshots go to E2E_SHOTS (default: <tmp>/mailr-ribbon-ask-shots).
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { conv, expect, test } from './fixtures'
import { dayOf, seedWeek } from './calendar-data'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-ribbon-ask-shots')
const capture = (page: Page) => page.getByTestId('ask-capture')
const ask = (page: Page) => page.locator('.ribbon').getByTestId('ribbon-ask')

async function office(page: Page, dark = false) {
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'office'))
  await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light', reducedMotion: 'reduce' })
}

for (const dark of [false, true]) {
  test(`agent.ask_about: the mail ribbon's Ask, about the open conversation (${dark ? 'dark' : 'light'})`, async ({ page }) => {
    await office(page, dark)
    const c = await conv(page.request, 'E2E Unread me')
    await page.goto(`/mail/inbox/${c.id}`)
    await expect(page.getByRole('heading', { name: 'E2E Unread me' }).first()).toBeVisible()
    await expect(ask(page)).toHaveAttribute('data-tip', 'Ask Ambrose (Shift+A)')
    await expect(ask(page)).toContainText('Ask')
    await ask(page).click()
    await expect(capture(page).getByTestId('ask-chip')).toContainText('E2E Unread me')
    await expect(capture(page).getByRole('textbox', { name: 'Your words for Ambrose' })).toBeFocused()
    await page.waitForTimeout(250)
    await page.screenshot({ path: `${SHOTS}/office-ribbon-ask-${dark ? 'dark' : 'light'}.png` })
    await page.keyboard.press('Escape')
    await expect(capture(page)).toHaveCount(0)
  })
}

test('agent.ask_about: with nothing open, the ribbon\'s Ask attaches no item', async ({ page }) => {
  await office(page)
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  await ask(page).click()
  await expect(capture(page)).toBeVisible()
  await expect(capture(page).getByTestId('ask-chip')).toHaveCount(0)
})

test('agent.ask_about: the Calendar and Tasks ribbons ask about the open event and task', async ({ page, seeded }) => {
  await seedWeek(page.request, seeded)
  const task = (await (await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Ribbon task', due: dayOf(0) } })).json()) as { uid: string }
  await office(page)
  await page.goto(`/calendar/week/${dayOf(3)}`)
  await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).first().click()
  await expect(page.getByTestId('cal-detail')).toBeVisible()
  await ask(page).click()
  await expect(capture(page).getByTestId('ask-chip')).toContainText('Budget review')
  await page.keyboard.press('Escape')

  await page.goto(`/tasks/all/task/${encodeURIComponent(task.uid)}`)
  // The pane shows while the task loads; the task is what Ask attaches.
  await expect(page.getByTestId('task-pane').getByRole('textbox', { name: 'Task title' })).toHaveValue('E2E Ribbon task')
  await ask(page).click()
  await expect(capture(page).getByTestId('ask-chip')).toContainText('E2E Ribbon task')
})
