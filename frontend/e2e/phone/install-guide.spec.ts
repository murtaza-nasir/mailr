// Install guidance (features/pwa/InstallGuide.tsx, web.install): an iPhone (WebKit, iOS user agent)
// gets the Share → Add to Home Screen steps with a picture; Android Chrome gets an Install button
// from the browser's install prompt (or the menu steps until the prompt has come). Nothing shows in
// the installed app; a dismissed banner stays away; Settings → General → Install app has the same guide.
import { expect, test } from '../fixtures'
import { emulateStandalone, isIPhone, settled } from './phone'

test('the banner offers the right steps for this phone, and a dismissal sticks', async ({ page }, info) => {
  await page.goto('/mail/inbox')
  const banner = page.getByTestId('install-banner')
  await expect(banner).toBeVisible()
  await banner.getByRole('button', { name: 'How' }).click()
  const sheet = page.getByTestId('install-sheet')
  await expect(sheet).toBeVisible()
  await settled(sheet)
  if (isIPhone(info.project.name)) {
    await expect(sheet.locator('[data-install="ios"]')).toBeVisible()
    await expect(sheet.getByRole('img', { name: /^Safari's Share button/ })).toBeVisible()
    await expect(sheet).toContainText('Add to Home Screen')
    await expect(sheet).toContainText('Share')
  } else {
    // No install prompt from the browser yet: the menu steps.
    await expect(sheet.locator('[data-install="prompt"], [data-install="menu"]')).toBeVisible()
    await expect(sheet).toContainText('Install app')
  }
  await sheet.getByRole('button', { name: 'Close' }).click()
  await expect(sheet).toHaveCount(0)

  await banner.getByRole('button', { name: 'Dismiss' }).click()
  await expect(banner).toHaveCount(0)
  await page.reload()
  await expect(page.getByTestId('phone-tabs')).toBeVisible()
  await expect(page.getByTestId('install-banner')).toHaveCount(0)
  // Still reachable from the account menu.
  await page.getByRole('button', { name: /^Account:/ }).click()
  await page.getByRole('menuitem', { name: 'Install app' }).click()
  await expect(page.getByTestId('install-sheet')).toBeVisible()
})

test('Android: the browser install prompt becomes an Install button', async ({ page }, info) => {
  test.skip(isIPhone(info.project.name), 'iOS has no install prompt')
  await page.goto('/settings/you/security')
  const row = page.locator('#install-app')
  await expect(row).toContainText('Install app')
  // The browser says mailr may be installed (Chrome fires this once the manifest qualifies).
  await page.evaluate(() => {
    const e = new Event('beforeinstallprompt', { cancelable: true }) as Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }
    ;(window as unknown as { __prompted: number }).__prompted = 0
    e.prompt = async () => {
      ;(window as unknown as { __prompted: number }).__prompted++
    }
    e.userChoice = Promise.resolve({ outcome: 'accepted' })
    window.dispatchEvent(e)
  })
  await row.getByRole('button', { name: 'Install Mailr' }).click()
  await expect.poll(() => page.evaluate(() => (window as unknown as { __prompted: number }).__prompted)).toBe(1)
  await expect(page.getByText('Mailr is being added to your device.')).toBeVisible()
})

test('iPhone: Settings → General → Install app shows the Home Screen steps', async ({ page }, info) => {
  test.skip(!isIPhone(info.project.name), 'iOS steps')
  await page.goto('/settings/you/security')
  const row = page.locator('#install-app')
  await expect(row.locator('[data-install="ios"]')).toBeVisible()
  await expect(row).toContainText('Add to Home Screen')
  // Notifications on iOS need the Home Screen app: Settings → Notifications says so.
  await page.goto('/settings/notifications')
  await expect(page.getByTestId('push-ios-install')).toBeVisible()
})

test('nothing to install in the installed app', async ({ page }) => {
  await emulateStandalone(page)
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('phone-tabs')).toBeVisible()
  await expect(page.getByTestId('install-banner')).toHaveCount(0)
  await page.getByRole('button', { name: /^Account:/ }).click()
  await expect(page.getByRole('menuitem', { name: 'Settings' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Install app' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await page.goto('/settings/you/security')
  await expect(page.locator('#install-app [data-install="installed"]')).toBeVisible()
})
