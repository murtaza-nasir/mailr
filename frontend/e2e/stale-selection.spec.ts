// A conversation that leaves the list leaves the selection too (3 Oct 2026): selected, opened
// and deleted from the reader, it stayed selected out of sight, and Archive (the toolbar, the
// e key) later archived it again, from Trash, instead of the conversation in front of the user.
// The row buttons, the toolbar and the keys must only ever act on what the list shows.
// Also: Cards bundles stay out of a one-section layout tab with card_bundles "flat".
// Covers: mail.archive, mail.delete, mail.star, mail.snooze (row buttons and keys after a removal).
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

test.use({ bulk: 6 })

const rows = (page: Page) => page.locator('[data-mail-list] [role="row"]')
const row = (page: Page, s: string) => rows(page).filter({ hasText: s }).first()

/** Every conversation action request the page sends, as parsed bodies. */
function recordActions(page: Page) {
  const sent: { ids: number[]; action: string }[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/conversations/actions')) sent.push(JSON.parse(r.postData() ?? '{}'))
  })
  return sent
}

async function setup(page: Page, theme: string, pane: string) {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  const csrf = (await (await page.request.get('/api/v1/me')).json()).csrf
  const r = await page.request.patch('/api/v1/me/settings', {
    headers: csrf ? { 'X-CSRF-Token': csrf } : {},
    data: { reading_pane: pane, reading_pane_by_theme: { [theme]: pane } },
  })
  expect(r.ok()).toBeTruthy()
}

// Cards beside a reading pane, Classic with the pane below (its narrow list beside a pane hides the row buttons).
for (const [theme, pane] of [['cards', 'right'], ['classic', 'below']]) {
  test(`${theme}: a conversation deleted from the reader leaves the selection; Archive acts on the row`, async ({ page }) => {
    await setup(page, theme, pane)
    const sent = recordActions(page)
    await page.goto('/mail/inbox')
    await expect(row(page, 'E2E Bulk 5')).toBeVisible()
    const a = Number(await row(page, 'E2E Bulk 5').getAttribute('data-id'))
    const b = Number(await row(page, 'E2E Bulk 2').getAttribute('data-id'))
    const c = Number(await row(page, 'E2E Bulk 1').getAttribute('data-id'))

    // Select A, open it, delete it from the reader.
    await row(page, 'E2E Bulk 5').hover()
    await row(page, 'E2E Bulk 5').locator('[aria-label="Select"]').first().click()
    await row(page, 'E2E Bulk 5').click({ position: { x: 300, y: 10 } })
    await expect(page.getByRole('heading', { name: 'E2E Bulk 5' })).toBeVisible()
    await page.locator('.conv-view').getByRole('button', { name: 'Delete', exact: true }).first().click()
    await expect(row(page, 'E2E Bulk 5')).toHaveCount(0)
    await expect.poll(() => sent.some((x) => x.action === 'trash' && x.ids.includes(a))).toBe(true)

    // Nothing is selected any more: the toolbar is back to its plain form.
    const toolbar = page.locator('[data-mail-list]').getByRole('toolbar', { name: 'Conversation actions' })
    await expect(toolbar.getByRole('button', { name: 'Archive' })).toHaveCount(0)

    // Archive from B's hover button archives B.
    await row(page, 'E2E Bulk 2').hover()
    await row(page, 'E2E Bulk 2').getByRole('button', { name: 'Archive' }).click()
    await expect.poll(() => sent.filter((x) => x.action === 'archive').map((x) => x.ids)).toEqual([[b]])

    // The e key archives the open conversation C, not A.
    await row(page, 'E2E Bulk 1').click({ position: { x: 300, y: 10 } })
    await expect(page.getByRole('heading', { name: 'E2E Bulk 1' })).toBeVisible()
    await page.locator('body').press('e')
    await expect.poll(() => sent.filter((x) => x.action === 'archive').map((x) => x.ids)).toEqual([[b], [c]])

    // A conversation in Trash is never acted on again.
    expect(sent.filter((x) => x.action !== 'trash' && x.ids.includes(a))).toEqual(
      sent.filter((x) => x.action === 'read' && x.ids.includes(a)))
  })
}

test('cards: a selected conversation removed elsewhere leaves the selection', async ({ page, browser, seeded }) => {
  await setup(page, 'cards', 'right')
  const sent = recordActions(page)
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Bulk 4')).toBeVisible()
  const a = Number(await row(page, 'E2E Bulk 4').getAttribute('data-id'))
  await row(page, 'E2E Bulk 4').hover()
  await row(page, 'E2E Bulk 4').locator('[aria-label="Select"]').first().click()
  const toolbar = page.locator('[data-mail-list]').getByRole('toolbar', { name: 'Conversation actions' })
  await expect(toolbar.getByRole('button', { name: 'Archive' })).toHaveCount(1)

  // Another device deletes it.
  const other = await browser.newContext({ baseURL: process.env.E2E_BASE ?? 'http://localhost:5175' })
  const r = await other.request.post('/api/v1/auth/login', { data: { email: seeded.email, password: seeded.password } })
  const csrf = (await r.json()).csrf
  expect((await other.request.post('/api/v1/conversations/actions', { data: { ids: [a], action: 'trash' }, headers: { 'X-CSRF-Token': csrf } })).ok()).toBeTruthy()
  await other.close()
  // The live stream takes it out of the list.
  await expect(row(page, 'E2E Bulk 4')).toHaveCount(0)
  await expect(toolbar.getByRole('button', { name: 'Archive' })).toHaveCount(0)
  await page.locator('body').press('e')
  await page.waitForTimeout(500)
  expect(sent.filter((x) => x.ids.includes(a))).toEqual([])
})

test('cards: a one-section layout tab shows no bundles with "flat"; "always" bundles it', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'cards'))
  const csrf = (await (await page.request.get('/api/v1/me')).json()).csrf
  const h: Record<string, string> = csrf ? { 'X-CSRF-Token': String(csrf) } : {}
  const lr = await page.request.put('/api/v1/layouts', { headers: h, data: { active: 'later', layouts: [{ name: 'Later', tabs: [
    { name: 'Inbox', icon: 'inbox', sections: [{ title: 'Inbox', base: 'inbox' }] },
    { name: 'Later', icon: 'clock', sections: [{ title: 'Later', base: 'inbox', q: 'subject:"E2E Bulk"' }] },
  ] }] } })
  expect(lr.ok(), await lr.text()).toBeTruthy()
  const bundle = page.locator('[data-mail-list] .c-group, [data-mail-list] .c-bundle')
  for (const [mode, shown] of [['flat', false], ['always', true]] as const) {
    expect((await page.request.patch('/api/v1/me/settings', { headers: h, data: { card_bundles: mode } })).ok()).toBeTruthy()
    await page.goto('/mail/inbox')
    await page.getByRole('tab', { name: /Later/ }).click()
    await expect(row(page, 'E2E Bulk 1')).toBeVisible()
    if (shown) await expect(bundle.first()).toBeVisible()
    else await expect(bundle).toHaveCount(0)
  }
})
