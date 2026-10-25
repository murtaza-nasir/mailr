// ACT-SURFACES: web.subscriptions web.cleanup
// Mail → Subscriptions and Mail → Clean up on a phone (Pixel 7; iPhone 14 when WebKit runs), in the
// Classic and Cards themes: rows stack as cards with their numbers labelled, nothing scrolls
// sideways, and the preview dialog fits the screen.
// Covers: subscription.list, subscription.archive_all (web.subscriptions); cleanup.groups, cleanup.trash (web.cleanup).
import type { Page } from '@playwright/test'
import { expect, test } from '../fixtures'

test.use({ subscriptions: true })

async function noSidewaysScroll(page: Page) {
  const over = await page.evaluate(() => {
    const v = document.querySelector('.mg-view') as HTMLElement
    return v.scrollWidth - v.clientWidth
  })
  expect(over).toBeLessThanOrEqual(1)
}

for (const theme of ['classic', 'cards'] as const) {
  test.describe(`${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    })

    test('subscription.list as cards, and subscription.archive_all through its preview', async ({ page }) => {
      await page.goto('/mail/subscriptions')
      const row = page.getByTestId('subscription-row').filter({ hasText: 'E2E Deals' })
      await expect(row).toBeVisible()
      // Each number carries its label (drawn from data-label, as the header row is hidden).
      const label = await row.locator('[data-label="30 days"]').evaluate((el) => getComputedStyle(el, '::before').content)
      expect(label).toContain('30 days')
      await expect(page.locator('.mg-gridhead')).toBeHidden()
      await noSidewaysScroll(page)
      await row.getByRole('button', { name: 'Archive all' }).tap()
      const dialog = page.getByRole('dialog')
      await expect(dialog).toContainText('2 conversations (2 messages) leave the Inbox')
      const box = (await dialog.boundingBox())!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
      await page.getByTestId('mg-confirm').tap()
      await expect(page.locator('.toast').filter({ hasText: 'Archived 2 conversations' })).toBeVisible()
    })

    test('cleanup.groups and cleanup.trash on a phone', async ({ page }) => {
      await page.goto('/mail/cleanup')
      const row = page.getByTestId('cleanup-row').filter({ hasText: 'bulletin@oldnews.e2e.example' })
      await expect(row).toContainText('3 conversations')
      await noSidewaysScroll(page)
      await row.getByRole('button', { name: 'Trash' }).tap()
      await expect(page.getByTestId('mg-preview')).toContainText('Left alone: 1 starred.')
      await page.getByTestId('mg-confirm').tap()
      await expect(page.locator('.toast').filter({ hasText: 'Move to Trash: 3 conversations' })).toBeVisible()
    })
  })
}
