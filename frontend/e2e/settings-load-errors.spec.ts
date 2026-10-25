// A settings list that fails to load says so with Retry; it never claims there is nothing
// ("No filters yet"), which made people recreate filters they still had.
// Covers: web.settings.
import { expect, test } from './fixtures'

test('Filters and Templates: a failed load shows Couldn\'t load with Retry, not the empty text', async ({ page }) => {
  let fail = true
  await page.route(/\/api\/v1\/(rules|templates)(\?|$)/, (route) =>
    fail && route.request().method() === 'GET' ? route.fulfill({ status: 500, body: 'boom' }) : route.fallback())
  await page.goto('/settings/filters')
  const failed = page.getByTestId('load-failed').filter({ hasText: 'your filters' })
  await expect(failed).toBeVisible()
  await expect(page.getByText('No filters yet')).toHaveCount(0)
  fail = false
  await failed.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByText('No filters yet')).toBeVisible()

  fail = true
  await page.goto('/settings/templates')
  const t = page.getByTestId('load-failed').filter({ hasText: 'templates' })
  await expect(t).toBeVisible()
  await expect(page.getByText('No templates yet.')).toHaveCount(0)
})
