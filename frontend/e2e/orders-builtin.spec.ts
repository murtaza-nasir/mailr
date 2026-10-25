// ACT-SURFACES: web.settings
// Settings → Ambrose → Standing orders: the built-in order
// "Follow my forwarded notes" sits at the top with its condition and permission; it cannot be edited or
// deleted; Ask me → Allow (order.permission) is kept after a reload; it turns off (order.pause); order.move
// puts another order above it.
// The editor warns about an order that writes and matches on a recipient address, with a one-click fix.
import { expect, shotsDir, test } from './fixtures'

const SHOTS = shotsDir('orders-consolidate')

test('the built-in order: permission, off and on, moved below an order of mine', async ({ page }) => {
  await page.goto('/settings/ambrose/orders')
  const builtin = page.getByTestId('order-builtin')
  await expect(builtin).toContainText('Follow my forwarded notes')
  await expect(builtin).toContainText('From your trusted addresses, with a note above the forwarded email.')
  await expect(builtin.getByRole('button', { name: 'Delete' })).toHaveCount(0)
  await expect(builtin.getByRole('button', { name: 'Edit' })).toHaveCount(0)
  const perm = builtin.getByTestId('order-builtin-permission')
  await expect(perm).toHaveValue('ask')
  await perm.selectOption('allow')
  await expect(builtin).toContainText('starts on the note at once')
  await page.reload()
  await expect(page.getByTestId('order-builtin-permission')).toHaveValue('allow')
  await page.screenshot({ path: `${SHOTS}/orders.png`, fullPage: true })

  await page.getByTestId('order-builtin').getByRole('switch').click()
  await expect(page.getByTestId('order-builtin')).toHaveClass(/ off/)
  await expect(page.getByTestId('order-builtin')).toContainText('Off: forwarded notes are ordinary mail.')
  await page.getByTestId('order-builtin').getByRole('switch').click()
  await expect(page.getByTestId('order-builtin')).not.toHaveClass(/ off/)

  // An order of mine, then moved above the built-in one.
  const name = `Invoices ${Date.now()}`
  await page.getByRole('button', { name: 'New order' }).click()
  const dlg = page.getByRole('dialog')
  await dlg.getByLabel('Name').fill(name)
  await dlg.getByPlaceholder('from:billing@example.com').fill('subject:invoice')
  await dlg.getByRole('checkbox', { name: 'Star' }).check()
  await dlg.getByRole('button', { name: 'Save' }).click()
  const mine = page.locator('.as-order').filter({ hasText: name })
  await expect(mine).toBeVisible()
  await expect(page.getByTestId('orders-duplicates-hint')).toBeVisible()
  // Other specs may add orders for the same user: move it up until it is the first.
  const up = mine.getByRole('button', { name: `Move ${name} up` })
  for (let i = 0; i < 20 && await up.isEnabled(); i++) {
    const before = await page.locator('.as-orders').first().locator('.as-order').allInnerTexts()
    await up.click()
    await expect.poll(async () => (await page.locator('.as-orders').first().locator('.as-order').allInnerTexts()).join('|')).not.toBe(before.join('|'))
  }
  await expect(page.locator('.as-orders').first().locator('.as-order').first()).toContainText(name)
  await expect(page.locator('.as-orders').first().locator('.as-order').nth(1)).not.toContainText(name)
  await mine.getByRole('button', { name: 'Delete' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click()
  await expect(mine).toHaveCount(0)
})

test('an order on a recipient address that writes warns, and the fix adds the sender condition', async ({ page }) => {
  await page.goto('/settings/ambrose/orders')
  await page.getByRole('button', { name: 'New order' }).click()
  const dlg = page.getByRole('dialog')
  await dlg.getByLabel('Name').fill('Memory alias')
  await dlg.getByPlaceholder('from:billing@example.com').fill('to:memory@example.com')
  await expect(dlg.getByTestId('order-sender-warning')).toHaveCount(0)
  await dlg.getByRole('checkbox', { name: /Add the invitation/ }).check()
  const warn = dlg.getByTestId('order-sender-warning')
  await expect(warn).toContainText('Anyone who knows the address can trigger them.')
  await page.screenshot({ path: `${SHOTS}/warning.png` })
  await warn.getByRole('button', { name: 'Only from me or my trusted addresses' }).click()
  await expect(warn).toHaveCount(0)
  await expect(dlg.getByRole('checkbox', { name: 'Only from me or my trusted addresses' })).toBeChecked()
  await dlg.getByRole('button', { name: 'Cancel' }).click()
})
