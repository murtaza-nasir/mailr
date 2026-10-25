// The user guide inside mailr at /help/ (backend core/helpdocs.py):
// signed out, it sends you to the sign-in page and back; signed in, the docs pages, their search
// and the Mermaid diagram work under the help pages' own policy (the cspGuard fixture fails the
// test on any refusal); the Help entries of the ? menu and of the account menu open it in a new tab.
// The pages come from backend/mailr/help, built by tools/build-help when missing.
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import type { BrowserContext } from '@playwright/test'
import { bareTest, expect, test, type Seed } from './fixtures'

const ROOT = path.resolve(import.meta.dirname, '../..')

// One worker for the file: the guide is built once, not by several workers at the same time.
test.describe.configure({ mode: 'default' })
test.beforeAll(() => {
  if (!fs.existsSync(path.join(ROOT, 'backend/mailr/help/index.html'))) {
    execFileSync(path.join(ROOT, 'tools/build-help'), { cwd: ROOT, stdio: 'inherit', timeout: 120_000 })
  }
})

async function opensHelp(context: BrowserContext, click: () => Promise<void>) {
  const [popup] = await Promise.all([context.waitForEvent('page'), click()])
  await popup.waitForLoadState()
  await expect(popup).toHaveURL(/\/help\/$/)
  await expect(popup.locator('.md-content h1').first()).toBeVisible()
  return popup
}

bareTest('signed out, /help sends you to sign in and back to the page', async ({ page, seeder }) => {
  const s = (await seeder({})) as Seed
  await page.goto('/help/user-guide/inbox/')
  await expect(page).toHaveURL(/\/login\?next=%2Fhelp%2Fuser-guide%2Finbox%2F$/)
  await expect(page.locator('.md-content')).toHaveCount(0)
  await page.locator('#login-email').fill(s.email)
  await page.locator('#login-password').fill(s.password)
  await page.locator('.login-submit').click()
  await expect(page).toHaveURL(/\/help\/user-guide\/inbox\/$/)
  await expect(page.getByRole('heading', { level: 1, name: 'The inbox' })).toBeVisible()
})

test('signed in, the docs render under their own policy', async ({ page }) => {
  const r = await page.request.get('/help/')
  expect(r.status()).toBe(200)
  const h = r.headers()
  expect(h['x-robots-tag']).toContain('noindex')
  expect(h['cache-control']).toBe('private, no-cache')
  const scriptSrc = h['content-security-policy'].split('; ').find((d) => d.startsWith('script-src '))!
  expect(scriptSrc).toMatch(/^script-src 'self'( 'sha256-[A-Za-z0-9+/=]+')+$/)

  await page.goto('/help/user-guide/inbox/')
  await expect(page.getByRole('heading', { level: 1, name: 'The inbox' })).toBeVisible()
  // The theme's own script ran: the navigation and the palette switch are live.
  await expect(page.locator('body[data-md-color-scheme]')).toHaveCount(1)
  // Search: the index and the worker load from /help/.
  const query = page.locator('input[data-md-component="search-query"]')
  // A query typed before the worker has the index is not run again: type it until it is answered.
  await expect(async () => {
    await query.click()
    await query.fill('')
    await query.pressSequentially('snooze')
    await expect(page.locator('.md-search-result__item').first()).toBeVisible({ timeout: 2000 })
  }).toPass({ timeout: 30_000 })
  // The architecture diagram: Mermaid from /help/assets/vendor, no CDN.
  await page.goto('/help/getting-started/')
  // The theme draws it into a closed shadow root in place of the code block: the block is gone and
  // the drawing has a size.
  await expect(page.locator('pre.mermaid')).toHaveCount(0)
  await expect(page.locator('div.mermaid').first()).toBeVisible()
  expect((await page.locator('div.mermaid').first().boundingBox())!.height).toBeGreaterThan(100)
  // A missing page: the guide's own 404 page, styled from /help/.
  const missing = await page.goto('/help/no-such-page/')
  expect(missing?.status()).toBe(404)
  await expect(page.locator('.md-content h1')).toHaveText('404 - Not found')
})

test('the Help entries open the guide in a new tab', async ({ page, context }) => {
  await page.goto('/mail/inbox')
  await page.getByRole('button', { name: 'Support' }).click()
  const fromSupport = await opensHelp(context, () => page.getByRole('menuitem', { name: 'Help' }).click())
  await fromSupport.close()
  // The account menu has it in every theme (Cards has no ? button).
  await page.evaluate(() => localStorage.setItem('mailr.uiTheme', 'cards'))
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'cards'))
  await page.goto('/mail/inbox')
  await page.getByTestId('account-btn').click()
  const fromAccount = await opensHelp(context, () => page.getByRole('menuitem', { name: 'Help' }).click())
  await fromAccount.close()
})
