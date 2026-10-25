// The phone's Mail tab badge and the Inbox count: the badge
// counted every unread conversation in the Inbox while the Inbox entry follows the Inbox count
// setting (the first tab's unread by default), so they disagreed (19 and 16). The badge now shows
// the Inbox entry's number.
// Covers: web.nav (phone).
import { expect, test } from './fixtures'

test('phone: the Mail tab badge shows the Inbox count', async ({ page }) => {
  await page.route(/\/api\/v1\/inbox\/count(\?|$)/, (r) => r.fulfill({ json: { count: 7, mode: 'first_tab', layout: null, tab: null } }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('phone-tabs').locator('[data-tab="mail"] .tab-badge')).toHaveText('7')
})
