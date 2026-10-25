// Send with a recipient that is not an address: words typed in To
// ("hecking in") become a red chip, and Ctrl + Enter did nothing that the user could see. Now the
// send says which entry is not an address, under the address fields, and puts the focus on it.
// The message replaces the one before and goes when the recipients change (toasts piled up).
// Covers: web.compose.
import { expect, test } from './fixtures'

test('Ctrl + Enter with an invalid chip says why and focuses the chip', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('c')
  const win = page.locator('.compose-win')
  await win.getByLabel('To recipients').fill('hecking in')
  await page.keyboard.press('Tab')
  await expect(win.locator('[data-testid="recip-chip"].invalid')).toHaveCount(1)
  await win.getByLabel('Subject').fill('Checking in')
  await page.keyboard.press('Tab')
  await page.keyboard.type('Hello')
  await page.keyboard.press('Control+Enter')
  const err = win.getByTestId('compose-recipient-error')
  await expect(err).toHaveText('"hecking in" in To is not an email address.')
  await expect(win.locator('[data-testid="recip-chip"].invalid button')).toBeFocused()
  // One warning, not one per handler of the key, and none as a toast.
  await page.keyboard.press('Control+Enter')
  await expect(err).toHaveCount(1)
  expect(await page.locator('.toast.error').count()).toBe(0)
  await expect(win).toHaveCount(1)
  // Removing the chip clears it.
  await win.locator('[data-testid="recip-chip"].invalid button').click()
  await expect(err).toHaveCount(0)
})
