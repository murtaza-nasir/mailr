// VIP senders: "Make … a VIP sender" from the message menu adds the address to Settings → General,
// where it can be removed. (backend/tests/test_vip.py: VIP mail is important and is:vip finds it.)
import { expect, test } from './fixtures'

const vip = async (request: import('@playwright/test').APIRequestContext) => ((await (await request.get('/api/v1/me')).json()).settings?.vip ?? []) as string[]

test('make a sender VIP from the message menu, then remove them in Settings', async ({ page }) => {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Reply to me' }).first().click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
  await page.getByRole('button', { name: 'More', exact: true }).last().click()
  await page.getByRole('menuitem', { name: 'Make Dana Tester a VIP sender' }).click()
  await expect.poll(() => vip(page.request)).toEqual(['dana-10@example.org'])
  await page.getByRole('button', { name: 'More', exact: true }).last().click()
  await expect(page.getByRole('menuitem', { name: 'Remove from VIP senders' })).toBeVisible()
  await page.keyboard.press('Escape')

  await page.goto('/settings/mail/inbox')
  await page.getByRole('button', { name: 'Remove dana-10@example.org from VIP senders' }).click()
  await expect.poll(() => vip(page.request)).toEqual([])
})
