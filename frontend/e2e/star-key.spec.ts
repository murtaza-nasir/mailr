// ACT-SURFACES: web.reader
// The s key in an open conversation toggles the star every time (the
// second press once did nothing, because the key read the list's copy of the star, which does not change
// while the list is hidden). Covers: mail.star, mail.unstar.
import { conv, expect, test } from './fixtures'

test('s stars, unstars and stars again in an open conversation', async ({ page }) => {
  await page.goto('/mail/inbox')
  const row = page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Read me' }).first()
  await row.click()
  await expect(page.getByRole('heading', { name: 'E2E Read me' }).first()).toBeVisible()
  const before = (await conv(page.request, 'E2E Read me')).starred
  for (const want of [!before, before, !before]) {
    await page.keyboard.press('s')
    await expect.poll(async () => (await conv(page.request, 'E2E Read me')).starred).toBe(want)
  }
})
