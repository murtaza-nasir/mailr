// ACT-SURFACES: web.list web.settings
// Label sections in an Inbox tab (layout.inbox_only): a
// "Financial" section on a label with "Only mail in the Inbox" drops a row once it is archived.
// Row actions follow the row (mail.archive, mail.move): a row that is not in the Inbox offers
// Move to Inbox where Archive sits, in the row's buttons (Classic, Cards, Focus, Desk), its right-click
// menu and the Office ribbon. The layout editor shows the checkbox on label sections.
// Screenshots go to E2E_SHOTS (default: <tmp>/mailr-inbox-only-shots).
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { conv, expect, roles, test, type Seed } from './fixtures'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-inbox-only-shots')
const KEEP = 'E2E Archive me'
const GONE = 'E2E Delete me'

const section = (page: Page, title: string) => page.getByRole('region', { name: title, exact: true })
const row = (page: Page, title: string, subject: string) => section(page, title).getByRole('row').filter({ hasText: subject }).first()

async function csrf(page: Page): Promise<Record<string, string>> {
  const t = (await (await page.request.get('/api/v1/me')).json()).csrf
  return t ? { 'X-CSRF-Token': t } : {}
}

async function act(page: Page, subject: string, data: Record<string, unknown>) {
  const id = (await conv(page.request, subject)).id
  const r = await page.request.post('/api/v1/conversations/actions', { headers: await csrf(page), data: { ids: [id], ...data } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

/** One Inbox tab: the "Financial" section on the E2E Label label, then everything else. */
async function setUp(page: Page, seeded: Seed, inboxOnly: boolean, theme = 'classic') {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  const label = seeded.labels['E2E Label']
  for (const s of [KEEP, GONE]) await act(page, s, { action: 'add_labels', label_ids: [label] })
  const r = await page.request.put('/api/v1/layouts', { headers: await csrf(page), data: { active: 'page', layouts: [{ name: 'Page', tabs: [
    { name: 'Inbox', icon: 'inbox', sections: [
      { title: 'Financial', base: `label:${label}`, limit: 10, inbox_only: inboxOnly },
      { title: 'Everything else', base: 'inbox', limit: null, exclude_above: true },
    ] },
    { name: 'Later', icon: 'clock', sections: [{ title: 'Later', base: `label:${seeded.labels['E2E Other']}` }] },
  ] }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

test('archiving a row of an inbox-only label section takes it out of the section (layout.inbox_only)', async ({ page, seeded }) => {
  await setUp(page, seeded, true)
  await page.goto('/mail/inbox')
  await expect(row(page, 'Financial', KEEP)).toBeVisible()
  await row(page, 'Financial', KEEP).hover()
  await row(page, 'Financial', KEEP).getByRole('button', { name: 'Archive', exact: true }).click()
  await expect(row(page, 'Financial', KEEP)).toHaveCount(0)
  await expect(row(page, 'Everything else', KEEP)).toHaveCount(0)
  const inbox = (await roles(page.request)).inbox
  await expect.poll(async () => (await conv(page.request, KEEP)).label_ids).not.toContain(inbox)
  // Archived elsewhere, it is not listed here either.
  await act(page, GONE, { action: 'archive' })
  await page.reload()
  await expect(section(page, 'Financial')).toHaveCount(0) // an empty section is not drawn
  await expect(page.getByRole('row').filter({ hasText: GONE })).toHaveCount(0)
})

for (const theme of ['classic', 'cards', 'focus', 'desk'] as const) {
  test(`${theme}: a row not in the Inbox offers Move to Inbox, not Archive (mail.move)`, async ({ page, seeded }) => {
    await setUp(page, seeded, false, theme)
    await act(page, GONE, { action: 'archive' })
    await page.goto('/mail/inbox')
    const archived = row(page, 'Financial', GONE)
    const inInbox = row(page, 'Financial', KEEP)
    await expect(archived).toBeVisible()
    await inInbox.hover()
    await expect(inInbox.getByRole('button', { name: 'Archive', exact: true })).toBeVisible()
    await archived.hover()
    await expect(archived.getByRole('button', { name: 'Archive', exact: true })).toHaveCount(0)
    await expect(archived.getByRole('button', { name: 'Move to Inbox' })).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/${theme}-move-to-inbox.png` })
    // The right-click menu says the same.
    await archived.click({ button: 'right', position: { x: 200, y: 10 } })
    const menu = page.getByRole('menu', { name: 'Conversation actions' })
    await expect(menu.getByRole('menuitem', { name: 'Move to Inbox' })).toBeVisible()
    await expect(menu.getByRole('menuitem', { name: /^Archive/ })).toHaveCount(0)
    await page.keyboard.press('Escape')
    // Moving it back gives it Archive again.
    await archived.hover()
    await archived.getByRole('button', { name: 'Move to Inbox' }).click()
    const inbox = (await roles(page.request)).inbox
    await expect.poll(async () => (await conv(page.request, GONE)).label_ids).toContain(inbox)
    await archived.hover()
    await expect(archived.getByRole('button', { name: 'Archive', exact: true })).toBeVisible()
  })
}

test('office: the ribbon offers Move to Inbox for an archived row', async ({ page, seeded }) => {
  await setUp(page, seeded, false, 'office')
  await act(page, GONE, { action: 'archive' })
  await page.goto('/mail/inbox')
  await row(page, 'Financial', GONE).click()
  await expect(page.getByRole('button', { name: 'Move to Inbox' }).first()).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/office-ribbon.png` })
})

test('the layout editor shows "Only mail in the Inbox" on label sections (layout.inbox_only)', async ({ page, seeded }) => {
  await setUp(page, seeded, true)
  await page.goto('/settings/inbox')
  await page.getByRole('button', { name: 'Edit Page' }).click()
  const box = page.getByRole('checkbox', { name: 'Only mail in the Inbox' })
  // The Financial section has it (checked); the Inbox section does not.
  await expect(box).toHaveCount(1)
  await expect(box).toBeChecked()
  await page.screenshot({ path: `${SHOTS}/editor.png` })
  await box.uncheck()
  await page.getByRole('button', { name: 'Save layout' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const lays = await (await page.request.get('/api/v1/layouts')).json()
  const sec = lays.layouts.find((l: { id: string }) => l.id === 'page').tabs[0].sections[0]
  expect(sec.inbox_only).toBe(false)
})

test('the e key on a row outside the Inbox moves it back to the Inbox (mail.move)', async ({ page, seeded }) => {
  await setUp(page, seeded, false)
  await act(page, GONE, { action: 'archive' })
  await page.goto('/mail/inbox')
  const archived = row(page, 'Financial', GONE)
  // Open it, then press e (the shortcut acts on the open conversation).
  await archived.click({ position: { x: 400, y: 10 } })
  await expect(page.getByRole('heading', { name: GONE })).toBeVisible()
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('e')
  const inbox = (await roles(page.request)).inbox
  await expect.poll(async () => (await conv(page.request, GONE)).label_ids).toContain(inbox)
  await expect(page.getByText('Nothing to archive')).toHaveCount(0)
  // The help lists it.
  await page.keyboard.press('Escape')
  await page.keyboard.press('?')
  await expect(page.getByText('Archive (Move to Inbox for mail not in the Inbox)')).toBeVisible()
})
