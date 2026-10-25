// What archive and Undo show.
// - In an Inbox tab, a label section with "Only mail in the Inbox" off lists archived mail too,
//   so a row archived there stays. Its head says "All mail", and the toast says where the row
//   still shows.
// - Undo (the toast's button or z) replaces the toast it came from: no stale Undo remains.
// Covers: web.list, web.shortcuts.
import type { Page } from '@playwright/test'
import { conv, expect, test, type Seed } from './fixtures'

const KEEP = 'E2E Archive me'
const section = (page: Page, title: string) => page.getByRole('region', { name: title, exact: true })

async function setUp(page: Page, seeded: Seed) {
  const label = seeded.labels['E2E Label']
  const id = (await conv(page.request, KEEP)).id
  let r = await page.request.post('/api/v1/conversations/actions', { data: { ids: [id], action: 'add_labels', label_ids: [label] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  r = await page.request.put('/api/v1/layouts', { data: { active: 'page', layouts: [{ name: 'Page', tabs: [
    { name: 'Inbox', icon: 'inbox', sections: [
      { title: 'Financial', base: `label:${label}`, limit: 10, inbox_only: false },
      { title: 'Everything else', base: 'inbox', limit: null, exclude_above: true },
    ] },
  ] }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

test('archive in an all-mail label section: the head says All mail and the toast says where the row is', async ({ page, seeded }) => {
  await setUp(page, seeded)
  await page.goto('/mail/inbox')
  const fin = section(page, 'Financial')
  await expect(fin.getByTestId('section-all-mail')).toHaveText('All mail')
  await expect(section(page, 'Everything else').getByTestId('section-all-mail')).toHaveCount(0)
  const row = fin.getByRole('row').filter({ hasText: KEEP }).first()
  await row.hover()
  await row.getByRole('button', { name: 'Archive', exact: true }).click()
  await expect(page.locator('.toast').filter({ hasText: 'It is still listed in Financial' })).toBeVisible()
})

test('z replaces the archive toast with "Action undone"; no stale Undo stays', async ({ page }) => {
  await page.goto('/mail/inbox')
  const rows = page.locator('[data-mail-list] [role="row"]')
  await expect(rows.first()).toBeVisible()
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('j')
  await page.keyboard.press('e')
  const archived = page.locator('.toast').filter({ hasText: 'Conversation archived' })
  await expect(archived).toBeVisible()
  await page.keyboard.press('z')
  await expect(page.locator('.toast').filter({ hasText: 'Action undone' })).toBeVisible()
  // At once, not after the old toast's 10 s run out.
  expect(await archived.count()).toBe(0)
  expect(await page.locator('.toast-act', { hasText: 'Undo' }).count()).toBe(0)
})
