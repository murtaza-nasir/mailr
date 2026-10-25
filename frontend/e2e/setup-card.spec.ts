// The "Continue setting up Mailr" card.
// It used to read "0 of 8 steps done" on every page of a fully set-up account once the guide had
// been opened. Now it counts what the account has, shows only while no mail account is connected,
// only on mail pages, and stays one compact strip on a phone.
// Covers: web.setup.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const card = (page: Page) => page.getByTestId('setup-card')

test('a set-up account with the guide open shows no card on any page', async ({ page }) => {
  // The guide opened from Settings → You, then left at the second step: nothing marked.
  const r = await page.request.put('/api/v1/onboarding', { data: { status: 'active', step: 'look' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  await expect(card(page)).toHaveCount(0)
  await page.goto('/calendar')
  await expect(page.locator('main')).toBeVisible()
  await expect(card(page)).toHaveCount(0)
})

/** The account as if no mail account were connected yet (the session answer without identities). */
async function withoutAccounts(page: Page) {
  await page.route(/\/api\/v1\/auth\/session(\?|$)/, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    await route.fulfill({ response: res, json: { ...body, identities: [] } })
  })
}

test('without a mail account: the card on mail pages only, one strip on a phone', async ({ page }) => {
  await page.request.put('/api/v1/onboarding', { data: { status: 'active', step: 'accounts', mark: { welcome: 'done' } } })
  await withoutAccounts(page)
  await page.goto('/mail/inbox')
  await expect(card(page)).toContainText('1 of 8 steps done')
  await page.goto('/tasks')
  await expect(page.locator('main')).toBeVisible()
  await expect(card(page)).toHaveCount(0)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/mail/inbox')
  await expect(card(page)).toBeVisible()
  const box = (await card(page).boundingBox())!
  expect(box.height).toBeLessThanOrEqual(64)
  await expect(card(page).getByRole('link', { name: /Continue setup/ })).toBeVisible()
})
