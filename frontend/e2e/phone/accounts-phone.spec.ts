// ACT-SURFACES: web.nav
// The phone drawer with several mail accounts (features/layout/accounts.tsx):
// "Show each account separately" puts each account's own folders in the theme's mail drawer
// (ACT mail.account_view). Screenshots of the Classic drawer, light and dark.
import os from 'node:os'
import path from 'node:path'
import { expect, test } from '../fixtures'
import { rows } from './phone'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-accounts-shots')

test.use({ accounts: true })

test('the drawer lists each account with its folders, and one opens its Inbox', async ({ page, seeded }) => {
  const a = seeded.accounts!
  const r = await page.request.patch('/api/v1/me/settings', { data: { account_folders: true } })
  expect(r.ok()).toBeTruthy()
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  await page.getByRole('button', { name: 'Main menu' }).first().click()
  const drawer = page.locator('.sidebar.mobile-open')
  await expect(drawer).toBeVisible()
  const work = drawer.getByRole('group', { name: a.work_address })
  await work.scrollIntoViewIfNeeded()
  await expect(work.getByRole('link', { name: 'Inbox' })).toContainText('1')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(SHOTS, `phone-drawer-${scheme}-${test.info().project.name}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
  await work.getByRole('link', { name: 'Inbox' }).click()
  await expect(drawer).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Ainbox$`))
  await expect(rows(page).filter({ hasText: 'E2E Work report' })).toBeVisible()
  await expect(rows(page).filter({ hasText: 'E2E Home letter' })).toHaveCount(0)
})
