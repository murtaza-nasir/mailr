// One app switcher in every theme: the same apps
// in the same order and with the same names, Ambrose's one button left of the bell in the top bar,
// Compose as the one verb for a new email, Classic's right rail as panels beside the page, and
// /tables inside Notes. The phone bar: phone/nav-switcher-phone.spec.ts.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { THEMES } from './matrix'
import { openApp } from './helpers'

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'
const SWITCHER: Record<Theme, string> = { classic: '.sidebar .nav-apps', office: '.o-appbar', focus: '.f-apps', desk: '.d-spaces', cards: '.c-rail' }

const open = (page: Page, theme: Theme, path = '/mail/inbox') => openApp(page, path, { theme, ready: page.locator('[data-mail-list]').first() })

for (const theme of THEMES) {
  test(`${theme}: Mail, Calendar, Tasks, Notes in order; Ambrose left of the bell; Compose`, async ({ page }) => {
    await open(page, theme)
    const apps = page.locator(`${SWITCHER[theme]} [data-app-nav]`)
    expect(await apps.evaluateAll((els) => els.map((e) => e.getAttribute('data-app-nav')))).toEqual(['mail', 'calendar', 'tasks', 'notes'])
    // The same names (visible text or accessible name) in every theme.
    const names = await apps.evaluateAll((els) => els.map((e) => (e.getAttribute('aria-label') || e.querySelector('.c-dest-label')?.textContent || e.textContent || '').trim()))
    expect(names).toEqual(['Mail', 'Calendar', 'Tasks', 'Notes'])

    // Ambrose: one button, in the top bar, immediately left of the bell.
    const ambrose = page.locator('[data-ambrose]:visible')
    await expect(ambrose).toHaveCount(1)
    await expect(ambrose).toHaveAccessibleName('Ambrose')
    await expect(ambrose).toHaveText('Ambrose')
    const bell = page.getByTestId('waiting-bell').first()
    const a = (await ambrose.boundingBox())!
    const b = (await bell.boundingBox())!
    expect(a.y).toBeLessThan(64)
    expect(a.x + a.width).toBeLessThanOrEqual(b.x + 1)
    expect(b.x - (a.x + a.width)).toBeLessThan(24)
    expect(Math.abs(a.y + a.height / 2 - (b.y + b.height / 2))).toBeLessThan(6)
    expect(a.x).toBeGreaterThan(page.viewportSize()!.width / 2)
    // It opens and closes Ambrose's panel.
    await ambrose.click()
    await expect(page.locator('.side-panel')).toBeVisible()
    await expect(page.locator('.side-panel-head h2')).toHaveText('Ambrose')
    await expect(ambrose).toHaveAttribute('aria-pressed', 'true')
    await ambrose.click()
    await expect(page.locator('.side-panel')).toHaveCount(0)

    // One verb for a new email.
    await expect(page.getByRole('button', { name: 'Compose' }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /^(New mail|Write)$/ })).toHaveCount(0)
    // No second row of app icons (the old panel toggles named Calendar, Tasks or Notes).
    await expect(page.getByRole('button', { name: /^(Calendar|Tasks|Notes|Assistant)$/ })).toHaveCount(0)

    // The switcher goes to Notes.
    await apps.nth(3).click()
    await expect(page).toHaveURL(/\/notes/)
    await expect(page.locator('[data-ambrose]:visible')).toHaveCount(1)
  })
}

test('Classic: the right rail opens Calendar, Tasks and Notes beside the page', async ({ page }) => {
  await open(page, 'classic')
  const rail = page.getByRole('complementary', { name: 'Open beside' })
  // Only the three toggles: no caption above them.
  await expect(rail).toHaveText('')
  await expect(rail.getByRole('button')).toHaveCount(3)
  for (const app of ['Calendar', 'Tasks', 'Notes']) {
    await rail.getByRole('button', { name: `${app} beside` }).click()
    await expect(page.locator('.side-panel-head h2')).toHaveText(app)
    // A panel beside the page: the mail list stays.
    await expect(page).toHaveURL(/\/mail\/inbox/)
    await rail.getByRole('button', { name: `${app} beside` }).click()
    await expect(page.locator('.side-panel')).toHaveCount(0)
  }
})

test('/tables opens Notes → Tables', async ({ page }) => {
  await page.goto('/tables')
  await expect(page).toHaveURL(/\/notes\/tables$/)
  await page.goto('/tables/anything')
  await expect(page).toHaveURL(/\/notes\/tables$/)
})
