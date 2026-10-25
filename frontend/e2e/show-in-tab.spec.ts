// ACT-SURFACES: web.list web.reader web.palette
// Show in tab (mail.show_in_tab) and the Category submenu (mail.category) in the row's menu, the
// reader's ⋮ menu and the command palette, in Classic, Cards, Desk and Workspace: exactly the
// layout's tabs with one check, a tab defined by a search greyed out, picking a tab moves the row
// into it, Undo, the raw category under More, and "Move to folder" with its hint.
// Screenshots go to E2E_SHOTS (default: <tmp>/mailr-show-in-tab-shots).
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { conv, expect, test, type Seed } from './fixtures'

type Theme = 'classic' | 'cards' | 'desk' | 'office'
const THEMES: Theme[] = ['classic', 'cards', 'desk', 'office']
const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-show-in-tab-shots')
const SUBJECT = 'E2E Reply to me'

const rows = (page: Page) => page.locator('[data-mail-list] [role="row"]')
const row = (page: Page, subject: string) => rows(page).filter({ hasText: subject }).first()
const menu = (page: Page) => page.getByRole('menu', { name: 'Conversation actions' })
const sub = (page: Page, name: string) => page.getByRole('menu', { name, exact: true })
const trigger = (scope: Locator, name: string) => scope.getByRole('menuitem', { name: new RegExp(`^${name}( \\S)?$`) })

async function hover(page: Page, l: Locator) {
  const b = (await l.boundingBox())!
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 3 })
}

/** A common layout shape: Inbox, two claiming label tabs, a claiming Promotions tab, and a
 *  tab defined by a search (which labels cannot satisfy). */
