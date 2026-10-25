// ACT-SURFACES: web.nav web.palette
// Folders inside each mail account (features/layout/accounts.tsx, backend
// core/mailaccounts.py). With "Show each account separately" on, each account's group lists, below
// its system folders, the labels that hold its mail and the labels made in it. "New folder" in an
// account's ⋮ makes a label whose home is that account (ACT mail.account_folder_create); it shows
// there at once, empty. Each folder opens the label narrowed to the account
// (ACT mail.account_label_view), with the account's own numbers. A label is one label: filing another
// account's mail into it is allowed and a toast says the mail stays in its own account. The unified
// Labels list shows a home label with its account's dot, and the setting only changes the
// presentation. Screenshots: Classic, Office, Desk and Cards, light and dark, the setting on and off.
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { apiSettled, conv, expect, test } from './fixtures'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-account-folders-shots')

/** A folder in the navigation by the view it opens (its link's name also holds its ⋮ button). */
const folder = (scope: Page | Locator, view: string) => scope.locator(`a.nav-item[data-drop-view="${view}"]`)
const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

async function theme(page: Page, name: string) {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), name)
}

async function folders(page: Page, on: boolean) {
  // Every account's More folders open, so the folders that are not pinned show too
  // (account-folder-pins.spec.ts covers the collapsed row).
  const open = (await (await page.request.get('/api/v1/counts')).json()).accounts.map((x: { id: number }) => x.id)
  const r = await page.request.patch('/api/v1/me/settings', { data: { account_folders: on, account_folders_more_open: open } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

async function shots(page: Page, name: string) {
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(250)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${scheme}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
}

async function newFolder(page: Page, menuButton: Locator, name: string, address: string) {
  await menuButton.click()
  await page.getByRole('menuitem', { name: 'New folder' }).click()
  const dialog = page.getByRole('dialog', { name: `New folder in ${address}` })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByTestId('new-folder-account')).toBeVisible()
  await dialog.getByRole('textbox').first().fill(name)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(dialog).toHaveCount(0)
}

async function labelId(page: Page, name: string): Promise<number> {
  const labels = (await (await page.request.get('/api/v1/labels')).json()) as { id: number; name: string; home_account_id: number | null }[]
  return labels.find((l) => l.name === name)!.id
}

test.use({ accounts: true })

test('Classic: a folder made in an account shows there, takes mail of another account, counts, renames, and survives the setting', async ({ page, seeded }) => {
  const a = seeded.accounts!
  await folders(page, true)
  await page.goto('/mail/inbox')
  const nav = page.getByTestId('nav-accounts')
  const work = nav.getByRole('group', { name: a.work_address })
  const home = nav.getByRole('group', { name: a.home_address })
  await expect(work.getByRole('link', { name: 'Inbox' })).toBeVisible()
  // The heading's dot and address sit together at the start, as other rows' icon and name do.
  const head = page.getByTestId(`acct-group-${a.work}`)
  const dot = await head.locator('.acct-dot').boundingBox()
  const addr = await head.locator('.name').boundingBox()
  const caret = await head.locator('.caret').boundingBox()
  expect(addr!.x - (dot!.x + dot!.width)).toBeLessThan(14)
  expect(dot!.x - (caret!.x + caret!.width)).toBeLessThan(24)
  // Right to left: the dot leads on the right, the address right after it on its left.
  await page.evaluate(() => { document.documentElement.dir = 'rtl' })
  const rdot = await head.locator('.acct-dot').boundingBox()
  const raddr = await head.locator('.name').boundingBox()
  expect(raddr!.x + raddr!.width).toBeLessThanOrEqual(rdot!.x + 1)
  expect(rdot!.x - (raddr!.x + raddr!.width)).toBeLessThan(14)
  await page.screenshot({ path: path.join(SHOTS, 'classic-rtl-light.png') })
  await page.evaluate(() => { document.documentElement.dir = 'ltr' })

  // New folder in Work's ⋮: it opens at once, empty, under Work.
  await head.hover()
  await newFolder(page, page.getByTestId(`acct-menu-${a.work}`), 'Receipts', a.work_address)
  const id = await labelId(page, 'Receipts')
  await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Alabel%3A${id}$`))
  await expect(page.getByText('No conversations with this label.')).toBeVisible()
  const workReceipts = folder(work, `acct:${a.work}:label:${id}`)
  await expect(workReceipts).toBeVisible()
  await expect(workReceipts).toHaveAttribute('aria-current', 'page')
  await expect(folder(home, `acct:${a.home}:label:${id}`)).toHaveCount(0)
  await expect(page).toHaveTitle(new RegExp(`Receipts · ${a.work_address.replace(/\./g, '\\.')}`))
  // The unified Labels list holds the same label, with Work's dot.
  const unified = page.locator('.nav > a.nav-item[data-drop-view="label:' + id + '"]')
  await expect(unified.getByRole('img', { name: `Account: ${a.work_address}` })).toBeVisible()
  await shots(page, 'classic-folder-empty')

  // File Home's mail in Work's folder: allowed, it is the same label; the toast says it stays put.
  await page.goto(`/mail/acct:${a.home}:inbox`)
  await expect(row(page, 'E2E Home letter')).toBeVisible()
  await row(page, 'E2E Home letter').click({ button: 'right', position: { x: 200, y: 10 } })
  const menu = page.getByRole('menu', { name: 'Conversation actions' })
  await menu.getByRole('menuitem', { name: /^Label as( \S)?$/ }).hover()
  const lab = page.getByRole('menu', { name: 'Label as', exact: true })
  await lab.getByRole('menuitemcheckbox', { name: 'Receipts' }).click()
  await lab.getByRole('menuitem', { name: 'Apply' }).click()
  await expect(page.locator('.toast', { hasText: `The mail stays in its own account; nothing is copied to ${a.work_address}.` })).toBeVisible()
  await expect.poll(async () => (await conv(page.request, 'E2E Home letter')).label_ids).toContain(id)
  // Home's group now lists Receipts with its own mail (one unread); Work's stays empty.
  const homeReceipts = folder(home, `acct:${a.home}:label:${id}`)
  await expect(homeReceipts).toContainText('1')
  await expect(workReceipts).not.toContainText(/\d/)
  await homeReceipts.click()
  await expect(row(page, 'E2E Home letter')).toBeVisible()
  await page.goto(`/mail/acct:${a.work}:label:${id}`)
  await expect(page.getByText('No conversations with this label.')).toBeVisible()
  // The unified label holds it.
  await page.goto(`/mail/label:${id}`)
  await expect(row(page, 'E2E Home letter')).toBeVisible()

  // Work's own mail filed there counts under Work.
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [a.conversations['E2E Work report']], action: 'add_labels', label_ids: [id] } })
  await page.goto(`/mail/acct:${a.work}:label:${id}`)
  await expect(row(page, 'E2E Work report')).toBeVisible()
  await expect(row(page, 'E2E Home letter')).toHaveCount(0)
  await expect(workReceipts).toContainText('1')
  await shots(page, 'classic-on')

  // Nest a folder inside it from the account's tree, then rename the parent from the group.
  await workReceipts.hover()
  await workReceipts.getByRole('button', { name: 'Options for Receipts' }).click()
  await page.getByRole('menuitem', { name: 'New folder inside' }).click()
  const dialog = page.getByRole('dialog', { name: `New folder in ${a.work_address}` })
  await dialog.getByRole('textbox').first().fill('2026')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(work.locator('a.nav-item[data-tip="Receipts/2026"]')).toBeVisible()
  await apiSettled(page)
  await workReceipts.hover()
  await workReceipts.getByRole('button', { name: 'Options for Receipts' }).click()
  await page.getByRole('menuitem', { name: 'Edit' }).click()
  const edit = page.getByRole('dialog', { name: 'Edit label' })
  await edit.getByRole('textbox').fill('Bills')
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect(workReceipts).toHaveAttribute('data-tip', 'Bills')
  await expect(homeReceipts).toHaveAttribute('data-tip', 'Bills')
  await expect(work.locator('a.nav-item[data-tip="Bills/2026"]')).toBeVisible()

  // Off: the account groups go; the label stays in the unified list with its dot. On: back again.
  await folders(page, false)
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Work report')).toBeVisible()
  await expect(page.getByTestId('nav-accounts')).toHaveCount(0)
  await expect(page.locator(`a.nav-item[data-drop-view="label:${id}"]`).getByRole('img', { name: `Account: ${a.work_address}` })).toBeVisible()
  await shots(page, 'classic-off')
  await folders(page, true)
  await page.reload()
  await expect(folder(page.getByTestId('nav-accounts'), `acct:${a.work}:label:${id}`)).toHaveAttribute('data-tip', 'Bills')
})

test('Office: New folder in an account node; its folders show as a tree with counts', async ({ page, seeded }) => {
  const a = seeded.accounts!
  await folders(page, true)
  await theme(page, 'office')
  await page.goto('/mail/inbox')
  const node = page.locator(`.o-section[data-section="acct.${a.work}"]`)
  await expect(node.getByRole('link', { name: 'Inbox' })).toBeVisible()
  await node.locator('.o-section-head').hover()
  await newFolder(page, page.getByTestId(`acct-menu-${a.work}`), 'Travel', a.work_address)
  const id = await labelId(page, 'Travel')
  await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Alabel%3A${id}$`))
  await expect(page.locator('.o-list-title h1')).toHaveText(`Travel · ${a.work_address}`)
  await expect(folder(node, `acct:${a.work}:label:${id}`)).toBeVisible()
  // A nested label with Work's mail brings its parent into the tree.
  const r = await page.request.post('/api/v1/labels', { data: { name: 'Travel/Hotels' } })
  const nested = (await r.json()).id as number
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [a.conversations['E2E Work report']], action: 'add_labels', label_ids: [nested] } })
  await page.goto('/mail/inbox')
  const travel = folder(node, `acct:${a.work}:label:${id}`)
  const hotels = folder(node, `acct:${a.work}:label:${nested}`)
  await expect(hotels).toContainText('1')
  await expect(travel).toContainText('1')
  const pad = async (l: Locator) => Number.parseFloat(await l.evaluate((el) => getComputedStyle(el).paddingInlineStart))
  expect(await pad(hotels)).toBeGreaterThan(await pad(travel))
  // The Home node lists neither.
  await expect(page.locator(`.o-section[data-section="acct.${a.home}"]`).locator('a.nav-item[data-tip^="Travel"]')).toHaveCount(0)
  // The unified Labels node keeps them, Travel with Work's dot.
  await expect(page.locator('.o-section[data-section="labels"]').getByRole('img', { name: `Account: ${a.work_address}` })).toBeVisible()
  await shots(page, 'office-on')
  await node.scrollIntoViewIfNeeded()
  await travel.scrollIntoViewIfNeeded()
  await shots(page, 'office-on-nodes')
  await folders(page, false)
  await page.reload()
  await expect(page.locator(`.o-section[data-section="acct.${a.work}"]`)).toHaveCount(0)
  await shots(page, 'office-off')
})

