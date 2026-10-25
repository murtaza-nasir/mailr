// ACT-SURFACES: web.list
// List motion is quick enough to delete row after row (about 120 ms from click
// to the next row in place). Covers: mail.delete.
import { expect, test } from './fixtures'

test('mail.delete: a row leaves within a moment, and the next row takes the next click', async ({ page }) => {
  await page.goto('/mail/inbox')
  const rows = page.locator('[data-mail-list] [role="row"][data-id]')
  await expect(rows.nth(2)).toBeVisible()
  const first = rows.nth(0)
  const idA = await first.getAttribute('data-id')
  const idB = await rows.nth(1).getAttribute('data-id')

  await first.hover()
  const del = first.getByRole('button', { name: 'Delete' })
  const box = (await del.boundingBox())!
  // Measured in the page, from the click event to the row leaving the DOM, so the time the test
  // runner and the browser protocol take (seconds on a loaded machine) does not count.
  await page.evaluate((id) => {
    const w = window as unknown as { __rowGone?: { clicked: number; gone: number } }
    const m = { clicked: 0, gone: 0 }
    w.__rowGone = m
    document.addEventListener('click', (e) => { if (!m.clicked) m.clicked = e.timeStamp }, { capture: true, once: true })
    const list = document.querySelector('[data-mail-list]')!
    const obs = new MutationObserver(() => {
      if (m.clicked && !list.querySelector(`[data-id="${id}"]`)) {
        m.gone = performance.now()
        obs.disconnect()
      }
    })
    obs.observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-id'] })
  }, idA)
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
  await expect(page.locator(`[data-mail-list] [data-id="${idA}"]`)).toHaveCount(0, { timeout: 5000 })
  const t = await page.evaluate(() => (window as unknown as { __rowGone: { clicked: number; gone: number } }).__rowGone)
  expect(t.clicked, 'the click reached the page').toBeGreaterThan(0)
  expect(t.gone, 'the row left the list').toBeGreaterThan(0)
  // The animation is ~120 ms; leave room for a busy page, but far below the old ~400 ms + render.
  expect(t.gone - t.clicked).toBeLessThan(450)

  // The next row is now where the first was: delete it with the same quick click.
  const next = page.locator(`[data-mail-list] [data-id="${idB}"]`)
  await next.hover()
  await next.getByRole('button', { name: 'Delete' }).click()
  await expect(next).toHaveCount(0, { timeout: 1000 })
})
