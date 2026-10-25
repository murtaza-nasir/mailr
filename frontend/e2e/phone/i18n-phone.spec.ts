// The pseudo-language sweep on a phone: all five themes' main screens in en-XA,
// accented and about 35% longer. No screen may scroll sideways; the screenshots are attached.
import { expect, test } from '../fixtures'
import { pseudoPage, sweepScreen, THEMES } from '../i18n/sweep'

for (const theme of THEMES) {
  test(`pseudo-language sweep, ${theme}, phone`, async ({ page }, info) => {
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
