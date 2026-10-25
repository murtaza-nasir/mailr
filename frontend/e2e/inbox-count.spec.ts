// ACT-SURFACES: web.settings web.nav
// settings.inbox_count: Settings → Inbox → "Inbox count: Inbox tab only / All tabs". With "Inbox
// tab only" (the default) the number by Inbox in the sidebar is the unread mail the layout's
// first tab shows, the same number as that tab's own unread indicator; mail a claiming tab
// takes is left out. With "All tabs" it is every unread conversation in the Inbox. The layout
// editor marks the first tab "Counted in the sidebar" and does not delete it.
import { conv, expect, test, type Page } from './fixtures'

const TABS = [
  { name: 'Inbox', icon: 'inbox', indicators: ['unread'], sections: [{ title: 'All', base: 'inbox' }] },
  { name: 'Claimed', icon: 'tag', indicators: ['unread'], claims: true, sections: [{ title: 'All', base: 'inbox', q: 'subject:"E2E Reply to me"' }] },
]

async function setup(page: Page) {
  const r = await page.request.put('/api/v1/layouts', { data: { active: 'counted', layouts: [{ name: 'Counted', tabs: TABS }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const claimed = await conv(page.request, 'E2E Reply to me')
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [claimed.id], action: 'unread' } })
}

const inboxNav = (page: Page) =>
  page.locator('nav a.nav-item', { has: page.locator('.name', { hasText: /^Inbox$/ }) }).first()

async function labelUnread(page: Page) {
  const labels = (await (await page.request.get('/api/v1/labels')).json()) as { role: string | null; unread: number }[]
  return labels.find((l) => l.role === 'inbox')!.unread
}

test('the sidebar counts the first tab, or every tab with "All tabs"', async ({ page }) => {
  await setup(page)
  const first = await (await page.request.get('/api/v1/inbox/count')).json()
  expect(first).toMatchObject({ mode: 'first_tab', layout: 'counted', tab: 'inbox' })
  const all = await labelUnread(page)
  expect(first.count).toBeLessThan(all)

  await page.goto('/mail/inbox')
  // The first tab's own indicator is the same number.
  const indicator = page.getByRole('tab', { name: /^Inbox/ }).locator('[data-indicator="unread"]')
  if (first.count > 0) {
    await expect(inboxNav(page).locator('.count')).toHaveText(first.count.toLocaleString('en-US'))
    await expect(indicator).toContainText(String(first.count))
  } else {
    await expect(page.getByRole('tab', { name: /^Claimed/ }).locator('[data-indicator="unread"]')).toBeVisible()
    await expect(inboxNav(page).locator('.count')).toHaveCount(0)
    await expect(indicator).toHaveCount(0)
  }

  await page.goto('/settings/mail/inbox')
  const row = page.getByRole('radio', { name: 'Inbox tab only' })
  await expect(row).toBeChecked()
  await page.getByRole('radio', { name: 'All tabs' }).check()
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.inbox_count).toBe('all')
  expect((await (await page.request.get('/api/v1/inbox/count')).json()).count).toBe(all)

  await page.goto('/mail/inbox')
  await expect(inboxNav(page).locator('.count')).toHaveText(all.toLocaleString('en-US'))
})

test('the editor marks the first tab and does not delete it', async ({ page }) => {
  await setup(page)
  await page.goto('/settings/mail/inbox')
  await page.getByRole('button', { name: 'Edit Counted' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Counted in the sidebar')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Delete tab' })).toBeDisabled()
  await dialog.getByRole('tab', { name: 'Claimed' }).click()
  await expect(dialog.getByText('Counted in the sidebar')).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: 'Delete tab' })).toBeEnabled()
  // Moving it left makes it the first tab: then it is the counted one and cannot be deleted.
  await dialog.getByRole('button', { name: 'Move tab left' }).click()
  await expect(dialog.getByText('Counted in the sidebar')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Delete tab' })).toBeDisabled()
})
