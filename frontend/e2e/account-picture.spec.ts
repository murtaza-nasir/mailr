// ACT-SURFACES: web.settings
// Your picture (Settings → Accounts): upload (settings.picture_upload) shows in the top bar,
// remove (settings.picture_remove) goes back to initials. The provider's picture
// (settings.picture_use_sign_in) and the helpers (settings.picture_publish_check,
// settings.picture_bimi_check) are covered in backend/tests/test_account_picture.py. Here
// "Publish your picture" shows only the parts that match the user's sending addresses, and the
// BIMI checks only to admins, for domains that are not shared mailbox providers.
import type { Page } from '@playwright/test'
import { accessTest, expect, signIn, test } from './fixtures'

/** Replace the sending addresses that the picture section sees (GET /me/picture/providers). */
async function providers(page: Page, items: { identity_id: number; email: string; provider: 'google' | 'microsoft' | null; shared_domain: boolean }[]) {
  await page.route('**/api/v1/me/picture/providers', (r) =>
    r.fulfill({ json: { items: items.map((i) => ({ ...i, domain: i.email.split('@')[1] })) } }))
}

// A 2×2 red PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGO8o6HBwMDAxMDAwMDAAAAO7gEwnDTINgAAAABJRU5ErkJggg==',
  'base64',
)

test('the top bar shows the account picture', async ({ page }) => {
  await page.goto('/settings/you')
  await expect(page.getByRole('heading', { name: 'Your picture' })).toBeVisible()
  await expect(page.getByTestId('picture-source')).toContainText('initials')
  await page.getByLabel('Choose a picture').setInputFiles({ name: 'me.png', mimeType: 'image/png', buffer: PNG })
  await expect(page.getByTestId('picture-source')).toContainText('Uploaded')
  const topBarPic = page.locator('.account-btn .avatar img')
  await expect(topBarPic).toHaveAttribute('src', /\/api\/v1\/(me\/picture|identities\/\d+\/avatar)/)
  await expect.poll(() => topBarPic.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)

  await page.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(page.getByTestId('picture-source')).toContainText('initials')
  await expect(page.locator('.account-btn .avatar img')).toHaveCount(0)
})

test('a user with an own-domain address sees Gravatar and Libravatar, and no BIMI', async ({ page }) => {
  await page.goto('/settings/you')
  await page.getByText('Publish your picture').click()
  await expect(page.getByRole('heading', { name: 'Gravatar and Libravatar' })).toBeVisible()
  await expect(page.getByRole('link', { name: /Gravatar/ })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Check my addresses' })).toBeEnabled()
  await expect(page.getByRole('heading', { name: 'Google account photo' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'BIMI brand logo' })).toHaveCount(0)
  // The server refuses the BIMI check too (settings.picture_bimi_check is for admins).
  const ids = (await (await page.request.get('/api/v1/me/picture/providers')).json()).items as { identity_id: number }[]
  const r = await page.request.post('/api/v1/me/picture/bimi-check', { data: { identity_id: ids[0].identity_id } })
  expect(r.status()).toBe(403)
})

test('Gmail and Outlook addresses get their account photo steps only', async ({ page }) => {
  await providers(page, [
    { identity_id: 1, email: 'alex@gmail.com', provider: 'google', shared_domain: true },
    { identity_id: 2, email: 'alex@outlook.com', provider: 'microsoft', shared_domain: true },
  ])
  await page.goto('/settings/you')
  await page.getByText('Publish your picture').click()
  await expect(page.getByRole('heading', { name: 'Google account photo' })).toBeVisible()
  await expect(page.getByRole('link', { name: /Google Account/ })).toHaveAttribute('href', 'https://myaccount.google.com/personal-info')
  await expect(page.getByRole('heading', { name: 'Microsoft account photo' })).toBeVisible()
  await expect(page.getByRole('link', { name: /Microsoft account/ })).toHaveAttribute('href', 'https://account.microsoft.com/profile')
  await expect(page.getByRole('heading', { name: 'Gravatar and Libravatar' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'BIMI brand logo' })).toHaveCount(0)
})

accessTest('an admin gets BIMI checks for own domains only', async ({ page, access }) => {
  await signIn(page, access.admin)
  await providers(page, [
    { identity_id: 1, email: 'alex@gmail.com', provider: 'google', shared_domain: true },
    { identity_id: 2, email: 'alex@brand.example', provider: null, shared_domain: false },
  ])
  await page.goto('/settings/you')
  await page.getByText('Publish your picture').click()
  await expect(page.getByRole('heading', { name: 'BIMI brand logo' })).toBeVisible()
  const cards = page.locator('.picture-bimi')
  await expect(cards).toHaveCount(1)
  await expect(cards).toContainText('brand.example')
  await expect(cards.getByRole('button', { name: 'Check BIMI' })).toBeVisible()
})
