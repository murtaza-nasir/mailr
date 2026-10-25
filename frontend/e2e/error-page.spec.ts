// The page shown when the first session check fails: it showed
// the server's own text ("Can't reach Mailr / down Retrying."). It now says what happened in
// Mailr's words and keeps the server's text under Details.
// Covers: web.errors.
import { expect, test } from './fixtures'

test('a 503 before the app loads: fixed words, the server text under Details', async ({ page }) => {
  await page.route('**/api/v1/auth/session', (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' }))
  await page.goto('/mail/inbox')
  await expect(page.getByRole('heading', { name: "Can't reach Mailr" })).toBeVisible()
  const text = page.locator('.empty > p')
  await expect(text).toHaveText("Mailr's server had a problem. Retrying.")
  const details = page.locator('.empty .error-details')
  await expect(details.locator('code')).toBeHidden()
  await details.locator('summary').click()
  await expect(details.locator('code')).toHaveText(/down/)
})
