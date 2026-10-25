// ACT-SURFACES: web.reader
// mail.translate on web.reader: "Translate to English" from the message menu shows the
// translation in place of the body, with "Show original". The model call is stubbed here (the
// endpoint's own behaviour is covered by backend/tests/test_translate.py).
import { expect, test } from './fixtures'

test('mail.translate: translate a message, then show the original', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/messages/*/translate', async (r) => {
    calls++
    expect(r.request().postDataJSON()).toEqual({ to: 'English' })
    await r.fulfill({ json: { status: 'ready', source: 'Spanish', to: 'English', text: 'The **invoice** is attached.', truncated: false } })
  })
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Reply to me' }).first().click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
  await page.getByRole('button', { name: 'More', exact: true }).last().click()
  await page.getByRole('menuitem', { name: 'Translate to English' }).click()
  await expect(page.locator('.translate-bar')).toContainText('Translated from Spanish to English')
  await expect(page.locator('.translated-text strong')).toHaveText('invoice')
  await page.getByRole('button', { name: 'Show original' }).click()
  await expect(page.locator('.translated-text')).toHaveCount(0)
  await page.getByRole('button', { name: 'Show translation' }).click()
  await expect(page.locator('.translated-text')).toContainText('is attached')
  expect(calls).toBe(1)
})