async function layout(page: Page, seeded: Seed) {
  const csrf = (await (await page.request.get('/api/v1/me')).json()).csrf
  const label = (name: string) => ({ name, icon: 'tag', claims: true, sections: [{ title: name, base: `label:${seeded.labels[name]}` }] })
  const r = await page.request.put('/api/v1/layouts', { headers: csrf ? { 'X-CSRF-Token': csrf } : {}, data: { active: 'mine', layouts: [{ name: 'Mine', tabs: [
    { name: 'Inbox', icon: 'inbox', sections: [{ title: 'Inbox', base: 'inbox' }] },
    label('E2E Label'),
    label('E2E Other'),
    { name: 'Promotions', icon: 'tag', claims: true, sections: [{ title: 'Promotions', base: 'inbox', category: 'promotions' }] },
    { name: 'Boss', icon: 'star', sections: [{ title: 'Boss', base: 'inbox', q: 'from:boss@example.org' }] },
  ] }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

async function open(page: Page, seeded: Seed, theme: Theme) {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  await layout(page, seeded)
  await page.goto('/mail/inbox')
  await expect(row(page, SUBJECT)).toBeVisible()
}

async function rowMenu(page: Page, subject: string) {
  // Scroll first and let a smooth scroll finish: a scroll closes an open menu.
  await row(page, subject).scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  await row(page, subject).click({ button: 'right', position: { x: 200, y: 10 } })
  await expect(menu(page)).toBeVisible()
}

const category = async (page: Page) => ((await conv(page.request, SUBJECT)) as unknown as { category: string | null }).category

for (const theme of THEMES) {
  test(`${theme}: Show in tab lists the layout's tabs with one check, moves the row, and undoes (mail.show_in_tab)`, async ({ page, seeded }) => {
    await open(page, seeded, theme)
    const labelId = seeded.labels['E2E Label']

    await rowMenu(page, SUBJECT)
    // "Move to folder" (v) says what it does.
    await hover(page, trigger(menu(page), 'Move to folder'))
    await expect(sub(page, 'Move to folder').getByText('Removes from Inbox')).toBeVisible()
    await hover(page, trigger(menu(page), 'Show in tab'))
    const tabs = sub(page, 'Show in tab')
    await expect(tabs.getByRole('menuitemradio')).toHaveText(['Inbox', 'E2E Label', 'E2E Other', 'Promotions', 'Boss'])
    await expect(tabs.locator('[aria-checked="true"]')).toHaveCount(1)
    await expect(tabs.getByRole('menuitemradio', { name: 'Inbox' })).toHaveAttribute('aria-checked', 'true')
    // A tab defined by a search: greyed out, and it says why.
    const boss = tabs.getByRole('menuitemradio', { name: 'Boss' })
    await expect(boss).toHaveAttribute('aria-disabled', 'true')
    await expect(boss).toHaveAttribute('data-tip', /search/)
    await page.waitForTimeout(200)
    await page.screenshot({ path: `${SHOTS}/${theme}-show-in-tab.png` })

    await tabs.getByRole('menuitemradio', { name: 'E2E Label' }).click()
    await expect(page.getByText('Shown in E2E Label.')).toBeVisible()
    await expect(row(page, SUBJECT)).toHaveCount(0)
    await expect.poll(async () => (await conv(page.request, SUBJECT)).label_ids).toContain(labelId)
    await page.getByRole('button', { name: 'Undo' }).last().click()
    await expect.poll(async () => (await conv(page.request, SUBJECT)).label_ids).not.toContain(labelId)
    await expect(row(page, SUBJECT)).toBeVisible()

    // Again, and follow it into its tab: the check moved there, and it is still the only one.
    await rowMenu(page, SUBJECT)
    await hover(page, trigger(menu(page), 'Show in tab'))
    await sub(page, 'Show in tab').getByRole('menuitemradio', { name: 'E2E Label' }).click()
    await expect(row(page, SUBJECT)).toHaveCount(0)
    await page.getByRole('tab', { name: /E2E Label/ }).click()
    await expect(row(page, SUBJECT)).toBeVisible()
    await rowMenu(page, SUBJECT)
    await hover(page, trigger(menu(page), 'Show in tab'))
    await expect(sub(page, 'Show in tab').locator('[aria-checked="true"]')).toHaveText(['E2E Label'])
    // The Inbox tab takes it back, and drops the label that would claim it.
    await sub(page, 'Show in tab').getByRole('menuitemradio', { name: 'Inbox' }).click()
    await expect.poll(async () => (await conv(page.request, SUBJECT)).label_ids).not.toContain(labelId)
  })

  test(`${theme}: the Category submenu under More sets the raw category, with Undo (mail.category)`, async ({ page, seeded }) => {
    await open(page, seeded, theme)
    const before = await category(page)
    await rowMenu(page, SUBJECT)
    await hover(page, trigger(menu(page), 'More'))
    await hover(page, trigger(sub(page, 'More'), 'Category'))
    const cats = sub(page, 'Category')
    await expect(cats.getByRole('menuitemradio')).toHaveText(['Primary', 'Promotions', 'Social', 'Updates', 'Forums', 'Purchases'])
    await expect(cats.locator('[aria-checked="true"]')).toHaveText(['Primary'])
    await page.waitForTimeout(200)
    await page.screenshot({ path: `${SHOTS}/${theme}-category.png` })
    await cats.getByRole('menuitemradio', { name: 'Social' }).click()
    await expect.poll(() => category(page)).toBe('social')
    await expect(page.getByText('Category set to Social.')).toBeVisible()
    // Social is not a tab of this layout: the conversation stays in the Inbox tab.
    await expect(row(page, SUBJECT)).toBeVisible()
    await page.getByRole('button', { name: 'Undo' }).last().click()
    await expect.poll(() => category(page)).toBe(before)
  })
}

test('the reader menu and the command palette offer the same choices (web.reader, web.palette)', async ({ page, seeded }) => {
  await open(page, seeded, 'classic')
  await row(page, SUBJECT).click()
  await expect(page.getByRole('heading', { name: SUBJECT })).toBeVisible()
  const bar = page.getByRole('toolbar', { name: 'Conversation actions' })
  await expect(bar.getByRole('button', { name: 'Move to folder' }).first()).toBeVisible()
  await bar.getByRole('button', { name: 'More', exact: true }).click()
  await hover(page, page.getByRole('menuitem', { name: 'Show in tab' }))
  const tabs = sub(page, 'Show in tab')
  await expect(tabs.locator('[aria-checked="true"]')).toHaveText(['Inbox'])
  await page.waitForTimeout(200)
  await page.screenshot({ path: `${SHOTS}/reader-show-in-tab.png` })
  await tabs.getByRole('menuitemradio', { name: 'Promotions' }).click()
  await expect.poll(() => category(page)).toBe('promotions')
  await bar.getByRole('button', { name: 'More', exact: true }).click()
  await hover(page, page.getByRole('menuitem', { name: 'Category' }))
  await expect(sub(page, 'Category').locator('[aria-checked="true"]')).toHaveText(['Promotions'])
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')

  // The palette: one item per tab and per category.
  await page.keyboard.press('Control+k')
  const input = page.getByRole('combobox').last()
  await input.fill('show in tab')
  await expect(page.getByRole('option', { name: /Show in tab: Promotions/ })).toContainText('shows here now')
  await page.getByRole('option', { name: /Show in tab: Inbox/ }).click()
  await expect.poll(() => category(page)).toBe('primary')
})
