// Classic's sidebar in Calendar, Tasks and Notes: the 240 px mail
// navigation (folders, labels) sat beside each app's own sidebar and squeezed the week grid. In
// those apps it is now a rail of the apps; Mail keeps the full navigation.
// Covers: web.layout.
import { expect, test } from './fixtures'

for (const path of ['/calendar/week', '/tasks', '/notes']) {
  test(`Classic ${path}: the sidebar is a rail of the apps`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(path)
    const nav = page.locator('nav.sidebar')
    await expect(nav.locator('[data-app-nav="mail"]')).toBeVisible()
    expect((await nav.boundingBox())!.width).toBeLessThanOrEqual(90)
    await expect(nav.getByRole('link', { name: /^Inbox/ })).toHaveCount(0)
    await nav.locator('[data-app-nav="mail"]').click()
    await expect(page).toHaveURL(/\/mail\//)
    await expect(page.locator('nav.sidebar').getByRole('link', { name: /^Inbox/ }).first()).toBeVisible()
  })
}
