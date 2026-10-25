// Short screens: at 200 % zoom on a 1440 × 900 laptop (720 × 450
// CSS pixels) the fixed bars left one conversation in view. Scrolling down now moves the top bar
// and the tab bar away; scrolling back to the top brings them back.
// Covers: web.layout.
import { expect, test } from './fixtures'
import { openApp } from './helpers'
import { THEMES } from './matrix'

test.use({ bulk: 20 })

for (const theme of THEMES) {
  test(`${theme}: on a 720 × 450 screen the bars move away while the list scrolls down`, async ({ page }) => {
    await page.setViewportSize({ width: 720, height: 450 })
    const list = page.locator('.list-scroll').first()
    await openApp(page, '/mail/inbox', { theme, ready: page.locator('[data-mail-list] [role="row"]').first() })
    const header = page.locator('.app > header, .app > .d-toolbar')
    await expect(header).toBeVisible()
    const before = (await list.boundingBox())!.height
    await list.hover()
    await page.mouse.wheel(0, 600)
    await expect(header).toBeHidden()
    await expect.poll(async () => (await list.boundingBox())!.height).toBeGreaterThan(before + 40)
    await list.evaluate((el) => el.scrollTo({ top: 0 }))
    await expect(header).toBeVisible()
  })
}
