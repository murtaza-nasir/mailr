// ARIA attributes axe flagged:
// - Settings' page links used aria-pressed, which a link may not carry; the current page is
//   aria-current="page" alone.
// - The list/reader resize handle (role separator, Office and Desk) lacked aria-valuenow, -min and
//   -max.
// - Classic's collapsed app rail (Calendar, Tasks, Notes) hid each link's name with display: none,
//   so a screen reader heard "link, link, link, link" (axe link-name). The name is now hidden from
//   sight only.
// Covers: web.a11y, web.settings.
import { expect, test } from './fixtures'
import { openApp } from './helpers'

test('Settings page links: aria-current, no aria-pressed', async ({ page }) => {
  await page.goto('/settings/mail/labels')
  const nav = page.getByRole('navigation', { name: 'Mail pages' })
  await expect(nav.getByRole('link', { name: 'Labels', exact: true })).toHaveAttribute('aria-current', 'page')
  expect(await nav.locator('[aria-pressed]').count()).toBe(0)
})

for (const theme of ['office', 'desk']) {
  test(`${theme}: the resize handle says its value and range`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openApp(page, '/mail/inbox', { theme, ready: page.locator('[data-mail-list] [role="row"]').first() })
    const handle = page.getByRole('separator', { name: /resize the message list/i })
    await expect(handle).toHaveAttribute('aria-valuenow', /^\d+$/)
    await expect(handle).toHaveAttribute('aria-valuemin', /^\d+$/)
    await expect(handle).toHaveAttribute('aria-valuemax', /^\d+$/)
    const [now, min, max] = await Promise.all(['aria-valuenow', 'aria-valuemin', 'aria-valuemax'].map(async (a) => Number(await handle.getAttribute(a))))
    expect(now).toBeGreaterThanOrEqual(min)
    expect(max).toBeGreaterThan(min)
  })
}

test('Classic: the collapsed app rail names its links', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openApp(page, '/notes', { theme: 'classic' })
  const nav = page.getByRole('navigation', { name: 'Apps' })
  for (const name of ['Mail', 'Calendar', 'Tasks', 'Notes']) await expect(nav.getByRole('link', { name, exact: true })).toBeVisible()
})
