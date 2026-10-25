// ACT-SURFACES: web.settings
// mail.label on web.settings: Settings → Assistant → Models → Label learning. "Favour recent
// labels" and its half-life save at once as the label_recency setting (backend
// tests/test_label_autoapply.py covers the weights, the auto-apply minimum and the retrain).
import { expect, shot, test } from './fixtures'

const recency = async (request: import('@playwright/test').APIRequestContext) =>
  (await (await request.get('/api/v1/me')).json()).settings?.label_recency

test('mail.label: favour recent labels, its half-life, and off', async ({ page }) => {
  await page.goto('/settings/mail/labels')
  const box = page.getByRole('checkbox', { name: /Favour recent labels/ })
  const half = page.getByRole('combobox', { name: 'Half-life of a label' })
  await expect(box).toBeChecked()
  await expect(half).toHaveValue('12')
  await expect(page.getByText('a label you stopped using goes back to suggestions only.')).toBeVisible()

  await half.selectOption('24')
  await expect.poll(() => recency(page.request)).toEqual({ enabled: true, half_life_months: 24 })
  await expect(page.getByText('Saved. Label models are retrained in a few minutes.')).toBeVisible()

  await box.click()
  await expect(box).not.toBeChecked()
  await expect.poll(() => recency(page.request)).toEqual({ enabled: false, half_life_months: 24 })
  await expect(half).toBeDisabled()
  await page.screenshot({ path: shot('label-recency-off.png'), fullPage: false })

  await page.reload()
  await expect(page.getByRole('checkbox', { name: /Favour recent labels/ })).not.toBeChecked()
  await page.getByRole('checkbox', { name: /Favour recent labels/ }).click()
  await expect.poll(() => recency(page.request)).toEqual({ enabled: true, half_life_months: 24 })
})
