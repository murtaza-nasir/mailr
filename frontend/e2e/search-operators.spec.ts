// Search suggestions after an operator: from: and to: offer people,
// label: the user's labels, in: the folders. A pick completes the word; Enter searches.
// Covers: web.search.
import { expect, test } from './fixtures'

test('from:, label: and in: offer people, labels and folders', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  const box = page.getByTestId('search-input')
  const panel = page.locator('.search-panel')
  await box.click()
  await box.fill('from:dan')
  await expect(panel.getByRole('option').first()).toContainText('Dana Tester')
  await panel.getByRole('option').first().click()
  await expect(box).toHaveValue(/^from:\S+@\S+ $/)
  await expect(box).toBeFocused()

  await box.fill('label:"E2E L')
  await expect(panel.getByRole('option', { name: 'label:"E2E Label"' })).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(box).toHaveValue('label:"E2E Label" ')

  await box.fill('in:sp')
  await expect(panel.getByRole('option', { name: 'in:spam' })).toBeVisible()
})
