// ACT-SURFACES: web.subscriptions web.cleanup web.nav
// Mail → Subscriptions and Mail → Clean up (features/mail/SubscriptionsView.tsx, CleanupView.tsx;
// backend core/mailgroups.py), in the Classic and Cards themes on a desktop. The seed's list and
// bulk mail: "E2E Digest" (a list with one-click unsubscribe), "E2E Deals" (an unsubscribe address)
// and old archived "E2E Old News" (three plain conversations and a starred one).
// Covers: subscription.list, subscription.unsubscribe, subscription.archive_all, subscription.auto_archive,
// subscription.label, subscription.keep (web.subscriptions); cleanup.groups, cleanup.trash, cleanup.read,
// cleanup.label, cleanup.archive, cleanup.category, cleanup.rule, cleanup.keep, cleanup.suggest (web.cleanup);
// subscription.list and cleanup.groups from the navigation (web.nav).
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

test.use({ subscriptions: true })

const subRow = (page: Page, name: string) => page.getByTestId('subscription-row').filter({ hasText: name })
const groupRow = (page: Page, name: string) => page.getByTestId('cleanup-row').filter({ hasText: name })
const toast = (page: Page, text: string | RegExp) => page.locator('.toast').filter({ hasText: text })

async function openFromNav(page: Page, title: 'Subscriptions' | 'Clean up') {
  await page.goto('/mail/inbox')
  const link = page.getByRole('link', { name: title, exact: true })
  if (!(await link.first().isVisible())) {
    // Classic keeps the secondary views under More.
    await page.getByRole('button', { name: 'More', exact: true }).first().click()
  }
  await link.first().click()
}

