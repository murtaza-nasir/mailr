// The app's one tooltip (components/TooltipLayer.tsx) goes when attention moves, as on other sites:
// the window losing focus, the tab being hidden, the pointer leaving the window, Esc, a click, and
// its element leaving the page.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const tip = (page: Page) => page.locator('.tooltip[role=tooltip]')

async function hoverTip(page: Page) {
  await page.mouse.move(0, 0)
  const el = page.locator('[data-mail-list] .toolbar [data-tip]:visible').first()
  await el.hover()
  await expect(tip(page)).toBeVisible()
  return el
}

test('a tooltip goes when the window, the tab or the pointer moves on', async ({ page }) => {
  await page.goto('/mail/inbox')
  await hoverTip(page)
  await page.evaluate(() => window.dispatchEvent(new Event('blur')))
  await expect(tip(page)).toHaveCount(0)

  await hoverTip(page)
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect(tip(page)).toHaveCount(0)
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  })

  await hoverTip(page)
  await page.evaluate(() => document.body.dispatchEvent(new PointerEvent('pointerout', { pointerType: 'mouse', bubbles: true, relatedTarget: null })))
  await expect(tip(page)).toHaveCount(0)

  await hoverTip(page)
  await page.keyboard.press('Escape')
  await expect(tip(page)).toHaveCount(0)
})

test('a tooltip goes with its element', async ({ page }) => {
  await page.goto('/mail/inbox')
  const el = await hoverTip(page)
  const text = (await tip(page).textContent()) ?? ''
  await el.evaluate((n) => n.remove())
  // Its tip goes (what is now under the pointer may show its own).
  await expect(tip(page).filter({ hasText: text })).toHaveCount(0)
})
