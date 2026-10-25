// The web app's language (Settings → You → Region): the setting saves at once
// and reloads into the new words, Undo puts the old ones back, <html lang> follows; and the
// pseudo-language sweep of all five themes on the desktop (the phone: e2e/phone/i18n-phone.spec.ts).
// ACT-SURFACES: web.settings
import { expect, test } from './fixtures'
import { pseudoPage, sweepScreen, THEMES } from './i18n/sweep'

test('Region → Language: the pseudo-language reloads every word, Undo brings English back (settings.region)', async ({ page }) => {
  await page.goto('/settings/you/region')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  const select = page.getByTestId('language-select')
  await expect(select).toBeVisible()
  await expect(select.locator('option').first()).toHaveText('Automatic (English)')
  // Development offers the pseudo-language; choosing it saves and reloads.
  await select.selectOption('en-XA')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en-XA')
  await expect(page.getByRole('heading', { name: /^\[Ŕéĝíóñ •+\]$/ }).first()).toBeVisible()
  expect((await (await page.request.get('/api/v1/me')).json()).settings.language).toBe('en-XA')
  // The notice after the reload still offers Undo.
  const toast = page.locator('.toast').filter({ hasText: /Ļáñĝúáĝé/ })
  await expect(toast).toBeVisible()
  await toast.locator('.toast-act').click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('heading', { name: 'Region' }).first()).toBeVisible()
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.language ?? null).toBe(null)
})

test('a language saved on another device is used here after the next load', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  expect((await page.request.patch('/api/v1/me/settings', { data: { language: 'en-XA' } })).ok()).toBeTruthy()
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en-XA')
})

for (const theme of THEMES) {
  test(`pseudo-language sweep, ${theme}, desktop`, async ({ page }, info) => {
    await pseudoPage(page, theme)
    await page.goto('/mail/inbox')
    await expect(page.locator('[data-mail-list] [data-id]').first()).toBeVisible()
    await sweepScreen(page, info, `${theme}-inbox`)
    await page.locator('[data-mail-list] [data-id]').first().click()
    await sweepScreen(page, info, `${theme}-reader`)
    await page.goto('/settings/you/region')
    await sweepScreen(page, info, `${theme}-settings`)
    await page.goto('/calendar')
    await sweepScreen(page, info, `${theme}-calendar`)
    await page.goto('/tasks')
    await sweepScreen(page, info, `${theme}-tasks`)
    await page.goto('/notes')
    await sweepScreen(page, info, `${theme}-notes`)
  })
}
