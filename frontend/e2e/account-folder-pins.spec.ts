// ACT-SURFACES: web.nav web.settings
// Account groups stay short (features/layout/folderPins.tsx, lib/accountFolders.ts,
// backend core/accountpins.py). With "Show each account separately" on, an account's group shows its
// system folders, the folders pinned to it in the user's order, and one row "More folders" with the
// unread mail of the folders inside it; it opens to the rest, and open or closed is a setting
// (ACT mail.account_folders_more). Pin and unpin from a folder's ⋮ or by drag
// (ACT mail.account_folder_pin, ACT mail.account_folder_unpin), reorder with Alt+↑/↓ or the menu
// (ACT mail.account_folder_reorder). A folder made with New folder in an account is pinned there.
// The option "Show folders with unread mail above More folders" (ACT mail.account_folders_show_unread)
// lifts an unread folder out of More folders until it is read. The Focus palette lists pinned
// folders first. Screenshots: Classic, Office, Desk and Cards, light and dark, closed and open.
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { apiSettled, expect, test } from './fixtures'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-account-folder-pins-shots')

const folder = (scope: Page | Locator, view: string) => scope.locator(`a.nav-item[data-drop-view="${view}"]`)
const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

async function settings(page: Page): Promise<Record<string, unknown>> {
  return (await (await page.request.get('/api/v1/me')).json()).settings
}

async function patch(page: Page, data: Record<string, unknown>) {
  const r = await page.request.patch('/api/v1/me/settings', { data })
  expect(r.ok(), await r.text()).toBeTruthy()
}

async function label(page: Page, name: string, extra: Record<string, unknown> = {}): Promise<number> {
  const r = await page.request.post('/api/v1/labels', { data: { name, ...extra } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()).id
}

async function file(page: Page, conv: number, labelId: number) {
  const r = await page.request.post('/api/v1/conversations/actions', { data: { ids: [conv], action: 'add_labels', label_ids: [labelId] } })
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

/** Work's mail in Alpha and Beta (one unread conversation, so each shows 1), Home's in Gamma. */
async function setup(page: Page, a: { conversations: Record<string, number> }) {
  await patch(page, { account_folders: true })
  const ids = { alpha: await label(page, 'Alpha'), beta: await label(page, 'Beta'), gamma: await label(page, 'Gamma') }
  await file(page, a.conversations['E2E Work report'], ids.alpha)
  await file(page, a.conversations['E2E Work report'], ids.beta)
  await file(page, a.conversations['E2E Home letter'], ids.gamma)
  return ids
}
test.use({ accounts: true })

test('Classic: More folders holds the rest with their unread sum, remembers open, and pins from ⋮ (pin, reorder, unpin)', async ({ page, seeded }) => {
  const a = seeded.accounts!
  const ids = await setup(page, a)
  await page.goto('/mail/inbox')
  const work = page.getByTestId('nav-accounts').getByRole('group', { name: a.work_address })
  const more = page.getByTestId(`acct-more-${a.work}`)
  // Closed: the folders are inside it, the row carries their unread mail (1 + 1), bold.
  await expect(more).toHaveAttribute('aria-expanded', 'false')
  await expect(more.locator('.count')).toHaveText('2')
  await expect(more).toHaveClass(/unread/)
  await expect(folder(work, `acct:${a.work}:label:${ids.alpha}`)).toHaveCount(0)
  await shots(page, 'classic-closed')
  // Open: the folders show, and the setting remembers it after a reload.
  await more.click()
  await expect(folder(work, `acct:${a.work}:label:${ids.alpha}`)).toBeVisible()
  await expect(more.locator('.count')).toHaveCount(0)
  await expect.poll(async () => (await settings(page)).account_folders_more_open).toEqual([a.work])
  await page.reload()
  await expect(folder(work, `acct:${a.work}:label:${ids.beta}`)).toBeVisible()
  await expect(page.getByTestId(`acct-more-${a.home}`)).toHaveAttribute('aria-expanded', 'false')

  // Pin Beta, then Alpha, from their ⋮ (ACT mail.account_folder_pin).
  for (const id of [ids.beta, ids.alpha]) {
    const f = folder(work, `acct:${a.work}:label:${id}`)
    await f.hover()
    await f.locator('.more-btn').click()
    await page.getByRole('menuitem', { name: `Pin to ${a.work_address}` }).click()
    await expect(page.locator('.toast', { hasText: `Pinned to ${a.work_address}` })).toBeVisible()
  }
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [ids.beta, ids.alpha] })
  // Pinned folders sit above More folders, in that order; More folders is gone (nothing left).
  const pinned = work.locator('[data-pinned-folder]')
  await expect(pinned).toHaveCount(2)
  await expect(pinned.nth(0)).toHaveAttribute('data-tip', 'Beta')
  await expect(more).toHaveCount(0)
  // Home is untouched: pins are per account.
  await expect(page.getByTestId(`acct-more-${a.home}`)).toBeVisible()

  // Reorder: Alt+↑ on Alpha (ACT mail.account_folder_reorder), then the menu's Move down.
  await apiSettled(page)
  await pinned.nth(1).focus()
  await page.keyboard.press('Alt+ArrowUp')
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [ids.alpha, ids.beta] })
  await expect(page.locator(`[data-pinned-folder="${a.work}:${ids.alpha}"]`)).toBeFocused()
  await apiSettled(page)
  const alpha = folder(work, `acct:${a.work}:label:${ids.alpha}`)
  await alpha.hover()
  await alpha.locator('.more-btn').click()
  await page.getByRole('menuitem', { name: 'Move down' }).click()
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [ids.beta, ids.alpha] })

  // Unpin Beta (ACT mail.account_folder_unpin): it goes back under More folders.
  await apiSettled(page)
  const beta = folder(work, `acct:${a.work}:label:${ids.beta}`)
  await beta.hover()
  await beta.locator('.more-btn').click()
  await page.getByRole('menuitem', { name: 'Unpin' }).click()
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [ids.alpha] })
  await expect(more).toBeVisible()
  await expect(page.locator(`[data-pinned-folder="${a.work}:${ids.beta}"]`)).toHaveCount(0)
  // Undo in the toast pins it again, in its old place.
  await page.locator('.toast').filter({ hasText: 'Unpinned' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [ids.beta, ids.alpha] })
})

