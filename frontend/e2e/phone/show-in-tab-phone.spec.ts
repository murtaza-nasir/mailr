// ACT-SURFACES: web.list
// Show in tab (mail.show_in_tab) and Category (mail.category) on a phone: press and hold a row, the
// menu is a bottom sheet, and each submenu replaces it in place under Back. Classic, Cards, Desk
// and Workspace: one check, picking a tab moves the row into that tab, Undo, the Category submenu
// under More, and "Move to folder" with its hint.
// Screenshots go to E2E_SHOTS (default: <tmp>/mailr-show-in-tab-shots).
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { conv, expect, test, type Seed } from '../fixtures'
import { longPress, row, settled } from './phone'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-show-in-tab-shots')
const SUBJECT = 'E2E Reply to me'
const sheet = (page: Page) => page.locator('.popover.as-sheet')

async function layout(page: Page, seeded: Seed) {
  const csrf = (await (await page.request.get('/api/v1/me')).json()).csrf
  const label = (name: string) => ({ name, icon: 'tag', claims: true, sections: [{ title: name, base: `label:${seeded.labels[name]}` }] })
  const r = await page.request.put('/api/v1/layouts', { headers: csrf ? { 'X-CSRF-Token': csrf } : {}, data: { active: 'mine', layouts: [{ name: 'Mine', tabs: [
    { name: 'Inbox', icon: 'inbox', sections: [{ title: 'Inbox', base: 'inbox' }] },
    label('E2E Label'),
    { name: 'Promotions', icon: 'tag', claims: true, sections: [{ title: 'Promotions', base: 'inbox', category: 'promotions' }] },
  ] }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

async function hold(page: Page) {
  await row(page, SUBJECT).scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  await longPress(row(page, SUBJECT))
  await expect(sheet(page)).toBeVisible()
  await settled(sheet(page))
}

const item = (page: Page, name: string) => sheet(page).getByRole('menuitem', { name, exact: true })
const category = async (page: Page) => ((await conv(page.request, SUBJECT)) as unknown as { category: string | null }).category

for (const theme of ['classic', 'cards', 'desk', 'office'] as const) {
  test(`${theme} on a phone: Show in tab and Category in the bottom sheet`, async ({ page, seeded }) => {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await layout(page, seeded)
    await page.goto('/mail/inbox')
    await expect(row(page, SUBJECT)).toBeVisible()
    const labelId = seeded.labels['E2E Label']

    await hold(page)
    await item(page, 'Move to folder').click()
    await expect(sheet(page).getByText('Removes from Inbox')).toBeVisible()
    await item(page, 'Back').click()
    await item(page, 'Show in tab').click()
    const radios = sheet(page).getByRole('menuitemradio')
    await expect(radios).toHaveText(['Inbox', 'E2E Label', 'Promotions'])
    await expect(sheet(page).locator('[aria-checked="true"]')).toHaveText(['Inbox'])
    await page.waitForTimeout(250)
    await page.screenshot({ path: `${SHOTS}/phone-${theme}-show-in-tab.png` })
    await sheet(page).getByRole('menuitemradio', { name: 'E2E Label' }).click()
    await expect.poll(async () => (await conv(page.request, SUBJECT)).label_ids).toContain(labelId)
    await expect(row(page, SUBJECT)).toHaveCount(0)
    await page.getByRole('button', { name: 'Undo' }).last().click()
    await expect.poll(async () => (await conv(page.request, SUBJECT)).label_ids).not.toContain(labelId)
    await expect(row(page, SUBJECT)).toBeVisible()

    // The raw category, under More.
    await hold(page)
    await item(page, 'More').click()
    await item(page, 'Category').click()
    await expect(sheet(page).locator('[aria-checked="true"]')).toHaveText(['Primary'])
    await page.waitForTimeout(250)
    await page.screenshot({ path: `${SHOTS}/phone-${theme}-category.png` })
    await sheet(page).getByRole('menuitemradio', { name: 'Promotions' }).click()
    await expect.poll(() => category(page)).toBe('promotions')
    // Promotions is a tab here: the row moves into it.
    await expect(row(page, SUBJECT)).toHaveCount(0)
    const where = await page.request.get('/api/v1/layouts/show-in-tab', { params: { conversation_ids: String(seeded.conversations[SUBJECT]) } })
    expect((await where.json()).current).toBe('promotions')
  })
}