for (const theme of ['classic', 'cards'] as const) {
  test.describe(`${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    })

    test('subscription.list from the navigation, with volume, reading and the unsubscribe method', async ({ page }) => {
      await openFromNav(page, 'Subscriptions')
      await expect(page).toHaveURL(/\/mail\/subscriptions$/)
      await expect(page.getByTestId('subscriptions-view')).toBeVisible()
      const digest = subRow(page, 'E2E Digest')
      await expect(digest).toContainText('One-click')
      await expect(digest).toContainText('0%')
      await expect(subRow(page, 'E2E Deals')).toContainText('By email')
      // The grid: one tab stop, arrow keys move between rows.
      await digest.focus()
      await page.keyboard.press('ArrowDown')
      await expect(page.locator('[data-group-row]:focus')).toHaveCount(1)
    })

    test('subscription.unsubscribe says what it sends first', async ({ page }) => {
      await page.goto('/mail/subscriptions')
      let posted = false
      await page.route('**/api/v1/subscriptions/unsubscribe', async (route) => {
        if (route.request().method() !== 'POST') return route.continue()
        posted = true
        await route.fulfill({ json: { ok: true, method: 'one_click', status: 200 } })
      })
      await subRow(page, 'E2E Digest').getByRole('button', { name: 'Unsubscribe' }).click()
      const dialog = page.getByRole('dialog')
      await expect(dialog).toContainText('digest.e2e.example')
      expect(posted).toBe(false)
      await dialog.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Unsubscribed from E2E Digest')).toBeVisible()
      expect(posted).toBe(true)
    })

    test('subscription.archive_all with a preview and Undo; subscription.auto_archive, subscription.label, subscription.keep', async ({ page }) => {
      await page.goto('/mail/subscriptions')
      await subRow(page, 'E2E Deals').getByRole('button', { name: 'Archive all' }).click()
      const preview = page.getByTestId('mg-preview')
      await expect(preview).toContainText('2 conversations (2 messages) leave the Inbox')
      await expect(preview).toContainText('E2E Deals 1')
      await page.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Archived 2 conversations from E2E Deals')).toBeVisible()
      await toast(page, 'Archived 2').getByRole('button', { name: 'Undo' }).click()
      await expect(toast(page, 'Action undone')).toBeVisible()

      await subRow(page, 'E2E Deals').getByRole('button', { name: 'Auto-archive' }).click()
      await expect(toast(page, 'will skip the Inbox')).toBeVisible()
      await expect(subRow(page, 'E2E Deals')).toContainText('Auto-archived')

      await subRow(page, 'E2E Digest').getByRole('button', { name: 'Label' }).click()
      await page.getByRole('dialog').getByLabel('Label').selectOption({ label: 'E2E Label' })
      await expect(page.getByTestId('mg-preview')).toContainText('get the label “E2E Label”')
      await page.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Labelled 3 conversations from E2E Digest')).toBeVisible()

      await subRow(page, 'E2E Digest').getByRole('button', { name: 'Keep' }).click()
      await expect(subRow(page, 'E2E Digest')).toContainText('Kept')
    })

    test('cleanup.groups from the navigation; cleanup.trash leaves the starred conversation alone, with Undo', async ({ page }) => {
      await openFromNav(page, 'Clean up')
      await expect(page).toHaveURL(/\/mail\/cleanup$/)
      const old = groupRow(page, 'bulletin@oldnews.e2e.example')
      await expect(old).toContainText('3 conversations')
      await expect(old).toContainText('1 left alone')
      await old.getByRole('button', { name: 'Trash' }).click()
      const preview = page.getByTestId('mg-preview')
      await expect(preview).toContainText('3 conversations (3 messages) move to Trash')
      await expect(preview).toContainText('Left alone: 1 starred.')
      await expect(preview.locator('.mg-sample')).toHaveCount(3)
      // Including the starred one changes the count before anything runs.
      await preview.getByLabel('Starred').check()
      await expect(page.getByTestId('mg-confirm')).toHaveText('Move to Trash 4')
      await preview.getByLabel('Starred').uncheck()
      await expect(page.getByTestId('mg-confirm')).toHaveText('Move to Trash 3')
      await page.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Move to Trash: 3 conversations')).toBeVisible()
      await toast(page, 'Move to Trash: 3').getByRole('button', { name: 'Undo' }).click()
      await expect(toast(page, 'Action undone')).toBeVisible()
      await expect(groupRow(page, 'bulletin@oldnews.e2e.example')).toContainText('3 conversations')
    })

    test('cleanup.read, cleanup.label, cleanup.category, cleanup.archive, cleanup.rule, cleanup.keep and cleanup.suggest', async ({ page }) => {
      await page.goto('/mail/cleanup')
      const old = groupRow(page, 'bulletin@oldnews.e2e.example')
      await old.getByRole('button', { name: 'Mark read' }).click()
      await page.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Mark read: 3 conversations')).toBeVisible()

      await old.getByRole('button', { name: 'Label' }).click()
      await page.getByRole('dialog').getByLabel('Label').selectOption({ label: 'E2E Other' })
      await page.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Label “E2E Other”: 3 conversations')).toBeVisible()

      await old.getByRole('button', { name: 'Category' }).click()
      await page.getByRole('dialog').getByLabel('Category').selectOption('updates')
      await expect(page.getByTestId('mg-preview')).toContainText('move to Updates')
      await page.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Move to Updates: 3 conversations')).toBeVisible()

      // The Inbox scope: archive the digest's issues.
      await page.getByLabel('Mail scope').selectOption('inbox')
      const digest = groupRow(page, 'news@digest.e2e.example')
      await digest.getByRole('button', { name: 'Archive' }).click()
      await page.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Archive: 3 conversations')).toBeVisible()

      await page.getByLabel('Mail scope').selectOption('all')
      await groupRow(page, 'offers@deals.e2e.example').getByRole('button', { name: 'Filter' }).click()
      const dialog = page.getByRole('dialog')
      await expect(dialog).toContainText('from:offers@deals.e2e.example')
      await dialog.getByTestId('mg-confirm').click()
      await expect(toast(page, 'Filter created')).toBeVisible()

      await groupRow(page, 'offers@deals.e2e.example').getByRole('button', { name: 'Keep' }).click()
      await expect(groupRow(page, 'offers@deals.e2e.example')).toContainText('Kept')

      await page.getByRole('button', { name: 'Suggest categories' }).click()
      await expect(page.getByRole('dialog')).toContainText('Nothing changes until you apply a suggestion')
      await page.getByRole('dialog').getByRole('button', { name: 'Tonight' }).click()
      // Either it is queued for tonight, or the account has no sorting model.
      await expect(page.locator('.toast').filter({ hasText: /Suggestions start at|Couldn't ask for suggestions/ })).toBeVisible()
    })
  })
}