test('Classic: drag a folder into the pinned folders, reorder by drag, and drag it out to unpin', async ({ page, seeded }) => {
  const a = seeded.accounts!
  const ids = await setup(page, a)
  const delta = await label(page, 'Delta', { home_account_id: a.work }) // pinned where it was made
  await patch(page, { account_folders_more_open: [a.work] })
  await page.goto('/mail/inbox')
  const work = page.getByTestId('nav-accounts').getByRole('group', { name: a.work_address })
  await expect(page.locator(`[data-pinned-folder="${a.work}:${delta}"]`)).toBeVisible()

  const drag = async (from: Locator, to: () => Locator, at: 'top' | 'bottom' | 'middle' = 'middle') => {
    // Both in view (the nav scrolls): the target when it is there before the drag, else the row.
    const center = (l: Locator) => l.evaluate((el) => el.scrollIntoView({ block: 'center' }))
    await center(from)
    if (await to().count()) await center(to())
    const b = (await from.boundingBox())!
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2)
    await page.mouse.down()
    await page.mouse.move(b.x + b.width / 2 + 10, b.y + b.height / 2 + 6, { steps: 4 })
    const t = to()
    await expect(t).toBeVisible()
    const r = (await t.boundingBox())!
    const y = at === 'top' ? r.y + 4 : at === 'bottom' ? r.y + r.height - 4 : r.y + r.height / 2
    await page.mouse.move(r.x + r.width / 2, y, { steps: 8 })
    await page.mouse.move(r.x + r.width / 2 + 1, y, { steps: 2 })
    await page.mouse.up()
  }

  // From More folders onto "Pin here" (the line a folder drag shows): pinned at the end.
  await drag(folder(work, `acct:${a.work}:label:${ids.alpha}`), () => page.getByTestId(`acct-pin-zone-${a.work}`))
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [delta, ids.alpha] })
  await expect(page.getByTestId(`acct-pin-zone-${a.work}`)).toHaveCount(0)
  // Onto the top half of a pinned folder: pinned before it.
  await drag(folder(work, `acct:${a.work}:label:${ids.beta}`), () => page.locator(`[data-pinned-folder="${a.work}:${delta}"]`), 'top')
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [ids.beta, delta, ids.alpha] })
  await apiSettled(page)
  // Reorder among the pinned: Beta below Alpha.
  await drag(page.locator(`[data-pinned-folder="${a.work}:${ids.beta}"]`), () => page.locator(`[data-pinned-folder="${a.work}:${ids.alpha}"]`), 'bottom')
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [delta, ids.alpha, ids.beta] })
  await apiSettled(page)
  // Out onto More folders: unpinned. Work's folders are all pinned now, so Gamma gets Work's mail
  // first and More folders comes back.
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [a.conversations['E2E Work report']], action: 'add_labels', label_ids: [ids.gamma] } })
  await page.reload()
  await expect(page.getByTestId(`acct-more-${a.work}`)).toBeVisible()
  await drag(page.locator(`[data-pinned-folder="${a.work}:${ids.alpha}"]`), () => page.getByTestId(`acct-more-${a.work}`))
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [delta, ids.beta] })
  await expect(page.locator(`[data-pinned-folder="${a.work}:${ids.alpha}"]`)).toHaveCount(0)
  await expect(folder(work, `acct:${a.work}:label:${ids.alpha}`)).toBeVisible()
})

