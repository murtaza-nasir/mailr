// Approval links (web.reader): agents, MCP results and push notifications hand out
// <base>/approvals/<id>, the id in its public form (a + base62) with public ids on. The link opens the approvals drawer over the inbox, in every theme, with
// that approval scrolled to and outlined; a decided approval shows its outcome; an unknown or
// foreign id says so. /approvals alone opens the drawer.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { themes } from './matrix'

async function useTheme(page: Page, theme: string) {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
}

// The drawer and its scrolling are shared; nightly every theme, otherwise Classic and Cards.
for (const theme of themes({ release: ['classic', 'cards'] })) {
  test(`an approval link opens the drawer on that approval (${theme})`, async ({ page, seeded }) => {
    await useTheme(page, theme)
    const id = seeded.approvals['E2E Deny me']
    await page.goto(`/approvals/${id}`)
    await expect(page).toHaveURL(/\/mail\/inbox$/)
    const drawer = page.getByRole('region', { name: 'Approvals' })
    await expect(drawer).toBeVisible()
    const item = drawer.locator(`[data-item-id="${id}"]`)
    await expect(item).toHaveClass(/focused/)
    await expect(item.getByRole('group', { name: /E2E Deny me/ })).toBeInViewport()
    // Only the linked card is outlined.
    await expect(drawer.locator('.approvals-item.focused')).toHaveCount(1)
  })
}

test('a decided approval shows its outcome', async ({ page, seeded }) => {
  const id = seeded.approvals['E2E Deny me']
  const r = await page.request.post(`/api/v1/approvals/${id}/decide`, { data: { decision: 'deny' } })
  expect(r.ok()).toBeTruthy()
  await page.goto(`/approvals/${id}`)
  const drawer = page.getByRole('region', { name: 'Approvals' })
  const item = drawer.locator(`[data-item-id="${id}"]`)
  await expect(item).toHaveClass(/focused/)
  await expect(item.locator('.approvals-meta')).toContainText('Denied')
  await expect(item.getByRole('group', { name: /E2E Deny me/ })).toHaveAttribute('data-status', 'denied')
  // A decided card has no decision buttons.
  await expect(item.getByRole('button', { name: 'Allow once' })).toHaveCount(0)
})

test('an unknown approval says so', async ({ page }) => {
  await page.goto('/approvals/987654321')
  const drawer = page.getByRole('region', { name: 'Approvals' })
  await expect(drawer.getByTestId('approval-not-found')).toContainText('This approval was not found')
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await page.goto('/approvals/nonsense')
  await expect(page.getByTestId('approval-not-found')).toContainText('“nonsense” was not found')
})

test('/approvals opens the drawer with what is waiting', async ({ page, seeded }) => {
  await page.goto('/approvals')
  await expect(page).toHaveURL(/\/mail\/inbox$/)
  const drawer = page.getByRole('region', { name: 'Approvals' })
  await expect(drawer.getByRole('group', { name: /E2E Approve me/ })).toBeVisible()
  await expect(drawer.locator('.approvals-item.focused')).toHaveCount(0)
  expect(seeded.approvals['E2E Approve me']).toBeGreaterThan(0)
})

test('an approval link survives sign-in', async ({ browser, seeded }) => {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  const id = seeded.approvals['E2E Approve me']
  await page.goto(`/approvals/${id}`)
  await expect(page).toHaveURL(new RegExp(`/login\\?next=${encodeURIComponent(`/approvals/${id}`)}`))
  await ctx.close()
})

test('an approval link on a phone-sized window, where the badge is hidden', async ({ page, seeded }) => {
  await useTheme(page, 'focus')
  await page.setViewportSize({ width: 390, height: 844 })
  const id = seeded.approvals['E2E Approve me']
  await page.goto(`/approvals/${id}`)
  const item = page.getByRole('region', { name: 'Approvals' }).locator(`[data-item-id="${id}"]`)
  await expect(item).toHaveClass(/focused/)
  await expect(item.getByRole('button', { name: 'Allow once' })).toBeInViewport()
})

test('the link as handed out names a public id, never the sequential one', async ({ page, seeded }) => {
  const link = seeded.approval_links['E2E Deny me']
  const id = seeded.approvals['E2E Deny me']
  expect(link).toMatch(/^\/approvals\/a[0-9A-Za-z]{8,}$/)
  expect(link).not.toContain(String(seeded.approval_legacy_ids['E2E Deny me']))
  expect(id).toBeGreaterThanOrEqual(2 ** 52)
  // The neighbouring approval's id is nowhere near.
  expect(Math.abs(seeded.approvals['E2E Approve me'] - id)).toBeGreaterThan(1000)
  await page.goto(link)
  await expect(page).toHaveURL(/\/mail\/inbox$/)
  const item = page.getByRole('region', { name: 'Approvals' }).locator(`[data-item-id="${id}"]`)
  await expect(item).toHaveClass(/focused/)
  // The API answers to the public id; the sequential one only while the approval is pending.
  expect((await page.request.get(`/api/v1/approvals/${id}`)).ok()).toBeTruthy()
})

test('an old numeric link still opens a pending approval; once decided it is not found', async ({ page, seeded }) => {
  const old = seeded.approval_legacy_ids['E2E Approve me']
  const id = seeded.approvals['E2E Approve me']
  await page.goto(`/approvals/${old}`)
  const drawer = page.getByRole('region', { name: 'Approvals' })
  const item = drawer.locator(`[data-item-id="${id}"]`)
  await expect(item).toHaveClass(/focused/)
  await expect(drawer.locator('.approvals-item.focused')).toHaveCount(1)
  await expect(drawer.getByTestId('approval-not-found')).toHaveCount(0)
  // Decided: the numeric form no longer names it (MAILR_PUBLIC_IDS_LEGACY=false); the public id still does.
  const r = await page.request.post(`/api/v1/approvals/${id}/decide`, { data: { decision: 'deny' } })
  expect(r.ok()).toBeTruthy()
  const legacy = await page.request.get(`/api/v1/approvals/${old}`)
  const legacyAllowed = process.env.MAILR_PUBLIC_IDS_LEGACY !== 'false'
  expect(legacy.status()).toBe(legacyAllowed ? 200 : 404)
  expect((await page.request.get(`/api/v1/approvals/${id}`)).ok()).toBeTruthy()
})
