// Desk's table over the preview: at 1440 × 900 the list was a
// strip of eight rows above a large empty preview, and From was narrow enough to read "M." and "J…".
// The list now takes most of the height, and From keeps at least 140 px.
// Covers: web.list (Desk).
import { expect, test } from './fixtures'
import { openApp } from './helpers'

test('Desk: the list takes most of the height and senders stay readable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openApp(page, '/mail/inbox', { theme: 'desk', ready: page.locator('.d-row').first() })
  const split = (await page.locator('.split.below').boundingBox())!
  const rows = (await page.locator('.split.below .list-scroll').boundingBox())!
  expect(rows.height).toBeGreaterThanOrEqual(split.height * 0.5)
  await expect.poll(async () => (await page.locator('.d-row .d-from').first().boundingBox())!.width).toBeGreaterThanOrEqual(140)
})
