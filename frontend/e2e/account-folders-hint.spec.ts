// ACT-SURFACES: web.nav web.settings
// The first-time hint for several mail accounts (features/layout/accounts.tsx, AccountFoldersHint):
// with two or more accounts and "Show each account separately" off, one line in the navigation
// offers the setting (ACT mail.account_folders) with a link to its row in Settings → Mail → Inbox.
// The ✕ dismisses it for good (settings.account_folders_hint_dismissed); changing the setting does
// too. One account: no hint. Classic, Office, Desk and Cards show it where the account groups go.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const hint = (page: Page) => page.getByTestId('acct-folders-hint')

async function settings(page: Page): Promise<Record<string, unknown>> {
  return ((await (await page.request.get('/api/v1/me')).json()) as { settings: Record<string, unknown> }).settings
}

test.describe('two accounts', () => {
  test.use({ accounts: true })

  test('Classic: the hint links to the setting; turning it on shows the groups and the hint goes for good', async ({ page, seeded }) => {
    const a = seeded.accounts!
    await page.goto('/mail/inbox')
    await expect(hint(page)).toBeVisible()
    // One line: no taller than a navigation row.
    expect((await hint(page).boundingBox())!.height).toBeLessThan(40)
    expect(await hint(page).getByRole('link').evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true)
    await expect(page.getByTestId('nav-accounts')).toHaveCount(0)

    await hint(page).getByRole('link', { name: 'Show each account separately' }).click()
    await expect(page).toHaveURL(/\/settings\/mail\/inbox#account-folders$/)
    const box = page.locator('#set-account-folders')
    await expect(box).toBeInViewport()
    await box.getByRole('checkbox', { name: /Show each account separately/ }).check()
    await expect.poll(async () => (await settings(page)).account_folders).toBe(true)
    await expect.poll(async () => (await settings(page)).account_folders_hint_dismissed).toBe(true)

    await page.goto('/mail/inbox')
    await expect(page.getByTestId('nav-accounts').getByRole('group', { name: a.work_address })).toBeVisible()
    await expect(hint(page)).toHaveCount(0)
    // Off again: the hint does not come back.
    await page.request.patch('/api/v1/me/settings', { data: { account_folders: false } })
    await page.reload()
    await expect(page.getByTestId('nav-accounts')).toHaveCount(0)
    await expect(page.locator('[data-mail-list]')).toBeVisible()
    await expect(hint(page)).toHaveCount(0)
  })

  test('the ✕ dismisses it on every device; Office, Desk and Cards show it too', async ({ page }) => {
    for (const [t, nav] of [['office', '.o-folders'], ['desk', '.d-folders']] as const) {
      await page.addInitScript((x) => localStorage.setItem('mailr.uiTheme', x), t)
      await page.goto('/mail/inbox')
      await expect(page.locator(nav).getByTestId('acct-folders-hint')).toBeVisible()
    }
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'cards'))
    await page.goto('/mail/inbox')
    await page.getByRole('button', { name: 'All mailboxes and labels' }).first().click()
    const drawer = page.locator('.c-drawer.open')
    await expect(drawer.getByTestId('acct-folders-hint')).toBeVisible()

    await drawer.getByRole('button', { name: 'Dismiss this hint' }).click()
    await expect(drawer.getByTestId('acct-folders-hint')).toHaveCount(0)
    await expect.poll(async () => (await settings(page)).account_folders_hint_dismissed).toBe(true)
    expect((await settings(page)).account_folders).toBe(false)

    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
    await page.goto('/mail/inbox')
    await expect(page.locator('[data-mail-list]')).toBeVisible()
    await expect(hint(page)).toHaveCount(0)
  })
})

test('one account: no hint', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list]')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Inbox' }).first()).toBeVisible()
  await expect(hint(page)).toHaveCount(0)
})
