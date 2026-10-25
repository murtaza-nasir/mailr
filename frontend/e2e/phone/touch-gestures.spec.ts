// Touch gestures on the mail list (features/pwa/useTouchList.ts) on a Pixel 7 and an iPhone 14,
// Classic and Cards: swipe right archives and swipe left deletes by default (Settings → General
// → Swipe actions changes them, per device), press and hold opens the row's menu as a bottom
// sheet, and pulling the list down refreshes it.
// ACT mail.archive, mail.delete (web.list swipes)
import type { Page } from '@playwright/test'
import { expect, test } from '../fixtures'
import { drag, longPress, row, rows, settled } from './phone'

async function serverHas(page: Page, view: string, subject: string): Promise<boolean> {
  const r = await page.request.get('/api/v1/conversations', { params: { view, limit: '100' } })
  return ((await r.json()).items as { subject: string }[]).some((c) => c.subject === subject)
}

for (const theme of ['classic', 'cards'] as const) {
  test.describe(`${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    })

    test('swipe right archives, swipe left deletes, a short swipe does nothing', async ({ page }) => {
      await page.goto('/mail/inbox')
      await expect(row(page, 'E2E Move me')).toBeVisible()
      const width = page.viewportSize()!.width

      // Not far enough: the row slides back and stays.
      await drag(row(page, 'E2E Move me'), width * 0.15, 0)
      await page.waitForTimeout(400)
      await expect(row(page, 'E2E Move me')).toBeVisible()
      expect(await row(page, 'E2E Move me').evaluate((el) => getComputedStyle(el).transform)).toMatch(/^(none|matrix\(1, 0, 0, 1, 0, 0\))$/)
      expect(await serverHas(page, 'inbox', 'E2E Move me')).toBe(true)

      await drag(row(page, 'E2E Move me'), width * 0.6, 0)
      await expect(page.getByText('Conversation archived.')).toBeVisible()
      await expect(rows(page).filter({ hasText: 'E2E Move me' })).toHaveCount(0)
      await expect.poll(() => serverHas(page, 'inbox', 'E2E Move me')).toBe(false)

      await drag(row(page, 'E2E Mute me'), -width * 0.6, 0)
      await expect(page.getByText('Conversation moved to Trash.')).toBeVisible()
      await expect(rows(page).filter({ hasText: 'E2E Mute me' })).toHaveCount(0)
      await expect.poll(() => serverHas(page, 'trash', 'E2E Mute me')).toBe(true)
    })

    test('the swipe actions follow Settings → General → Swipe actions', async ({ page }) => {
      await page.goto('/settings/general')
      await page.getByLabel('Swipe right').selectOption('star')
      await page.getByLabel('Swipe left').selectOption('none')
      await page.goto('/mail/inbox')
      const width = page.viewportSize()!.width
      await drag(row(page, 'E2E Label me'), width * 0.6, 0)
      await expect.poll(() => serverHas(page, 'starred', 'E2E Label me')).toBe(true)
      await expect(row(page, 'E2E Label me')).toBeVisible()
      // None: nothing happens, nothing is drawn behind the row.
      await drag(row(page, 'E2E Label me'), -width * 0.6, 0)
      await page.waitForTimeout(400)
      await expect(row(page, 'E2E Label me')).toBeVisible()
      await expect(page.locator('.swipe-under')).toHaveCount(0)
    })

    test('press and hold opens the row menu as a bottom sheet', async ({ page }) => {
      await page.goto('/mail/inbox')
      await expect(row(page, 'E2E Read me')).toBeVisible()
      await longPress(row(page, 'E2E Read me'))
      const sheet = page.locator('.popover.as-sheet')
      await expect(sheet).toBeVisible()
      await settled(sheet)
      await expect(sheet.getByRole('menuitem').first()).toBeVisible()
      // The hold did not open the conversation.
      await expect(page).toHaveURL(/\/mail\/inbox$/)
      const b = (await sheet.boundingBox())!
      expect(Math.round(b.y + b.height)).toBe(page.viewportSize()!.height)
    })

    test('pull down to refresh shows what changed elsewhere', async ({ page, seeded }) => {
      await page.goto('/mail/inbox')
      await expect(row(page, 'E2E Snooze me')).toBeVisible()
      // Archived on another device.
      const r = await page.request.post('/api/v1/conversations/actions', { data: { ids: [seeded.conversations['E2E Snooze me']], action: 'archive' } })
      expect(r.ok()).toBeTruthy()
      await drag(page.locator('.list-scroll'), 0, 260, { steps: 16 })
      await expect(page.locator('.ptr')).toHaveCount(1)
      await expect(rows(page).filter({ hasText: 'E2E Snooze me' })).toHaveCount(0)
    })
  })
}