test('Desk, Cards and the Focus palette show the folders; the palette makes one', async ({ page, seeded }) => {
  const a = seeded.accounts!
  await folders(page, true)
  const r = await page.request.post('/api/v1/labels', { data: { name: 'Projects', home_account_id: a.work } })
  const id = (await r.json()).id as number
  await theme(page, 'desk')
  await page.goto('/mail/inbox')
  const desk = page.locator('.d-folders').getByRole('group', { name: a.work_address })
  await expect(folder(desk, `acct:${a.work}:label:${id}`)).toBeVisible()
  // Desk's heading: the address right after the dot, not pushed to the end.
  const head = page.getByTestId(`acct-group-${a.work}`)
  const dot = await head.locator('.acct-dot').boundingBox()
  const addr = await head.locator('.name').boundingBox()
  expect(addr!.x - (dot!.x + dot!.width)).toBeLessThan(16)
  await folder(desk, `acct:${a.work}:label:${id}`).click()
  await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Alabel%3A${id}$`))
  await expect(page.locator('.d-status')).toContainText('Total: 0')
  await shots(page, 'desk-on')

  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'cards'))
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Work report')).toBeVisible()
  await page.getByRole('button', { name: 'All mailboxes and labels' }).first().click()
  const drawer = page.locator('.c-drawer.open')
  await expect(drawer).toBeVisible()
  const group = drawer.getByRole('group', { name: a.work_address })
  await expect(folder(group, `acct:${a.work}:label:${id}`)).toBeVisible()
  await expect(group.getByTestId(`acct-menu-${a.work}`)).toBeVisible()
  await folder(group, `acct:${a.work}:label:${id}`).scrollIntoViewIfNeeded()
  await shots(page, 'cards-on')
  await page.keyboard.press('Escape')

  // Focus: the palette lists the folder and makes a new one.
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'focus'))
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Work report')).toBeVisible()
  await page.keyboard.press('Control+k')
  await page.keyboard.type(`Projects · ${a.work_address}`)
  await page.getByRole('option', { name: new RegExp(`Projects · ${a.work_address.replace(/\./g, '\\.')}`) }).first().click()
  await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Alabel%3A${id}$`))
  await page.keyboard.press('Control+k')
  await page.keyboard.type(`New folder in ${a.work_address}`)
  await page.getByRole('option', { name: `New folder in ${a.work_address}` }).first().click()
  const dialog = page.getByRole('dialog', { name: `New folder in ${a.work_address}` })
  await dialog.getByRole('textbox').first().fill('Contracts')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(dialog).toHaveCount(0)
  const made = await labelId(page, 'Contracts')
  await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Alabel%3A${made}$`))
  const labels = (await (await page.request.get('/api/v1/labels')).json()) as { id: number; home_account_id: number | null }[]
  expect(labels.find((l) => l.id === made)!.home_account_id).toBe(a.work)
})
