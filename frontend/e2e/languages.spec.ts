// The shipped languages: German and Simplified
// Chinese chosen in Settings → You → Region reload the app into their words, <html lang> follows,
// and the Language menu names every language in its own words. The expected words come from the
// locale files, so a hand fix there keeps the test green.
// ACT-SURFACES: web.settings
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { expect, REVIEW_SHOTS, test } from './fixtures'

/** With SWEEP_SHOTS=<dir>, each screen is written there to look through (cut-off labels); with
 *  E2E_SHOTS set it is attached to the report. */
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.SWEEP_SHOTS
  if (!dir && !REVIEW_SHOTS) return
  await page.waitForTimeout(400)
  if (dir) await page.screenshot({ path: path.join(dir, `lang-${name}.png`) })
  else await test.info().attach(name, { body: await page.screenshot(), contentType: 'image/png' })
}

type Tree = { [k: string]: string | Tree }
const locale = (code: string): Tree => JSON.parse(readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), '../src/locales', `${code}.json`), 'utf8')) as Tree
const de = locale('de')
const zh = locale('zh-Hans')
const text = (table: Tree, key: string): string => key.split('.').reduce<string | Tree>((t, k) => (t as Tree)[k], table) as string

test('Region → Language: German, then Chinese, then back to Automatic (settings.region)', async ({ page }) => {
  await page.goto('/settings/you/region')
  const select = page.getByTestId('language-select')
  await expect(select).toBeVisible()
  const names = await select.locator('option').allTextContents()
  for (const name of ['Deutsch', 'Español', 'Français', 'Português (Brasil)', 'Русский', '中文（简体）']) expect(names).toContain(name)

  for (const [code, table] of [['de', de], ['zh-Hans', zh]] as const) {
    await page.getByTestId('language-select').selectOption(code)
    await expect(page.locator('html')).toHaveAttribute('lang', code)
    await expect(page.getByRole('heading', { name: text(table, 'settings.groups.region') }).first()).toBeVisible()
    expect((await (await page.request.get('/api/v1/me')).json()).settings.language).toBe(code)
    await shot(page, `${code}-settings`)
    await page.goto('/mail/inbox')
    await expect(page.getByText(text(table, 'common.inbox'), { exact: true }).first()).toBeVisible()
    await expect(page.getByText(text(table, 'common.calendar'), { exact: true }).first()).toBeVisible()
    await shot(page, `${code}-inbox`)
    await page.locator('[data-mail-list] [data-id]').first().click()
    await shot(page, `${code}-reader`)
    await page.goto('/calendar')
    await shot(page, `${code}-calendar`)
    await page.goto('/tasks')
    await shot(page, `${code}-tasks`)
    await page.goto('/settings/you/region')
  }

  await page.getByTestId('language-select').selectOption('')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('heading', { name: 'Region' }).first()).toBeVisible()
})

for (const [locale, lang] of [['de-DE', 'de'], ['zh-CN', 'zh-Hans'], ['zh-TW', 'en']] as const) {
  test.describe(`a ${locale} browser on Automatic`, () => {
    test.use({ locale })
    test(`reads ${lang}`, async ({ page }) => {
      await page.goto('/mail/inbox')
      // The first visit in a language fetches its table, then reloads once.
      await expect(page.locator('html')).toHaveAttribute('lang', lang, { timeout: 15_000 })
    })
  })
}
