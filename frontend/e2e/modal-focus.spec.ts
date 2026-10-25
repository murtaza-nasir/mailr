// Focus in modal drawers and menus.
// - the narrow-screen drawers (Tasks, Calendar, Recordings, Cards' mailbox drawer) are modal
//     dialogs: the focus moves in, Tab stays inside, Escape closes, and the focus returns to the
//     button that opened them. Global shortcuts stand down while one is open.
// - closing a top-level menu puts the focus back on the button that opened it.
// Covers: web.focus.
import type { Locator, Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'

/** Tab many times: the focus never leaves the drawer. */
async function tabStaysIn(page: Page, drawer: Locator) {
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press(i % 7 === 6 ? 'Shift+Tab' : 'Tab')
    expect(await drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true)
  }
}

test('Cards: the mailbox drawer is a modal dialog', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'cards'))
  await page.goto('/mail/inbox')
  const more = page.locator('.c-dest:visible').filter({ hasText: 'More' }).first()
  await more.click()
  const drawer = page.getByRole('dialog', { name: 'All mailboxes' })
  await expect(drawer).toBeVisible()
  await expect.poll(() => drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true)
  await tabStaysIn(page, drawer)
  // A mail key does nothing behind the drawer.
  await page.keyboard.press('c')
  await expect(page.getByRole('dialog', { name: /New message/i })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(drawer).toHaveCount(0)
  await expect(more).toBeFocused()
})

test('Tasks and Calendar at 600 px: the drawers are modal dialogs', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.setViewportSize({ width: 600, height: 800 })
  await page.goto('/tasks')
  const lists = page.getByRole('button', { name: 'Lists' })
  await lists.click()
  const tasksDrawer = page.getByRole('dialog', { name: 'Task lists' })
  await expect(tasksDrawer).toBeVisible()
  await tabStaysIn(page, tasksDrawer)
  await page.keyboard.press('Escape')
  await expect(tasksDrawer).toHaveCount(0)
  await expect(lists).toBeFocused()

  await page.goto('/calendar')
  const toggle = page.getByRole('button', { name: 'Calendars and mini-month' })
  await toggle.click()
  const calDrawer = page.getByRole('dialog', { name: 'Calendar navigation' })
  await expect(calDrawer).toBeVisible()
  await tabStaysIn(page, calDrawer)
  await page.keyboard.press('Escape')
  await expect(calDrawer).toHaveCount(0)
  await expect(toggle).toBeFocused()
})

test('F7: Escape on a menu returns the focus to its button', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"][data-id]').first()).toBeVisible()
  const more = page.locator('[data-mail-list]').getByRole('button', { name: 'More', exact: true }).first()
  await more.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menu')).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest('[role="menu"]'))).toBe(true)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(more).toBeFocused()
})
