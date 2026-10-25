// ACT-SURFACES: web.section
// Section header actions (surface web.section): select all in a
// section, and Mark all as read / unread, Archive all, Delete all, Snooze all, Move all to, Label
// all, Mark all as important / not important and Mute all, on every conversation of the section
// (not only the rows loaded on screen), in every theme. Each test checks the result through the API.
// Every one of them asks first in one confirmation (title with the count and the section, the
// primary button named for the action and focused, Enter confirms, Escape and Cancel change
// nothing); the toolbar's Mark all as read asks too.
// Covers: mail.read, mail.unread, mail.archive, mail.delete, mail.snooze, mail.move, mail.label,
// mail.important, mail.mute.
import os from 'node:os'
import path from 'node:path'
import fs from 'node:fs'
import type { APIRequestContext, Page } from '@playwright/test'
import { conv, expect, roles, test } from './fixtures'
import { openApp } from './helpers'
import { THEMES, type Theme } from './matrix'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-section-shots')

/** A layout with a small "Unread" section (3 rows shown of all unread), "Everything else" below,
 *  and a one-section tab "Shopping" (the E2E Bulk mail when seeded). */
async function useSections(request: APIRequestContext, pageSize = 50) {
  const layout = {
    name: 'E2E Sections', tabs: [
      { name: 'Inbox', icon: 'inbox', sections: [
        { title: 'Unread', base: 'inbox', filter: 'unread', limit: 3 },
        { title: 'Everything else', base: 'inbox' },
      ] },
      { name: 'Shopping', icon: 'bookmark', sections: [{ title: 'Shopping Emails', base: 'inbox', q: 'subject:"E2E Bulk"' }] },
    ],
  }
  const r = await request.put('/api/v1/layouts', { data: { layouts: [layout], active: 'e2e-sections' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  expect((await request.patch('/api/v1/me/settings', { data: { page_size: pageSize } })).ok()).toBeTruthy()
}

async function count(request: APIRequestContext, params: Record<string, string>) {
  const r = await request.get('/api/v1/conversations', { params: { limit: '1', ...params } })
  return (await r.json()).total as number
}
const inboxUnread = (request: APIRequestContext) => count(request, { view: 'inbox', q: 'is:unread' })
const inboxTotal = (request: APIRequestContext) => count(request, { view: 'inbox' })

const head = (page: Page, sec: string) => page.locator(`[data-section-head="${sec}"]`)

async function open(page: Page, theme: Theme = 'classic', dark = false) {
  await openApp(page, '/mail/inbox', { theme, dark, ready: head(page, 'unread') })
  await expect(head(page, 'unread').locator('.count')).toHaveText(/\d/)
}

async function act(page: Page, sec: string, name: string) {
  await head(page, sec).hover()
  await head(page, sec).getByRole('button', { name, exact: true }).click()
}

async function menu(page: Page, sec: string, item: string | RegExp) {
  await head(page, sec).hover()
  await head(page, sec).getByRole('button', { name: /More actions for all in/ }).click()
  await page.getByRole('menuitem', { name: item }).click()
}

const undoToast = (page: Page) => page.getByRole('button', { name: 'Undo' }).last()

/** The confirmation: its title, its focused primary button, then a click on it. */
async function confirm(page: Page, title: string, button: string) {
  const d = page.getByRole('dialog', { name: title })
  await expect(d).toBeVisible()
  const ok = d.getByRole('button', { name: button, exact: true })
  await expect(ok).toBeFocused()
  await ok.click()
  await expect(d).toHaveCount(0)
}

test('web.section: select all in a section, and mail.read on a section with more rows than loaded, with Undo', async ({ page }) => {
  await useSections(page.request)
  await open(page)
  const total = await inboxUnread(page.request)
  expect(total).toBeGreaterThan(3)
  // Three rows shown, the whole section counted.
  await expect(page.locator('section[aria-label="Unread"] [role="row"]')).toHaveCount(3)
  await expect(head(page, 'unread').locator('.count')).toHaveText(String(total))

  // Select all in this section: every conversation of it, not only the three rows.
  await head(page, 'unread').hover()
  await head(page, 'unread').getByRole('checkbox', { name: 'Select all in Unread' }).check()
  await expect(page.getByRole('status').filter({ hasText: `All ${total} conversations in Unread are selected` })).toBeVisible()
  await expect(page.locator('section[aria-label="Unread"] [role="row"][aria-selected="true"]')).toHaveCount(3)
  // Leaving one row out makes the box mixed.
  await page.locator('section[aria-label="Unread"] [role="row"]').first().hover()
  await page.locator('section[aria-label="Unread"] [role="row"]').first().locator('[role="checkbox"][aria-checked="true"]').first().click()
  await expect(page.getByRole('status').filter({ hasText: `${total - 1} of ${total} conversations in Unread` })).toBeVisible()
  expect(await head(page, 'unread').getByRole('checkbox').evaluate((el) => (el as HTMLInputElement).indeterminate)).toBe(true)
  await head(page, 'unread').getByRole('checkbox').check()
  await head(page, 'unread').getByRole('checkbox', { name: 'Deselect all in Unread' }).uncheck()
  await expect(page.getByRole('status').filter({ hasText: 'are selected' })).toHaveCount(0)

  // Mark all as read: every unread conversation, also the ones not loaded. It asks first, with the
  // server's count and the rows not on screen; Enter on the focused button confirms.
  await act(page, 'unread', 'Mark all as read')
  const d = page.getByRole('dialog', { name: `Mark ${total} conversations in Unread as read?` })
  await expect(d).toContainText(`included, with ${total - 3} conversations not on screen`)
  await expect(d.getByRole('button', { name: `Mark ${total} as read` })).toBeFocused()
  await expect(d.getByRole('button', { name: `Mark ${total} as read` })).not.toHaveClass(/danger/)
  await page.keyboard.press('Enter')
  await expect(d).toHaveCount(0)
  await expect.poll(() => inboxUnread(page.request)).toBe(0)
  await expect(page.getByText(`${total} conversations in Unread marked as read.`)).toBeVisible()
  await undoToast(page).click()
  await expect.poll(() => inboxUnread(page.request)).toBe(total)
})

test('mail.archive: Archive all in a section, then Undo', async ({ page }) => {
  await useSections(page.request)
  await open(page)
  const r = await roles(page.request)
  const total = await inboxTotal(page.request)
  // A small section asks too. Cancel and Escape change nothing.
  await act(page, 'unread', 'Archive all')
  const d = page.getByRole('dialog', { name: `Archive ${total} conversations in Unread?` })
  await expect(d).toContainText('You can undo this')
  await d.getByRole('button', { name: 'Cancel' }).click()
  await expect(d).toHaveCount(0)
  await act(page, 'unread', 'Archive all')
  await expect(d).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(d).toHaveCount(0)
  expect(await inboxTotal(page.request)).toBe(total)
  await act(page, 'unread', 'Archive all')
  await confirm(page, `Archive ${total} conversations in Unread?`, `Archive ${total}`)
  await expect.poll(() => inboxTotal(page.request)).toBe(0)
  expect((await conv(page.request, 'E2E Archive me')).label_ids).not.toContain(r.inbox)
  await undoToast(page).click()
  await expect.poll(() => inboxTotal(page.request)).toBe(total)
  expect((await conv(page.request, 'E2E Archive me')).label_ids).toContain(r.inbox)
})

test.describe('large sections', () => {
  test.use({ bulk: 30 })

  test('mail.delete: Delete all asks with the count, styled as destructive, then Undo; the main list pages', async ({ page }) => {
    await useSections(page.request, 5)
    await open(page)
    const r = await roles(page.request)
    const total = await inboxTotal(page.request)
    expect(total).toBeGreaterThan(25)
    await act(page, 'unread', 'Delete all')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText(`Delete ${total} conversations in Unread?`)
    await expect(dialog).toContainText('They move to Trash.')
    await expect(dialog.getByRole('button', { name: `Delete ${total}` })).toHaveClass(/danger/)
    await expect(dialog.getByRole('button', { name: `Delete ${total}` })).toBeFocused()
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    expect(await inboxTotal(page.request)).toBe(total)

    await act(page, 'unread', 'Delete all')
    await page.getByRole('dialog').getByRole('button', { name: `Delete ${total}` }).click()
    await expect.poll(() => inboxTotal(page.request)).toBe(0)
    expect((await conv(page.request, 'E2E Bulk 29')).label_ids).toContain(r.trash)
    await undoToast(page).click()
    await expect.poll(() => inboxTotal(page.request)).toBe(total)
    expect((await conv(page.request, 'E2E Bulk 29')).label_ids).toContain(r.inbox)

    // Archive all asks too.
    await act(page, 'unread', 'Archive all')
    await expect(page.getByRole('dialog')).toContainText(`Archive ${total} conversations in Unread?`)
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
  })

  test('a one-section tab: Mark all as read and unread on "Shopping Emails" (30, 5 per page)', async ({ page }) => {
    await useSections(page.request, 5)
    await open(page)
    await page.getByRole('tab', { name: /Shopping/ }).click()
    const sec = head(page, 'shopping-emails')
    await expect(sec.locator('.count')).toHaveText('30')
    await act(page, 'shopping-emails', 'Mark all as read')
    // 30 unread, 5 on screen: the count is the server's.
    await expect(page.getByRole('dialog')).toContainText('included, with 25 conversations not on screen')
    await confirm(page, 'Mark 30 conversations in Shopping Emails as read?', 'Mark 30 as read')
    await expect.poll(() => count(page.request, { view: 'inbox', q: 'subject:"E2E Bulk" is:unread' })).toBe(0)
    // The rest of the inbox is untouched.
    expect(await inboxUnread(page.request)).toBeGreaterThan(0)
    // All read now: the button offers the opposite.
    await act(page, 'shopping-emails', 'Mark all as unread')
    await confirm(page, 'Mark 30 conversations in Shopping Emails as unread?', 'Mark 30 as unread')
    await expect.poll(() => count(page.request, { view: 'inbox', q: 'subject:"E2E Bulk" is:unread' })).toBe(30)
  })
})

for (const theme of THEMES) {
  test(`web.section in ${theme}: header actions, mail.important, mail.label and the ⋯ menu`, async ({ page }) => {
    await useSections(page.request)
    await open(page, theme)
    const total = await inboxUnread(page.request)
    const sec = head(page, 'unread')
    await sec.hover()
    for (const name of ['Select all in Unread', 'Mark all as read', 'Archive all', 'Delete all', 'More actions for all in Unread']) {
      await expect(sec.getByRole(name.startsWith('Select') ? 'checkbox' : 'button', { name, exact: true })).toBeVisible()
    }
    // The header fits its pane: nothing spills out.
    const fits = await sec.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
    expect(fits).toBe(true)
    fs.mkdirSync(SHOTS, { recursive: true })
    await sec.screenshot({ path: `${SHOTS}/section-${theme}.png` })

    // The three header buttons ask first in this theme too; Cancel leaves the section as it was.
    const all = await inboxTotal(page.request)
    for (const [name, title] of [
      ['Mark all as read', `Mark ${total} conversations in Unread as read?`],
      ['Archive all', `Archive ${all} conversations in Unread?`],
      ['Delete all', `Delete ${all} conversations in Unread?`],
    ]) {
      await act(page, 'unread', name)
      await page.getByRole('dialog', { name: title }).getByRole('button', { name: 'Cancel' }).click()
      await expect(page.getByRole('dialog')).toHaveCount(0)
    }
    if (theme === 'classic' || theme === 'cards') {
      await act(page, 'unread', 'Delete all')
      await page.getByRole('dialog').waitFor()
      await page.waitForTimeout(250)
      await page.screenshot({ path: `${SHOTS}/confirm-${theme}.png` })
      await page.keyboard.press('Escape')
    }
    expect(await inboxTotal(page.request)).toBe(all)
    expect(await inboxUnread(page.request)).toBe(total)

    await menu(page, 'unread', 'Mark all as important')
    await confirm(page, `Mark ${all} conversations in Unread as important?`, `Mark ${all} as important`)
    await expect.poll(() => count(page.request, { view: 'inbox', q: 'is:important' })).toBe(total)
    await menu(page, 'unread', 'Mark all as not important')
    await confirm(page, `Mark ${all} conversations in Unread as not important?`, `Mark ${all} as not important`)
    await expect.poll(() => count(page.request, { view: 'inbox', q: 'is:important' })).toBe(0)

    // Label all ▸ opens the label menu for the whole section, and asks once after Apply.
    await menu(page, 'unread', /Label all/)
    await page.getByRole('menuitemcheckbox', { name: 'E2E Label' }).click()
    await page.getByRole('menuitem', { name: 'Apply' }).click()
    await confirm(page, `Label ${all} conversations in Unread?`, `Label ${all}`)
    await expect.poll(() => count(page.request, { view: 'inbox', q: 'label:"E2E Label"' })).toBe(total)
  })
}

test('mail.snooze, mail.move and mail.mute for a whole section', async ({ page }) => {
  await useSections(page.request)
  await open(page)
  const total = await inboxTotal(page.request)
  await menu(page, 'unread', /Snooze all/)
  await page.getByRole('menuitem', { name: /Tomorrow/ }).click()
  await confirm(page, `Snooze ${total} conversations in Unread?`, `Snooze ${total}`)
  await expect.poll(() => count(page.request, { view: 'snoozed' })).toBe(total)
  await undoToast(page).click()
  await expect.poll(() => inboxTotal(page.request)).toBe(total)

  await menu(page, 'unread', /Move all to/)
  await page.getByRole('menuitem', { name: 'E2E Other' }).click()
  // Cancel on a submenu's confirmation runs nothing.
  await page.getByRole('dialog', { name: `Move ${total} conversations in Unread to E2E Other?` }).getByRole('button', { name: 'Cancel' }).click()
  expect(await inboxTotal(page.request)).toBe(total)
  await menu(page, 'unread', /Move all to/)
  await page.getByRole('menuitem', { name: 'E2E Other' }).click()
  await confirm(page, `Move ${total} conversations in Unread to E2E Other?`, `Move ${total}`)
  await expect.poll(() => count(page.request, { view: 'all', q: 'label:"E2E Other"' })).toBe(total)
  await expect.poll(() => inboxTotal(page.request)).toBe(0)
  await undoToast(page).click()
  await expect.poll(() => inboxTotal(page.request)).toBe(total)

  await menu(page, 'unread', 'Mute all')
  await confirm(page, `Mute ${total} conversations in Unread?`, `Mute ${total}`)
  await expect.poll(() => inboxTotal(page.request)).toBe(0)
  expect((await conv(page.request, 'E2E Mute me')).muted).toBe(true)
  await undoToast(page).click()
  await expect.poll(() => inboxTotal(page.request)).toBe(total)
})

test('mail.read: the toolbar\'s Mark all as read asks first and covers only the rows on screen', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.locator('[role="row"]').first()).toBeVisible()
  const unread = await inboxUnread(page.request)
  expect(unread).toBeGreaterThan(0)
  const more = page.getByRole('toolbar', { name: 'Conversation actions' }).getByRole('button', { name: 'More', exact: true })
  await more.click()
  await page.getByRole('menuitem', { name: 'Mark all as read' }).click()
  const d = page.getByRole('dialog', { name: /^Mark \d+ conversations? in .+ as read\?$/ })
  await expect(d).toContainText('Only conversations on screen are included.')
  await d.getByRole('button', { name: 'Cancel' }).click()
  expect(await inboxUnread(page.request)).toBe(unread)
  await more.click()
  await page.getByRole('menuitem', { name: 'Mark all as read' }).click()
  await expect(d.getByRole('button', { name: /^Mark \d+ as read$/ })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(() => inboxUnread(page.request)).toBe(0)
  await undoToast(page).click()
  await expect.poll(() => inboxUnread(page.request)).toBe(unread)
})

test('* x selects all in the focused conversation\'s section; the ? help lists it', async ({ page }) => {
  await useSections(page.request)
  await open(page)
  const total = await inboxUnread(page.request)
  // Focus on the page itself (a click at the left edge lands on the Drafts link in the nav).
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('j')
  await page.keyboard.press('*')
  await page.keyboard.press('x')
  await expect(page.getByRole('status').filter({ hasText: `All ${total} conversations in Unread are selected` })).toBeVisible()
  // * n clears it, * a still selects the loaded rows.
  await page.keyboard.press('*')
  await page.keyboard.press('n')
  await expect(page.getByRole('status').filter({ hasText: 'are selected' })).toHaveCount(0)
  await page.keyboard.press('?')
  await expect(page.getByText('Select all in this section')).toBeVisible()
})

test('screenshots: section header in light and dark, Classic and Desk, and at phone width', async ({ page }) => {
  await useSections(page.request)
  fs.mkdirSync(SHOTS, { recursive: true })
  for (const theme of ['classic', 'desk'] as const) {
    for (const dark of [false, true]) {
      await open(page, theme, dark)
      await head(page, 'unread').hover()
      await page.locator('.list-pane').screenshot({ path: `${SHOTS}/list-${theme}-${dark ? 'dark' : 'light'}.png` })
    }
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await open(page, 'classic')
  // On a phone the actions show without hover, compact; Archive all and Delete all sit in ⋯
  // (beside Mark all as read, Archive all was an easy mis-tap on a whole section).
  await expect(head(page, 'unread').getByRole('button', { name: 'Mark all as read' })).toBeVisible()
  await expect(head(page, 'unread').getByRole('button', { name: 'Archive all' })).toHaveCount(0)
  const fits = await head(page, 'unread').evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
  expect(fits).toBe(true)
  await page.screenshot({ path: `${SHOTS}/phone-classic.png` })
  await head(page, 'unread').getByRole('button', { name: /More actions/ }).click()
  await expect(page.getByRole('menuitem', { name: 'Archive all' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Delete all' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/phone-classic-menu.png` })
})
