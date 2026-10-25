// ACT-SURFACES: web.nav
// The phone drawer with folders inside each account (features/layout/accounts.tsx):
// with "Show each account separately" on, each account's group in the drawer lists its folders below
// Trash, and the group's ⋮ makes a new one (ACT mail.account_folder_create, ACT mail.account_label_view).
// Screenshots of the Classic drawer, light and dark.
import os from 'node:os'
import path from 'node:path'
import { expect, test } from '../fixtures'
import { rows } from './phone'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-account-folders-shots')

test.use({ accounts: true })

test('the drawer lists an account\'s folders, and its ⋮ makes one there', async ({ page, seeded }) => {
  const a = seeded.accounts!
  expect((await page.request.patch('/api/v1/me/settings', { data: { account_folders: true } })).ok()).toBeTruthy()
  const r = await page.request.post('/api/v1/labels', { data: { name: 'Invoices', home_account_id: a.work } })
  const id = (await r.json()).id as number
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  await page.getByRole('button', { name: 'Main menu' }).first().click()
  const drawer = page.locator('.sidebar.mobile-open')
  const work = drawer.getByRole('group', { name: a.work_address })
  const invoices = work.locator(`a.nav-item[data-drop-view="acct:${a.work}:label:${id}"]`)
  await invoices.scrollIntoViewIfNeeded()
  await expect(invoices).toBeVisible()
  // On a touch screen the ⋮ shows without hovering.
  const more = page.getByTestId(`acct-menu-${a.work}`)
  await expect(more).toBeVisible()
  expect(Number(await more.evaluate((el) => getComputedStyle(el).opacity))).toBe(1)
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(SHOTS, `phone-drawer-${scheme}-${test.info().project.name}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
  await more.click()
  await page.getByRole('menuitem', { name: 'New folder' }).click()
  const dialog = page.getByRole('dialog', { name: `New folder in ${a.work_address}` })
  await dialog.getByRole('textbox').first().fill('Quotes')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Alabel%3A\\d+$`))
  await expect(page.getByText('No conversations with this label.')).toBeVisible()
})
