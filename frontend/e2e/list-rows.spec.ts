// web.list rows at phone width (Classic and Cards): a snippet that repeats the subject is not
// shown twice, and a row without a snippet has no empty line. The list response is edited in
// flight: "E2E Archive me" gets its subject as snippet, "E2E Delete me" gets none.
import { expect, shotsDir, test } from './fixtures'

const SHOTS = shotsDir('rows')

for (const theme of ['classic', 'cards'] as const) {
  test(`${theme} at phone width: no repeated snippet, no empty line`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.route(/\/api\/v1\/conversations\?/, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      for (const c of body.items ?? []) {
        if (c.subject === 'E2E Archive me') c.snippet = 'E2E Archive me'
        if (c.subject === 'E2E Delete me') c.snippet = ''
      }
      await route.fulfill({ response: res, json: body })
    })
    await page.goto('/mail/inbox')
    const row = (s: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: s }).first()
    await expect(row('E2E Archive me')).toBeVisible()
    await expect(row('E2E Archive me').getByText('E2E Archive me', { exact: true })).toHaveCount(1)
    const snippet = theme === 'cards' ? '.c-snippet' : '.snippet'
    await expect(row('E2E Archive me').locator(snippet)).toHaveCount(0)
    await expect(row('E2E Delete me').locator(snippet)).toHaveCount(0)
    // The row without a snippet is no taller than the subject line needs.
    const withText = await row('E2E Read me').boundingBox()
    const without = await row('E2E Delete me').boundingBox()
    expect(without!.height).toBeLessThan(withText!.height)
    await row('E2E Delete me').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/web-${theme}-phone.png` })
  })
}
