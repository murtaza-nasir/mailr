// Focus with Ambrose open: the panel lay over the list, hiding the
// rows' times and chips. On a wide screen it now pushes the page aside, as in Classic.
// Covers: web.layout (Focus).
import { expect, test } from './fixtures'
import { openApp } from './helpers'

test('Focus at 1440 px: Ambrose beside the list, not over it', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const row = page.locator('[data-mail-list] [role="row"][data-id]').first()
  await openApp(page, '/mail/inbox', { theme: 'focus', ready: row })
  await page.locator('.ambrose-btn').click()
  const panel = page.locator('aside.side-panel')
  await expect(panel).toBeVisible()
  const p = (await panel.boundingBox())!
  await expect.poll(async () => (await row.boundingBox())!.x + (await row.boundingBox())!.width).toBeLessThanOrEqual(p.x)
  await expect(page.locator('.f-scrim')).toBeHidden()
})
