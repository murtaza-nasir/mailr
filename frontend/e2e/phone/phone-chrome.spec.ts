// The phone frame (features/pwa/PhoneChrome.tsx, styles/phone.css) on a Pixel 7 and an iPhone 14,
// in the Classic and Cards themes: the bottom tab bar (Mail, Calendar, Tasks, Notes, Ambrose; no More),
// 44 px targets, the safe areas of a notched iPhone in the installed app, menus as bottom sheets, and
// app switches without a reload. The same bar in every theme: nav-switcher-phone.spec.ts.
import { expect, test } from '../fixtures'
import { emulateStandalone, notch, row, rows, settled } from './phone'

for (const theme of ['classic', 'cards'] as const) {
  test.describe(`${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    })

    test('the tab bar: Mail, Calendar, Tasks, Notes and Ambrose, without reloading the page', async ({ page }) => {
      await page.goto('/mail/inbox')
      const tabs = page.getByTestId('phone-tabs')
      await expect(tabs).toBeVisible()
      // Five is the ceiling, Ambrose far right; More is gone.
      await expect(tabs.locator('.tab')).toHaveCount(5)
      expect(await tabs.locator('.tab').evaluateAll((els) => els.map((e) => e.getAttribute('data-tab')))).toEqual(['mail', 'calendar', 'tasks', 'notes', 'assistant'])
      await expect(tabs.locator('[data-tab="assistant"]')).toHaveText('Ambrose')
      // One bar only (Cards has no bottom bar of its own any more).
      await expect(page.locator('.c-bottom')).toHaveCount(0)
      // At the bottom edge, every tab at least 44 × 44.
      const vh = page.viewportSize()!.height
      const box = (await tabs.boundingBox())!
      expect(Math.round(box.y + box.height)).toBe(vh)
      for (const t of await tabs.locator('.tab').all()) {
        const b = (await t.boundingBox())!
        expect(b.width).toBeGreaterThanOrEqual(44)
        expect(b.height).toBeGreaterThanOrEqual(44)
      }
      await expect(tabs.locator('[data-tab="mail"]')).toHaveAttribute('aria-current', 'page')
      // The unread count of the Inbox on Mail.
      await expect(tabs.locator('[data-tab="mail"] .tab-badge')).toHaveText(/\d+/)

      // Switching apps keeps the page (a marker on window survives).
      await page.evaluate(() => ((window as unknown as { __kept: number }).__kept = 42))
      await tabs.locator('[data-tab="calendar"]').click()
      await expect(page).toHaveURL(/\/calendar/)
      await tabs.locator('[data-tab="tasks"]').click()
      await expect(page).toHaveURL(/\/tasks/)
      await tabs.locator('[data-tab="notes"]').click()
      await expect(page).toHaveURL(/\/notes/)
      await tabs.locator('[data-tab="mail"]').click()
      await expect(page).toHaveURL(/\/mail\/inbox/)
      expect(await page.evaluate(() => (window as unknown as { __kept?: number }).__kept)).toBe(42)

      // Ambrose opens as a panel above the tab bar.
      await tabs.locator('[data-tab="assistant"]').click()
      const panel = page.locator('.side-panel')
      await expect(panel).toBeVisible()
      expect((await panel.boundingBox())!.y + (await panel.boundingBox())!.height).toBeLessThanOrEqual(box.y + 1)
      await tabs.locator('[data-tab="assistant"]').click()
      await expect(panel).toHaveCount(0)

      // The mail folders are in the mail drawer (the top bar's menu button).
      await page.getByRole('button', { name: theme === 'cards' ? 'All mailboxes and labels' : 'Main menu' }).first().click()
      const drawer = page.locator(theme === 'cards' ? '.c-drawer.open' : '.sidebar.mobile-open')
      await expect(drawer).toBeVisible()
      await drawer.getByRole('link', { name: 'Starred' }).first().click()
      await expect(page).toHaveURL(/\/mail\/starred/)
      // Settings is in the account menu.
      await page.getByRole('button', { name: /^Account:/ }).click()
      await page.getByRole('menuitem', { name: 'Settings' }).click()
      await expect(page).toHaveURL(/\/settings(\/you)?$/)
      expect(await page.evaluate(() => (window as unknown as { __kept?: number }).__kept)).toBe(42)
    })

    test('an open conversation has the screen; the tab bar comes back on the list', async ({ page }) => {
      await page.goto('/mail/inbox')
      await row(page, 'E2E Read me').click()
      await expect(page.locator('.conv-view').getByText('E2E Read me').first()).toBeVisible()
      await expect(page.getByTestId('phone-tabs')).toHaveCount(0)
      await page.goBack()
      await expect(page.getByTestId('phone-tabs')).toBeVisible()
    })

    test('safe areas: in the installed app nothing sits under the status bar or the home indicator', async ({ page }) => {
      await emulateStandalone(page)
      await notch(page, 47, 34)
      await page.goto('/mail/inbox')
      await expect(rows(page).first()).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.dataset.standalone)).toBe('true')
      const vh = page.viewportSize()!.height
      // The top bar starts below the status bar.
      const top = page.locator(theme === 'cards' ? '.c-top' : '.topbar')
      expect((await top.boundingBox())!.y).toBeGreaterThanOrEqual(47)
      // The tab bar's buttons end above the home indicator; its background reaches the edge.
      const tabs = page.getByTestId('phone-tabs')
      const bar = (await tabs.boundingBox())!
      expect(Math.round(bar.y + bar.height)).toBe(vh)
      const tab = (await tabs.locator('.tab').first().boundingBox())!
      expect(tab.y + tab.height).toBeLessThanOrEqual(vh - 34 + 1)
      // The app's own content ends where the tab bar starts.
      const app = (await page.locator('.app').boundingBox())!
      expect(app.y + app.height).toBeLessThanOrEqual(bar.y + 1)
      // Compose sits above the tab bar.
      const fab = page.locator('.compose-fab')
      if (await fab.isVisible()) expect((await fab.boundingBox())!.y + (await fab.boundingBox())!.height).toBeLessThanOrEqual(bar.y)
      // No install banner in the installed app.
      await expect(page.getByTestId('install-banner')).toHaveCount(0)
    })

    test('menus open as bottom sheets on a phone', async ({ page }) => {
      await page.goto('/mail/inbox')
      await expect(rows(page).first()).toBeVisible()
      await page.getByRole('button', { name: /^Account:/ }).click()
      const sheet = page.locator('.popover.as-sheet')
      await expect(sheet).toBeVisible()
      await settled(sheet)
      const vh = page.viewportSize()!.height
      const b = (await sheet.boundingBox())!
      expect(Math.round(b.y + b.height)).toBe(vh)
      expect(Math.round(b.width)).toBe(page.viewportSize()!.width)
      // The scrim closes it.
      await page.mouse.click(10, 10)
      await expect(sheet).toHaveCount(0)
    })
  })
}

test('reduced motion: sheets appear without sliding', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/mail/inbox')
  await page.getByRole('button', { name: /^Account:/ }).click()
  const anim = await page.locator('.popover.as-sheet').evaluate((el) => getComputedStyle(el).animationName)
  expect(anim).toBe('none')
})
