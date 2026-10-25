// ACT-SURFACES: web.nav
// The phone drawer keeps each account's group short (features/layout/folderPins.tsx):
// pinned folders, then "More folders" with the unread mail of the folders inside it. A tap opens it
// (ACT mail.account_folders_more), and a long press on a folder opens its menu, which pins it
// (ACT mail.account_folder_pin). Screenshots of the Classic drawer, light and dark.
import os from 'node:os'
import path from 'node:path'
import { expect, test } from '../fixtures'
import { rows } from './phone'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-account-folder-pins-shots')

test.use({ accounts: true })

test('the drawer shows pinned folders and a closed More folders with its unread sum; a long press pins a folder', async ({ page, seeded }) => {
  const a = seeded.accounts!
  expect((await page.request.patch('/api/v1/me/settings', { data: { account_folders: true } })).ok()).toBeTruthy()
  const ids: number[] = []
  for (const name of ['Alpha', 'Beta']) {
    const r = await page.request.post('/api/v1/labels', { data: { name } })
    ids.push((await r.json()).id)
  }
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [a.conversations['E2E Work report']], action: 'add_labels', label_ids: ids } })
  const pinned = (await (await page.request.post('/api/v1/labels', { data: { name: 'Invoices', home_account_id: a.work } })).json()).id as number
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  await page.getByRole('button', { name: 'Main menu' }).first().click()
  const drawer = page.locator('.sidebar.mobile-open')
  const work = drawer.getByRole('group', { name: a.work_address })
  const more = work.getByTestId(`acct-more-${a.work}`)
  await more.scrollIntoViewIfNeeded()
  await expect(work.locator(`[data-pinned-folder="${a.work}:${pinned}"]`)).toBeVisible()
  await expect(more.locator('.count')).toHaveText('2')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(SHOTS, `phone-drawer-closed-${scheme}-${test.info().project.name}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
  await more.click()
  const alpha = work.locator(`a.nav-item[data-drop-view="acct:${a.work}:label:${ids[0]}"]`)
  await expect(alpha).toBeVisible()
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.account_folders_more_open).toEqual([a.work])
  // A long press opens a folder's menu on a phone (the browser's contextmenu, as for every label).
  await alpha.click({ button: 'right' })
  await page.getByRole('menuitem', { name: `Pin to ${a.work_address}` }).click()
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.account_folder_pins).toEqual({ [a.work]: [pinned, ids[0]] })
  if (!(await drawer.count())) await page.getByRole('button', { name: 'Main menu' }).first().click()
  await expect(drawer.locator(`[data-pinned-folder="${a.work}:${ids[0]}"]`)).toBeVisible()
  await drawer.locator(`[data-pinned-folder="${a.work}:${ids[0]}"]`).scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(SHOTS, `phone-drawer-open-light-${test.info().project.name}.png`) })
})