test('New folder in an account pins it there; the option shows unread folders above More folders; the palette lists pinned first', async ({ page, seeded }) => {
  const a = seeded.accounts!
  const ids = await setup(page, a)
  await page.goto('/mail/inbox')
  const head = page.getByTestId(`acct-group-${a.work}`)
  await head.hover()
  await page.getByTestId(`acct-menu-${a.work}`).click()
  await page.getByRole('menuitem', { name: 'New folder' }).click()
  const dialog = page.getByRole('dialog', { name: `New folder in ${a.work_address}` })
  await dialog.getByRole('textbox').first().fill('Zulu')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(dialog).toHaveCount(0)
  const zulu = (((await (await page.request.get('/api/v1/labels')).json()) as { id: number; name: string }[]).find((l) => l.name === 'Zulu'))!.id
  await expect.poll(async () => (await settings(page)).account_folder_pins).toEqual({ [a.work]: [zulu] })
  await expect(page.locator(`[data-pinned-folder="${a.work}:${zulu}"]`)).toBeVisible()
  // Above More folders, which stays closed.
  const more = page.getByTestId(`acct-more-${a.work}`)
  const zb = (await page.locator(`[data-pinned-folder="${a.work}:${zulu}"]`).boundingBox())!
  expect(zb.y).toBeLessThan((await more.boundingBox())!.y)

  // The option (Settings → Mail → Inbox): off by default; on, unread folders show above More folders.
  await page.goto('/settings/mail/inbox')
  const option = page.getByRole('checkbox', { name: 'Show folders with unread mail above More folders' })
  await expect(option).not.toBeChecked()
  await option.check()
  await expect.poll(async () => (await settings(page)).account_folders_show_unread).toBe(true)
  await page.goto('/mail/inbox')
  const work = page.getByTestId('nav-accounts').getByRole('group', { name: a.work_address })
  await expect(folder(work, `acct:${a.work}:label:${ids.alpha}`)).toBeVisible()
  await expect(folder(work, `acct:${a.work}:label:${ids.beta}`)).toBeVisible()
  // Nothing unread is left inside: no More folders row in Work (all its folders show).
  await expect(more).toHaveCount(0)
  // The pinned folder keeps its place first.
  expect((await page.locator(`[data-pinned-folder="${a.work}:${zulu}"]`).boundingBox())!.y).toBeLessThan((await folder(work, `acct:${a.work}:label:${ids.alpha}`).boundingBox())!.y)
  await shots(page, 'classic-show-unread')
  // Read: they go back under More folders.
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [a.conversations['E2E Work report']], action: 'read' } })
  await page.reload()
  await expect(more).toBeVisible()
  await expect(more.locator('.count')).toHaveCount(0)
  await expect(folder(work, `acct:${a.work}:label:${ids.alpha}`)).toHaveCount(0)

  // Focus: the palette lists Work's pinned folder before the others.
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'focus'))
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Work report')).toBeVisible()
  await page.keyboard.press('Control+k')
  await page.keyboard.type(a.work_address)
  const options = page.getByRole('option')
  await expect(options.filter({ hasText: `Zulu · ${a.work_address}` })).toBeVisible()
  const names = await options.allTextContents()
  const at = (n: string) => names.findIndex((x) => x.includes(`${n} · ${a.work_address}`))
  expect(at('Zulu')).toBeGreaterThanOrEqual(0)
  expect(at('Zulu')).toBeLessThan(at('Alpha'))
})

test('Office, Desk and Cards: the collapsed group with its unread sum, and open', async ({ page, seeded }) => {
  const a = seeded.accounts!
  const ids = await setup(page, a)
  await patch(page, { account_folder_pins: { [a.work]: [ids.beta] } })
  for (const theme of ['office', 'desk', 'cards'] as const) {
    await patch(page, { account_folders_more_open: [] })
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/mail/inbox')
    await expect(row(page, 'E2E Work report')).toBeVisible()
    if (theme === 'cards') await page.getByRole('button', { name: 'All mailboxes and labels' }).first().click()
    const more = page.getByTestId(`acct-more-${a.work}`)
    await more.scrollIntoViewIfNeeded()
    await expect(page.locator(`[data-pinned-folder="${a.work}:${ids.beta}"]`)).toBeVisible()
    await expect(more.locator('.count')).toHaveText('1')
    await expect(page.locator(`a.nav-item[data-drop-view="acct:${a.work}:label:${ids.alpha}"]`)).toHaveCount(0)
    await shots(page, `${theme}-closed`)
    await more.click()
    await expect(page.locator(`a.nav-item[data-drop-view="acct:${a.work}:label:${ids.alpha}"]`)).toBeVisible()
    await expect.poll(async () => (await settings(page)).account_folders_more_open).toEqual([a.work])
    await shots(page, `${theme}-open`)
  }
})
