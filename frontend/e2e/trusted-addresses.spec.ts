// ACT-SURFACES: web.settings
// Settings → Ambrose → Trusted addresses (backend core/trusted.py): trusted.add (a code goes
// out through the send gate; the seeded identity's connector is disabled, so it stays in the outbox),
// trusted.confirm with that code, trusted.list and trusted.remove after its dialog; the addresses another
// assistant handles (add, kept after a reload, remove). What Ambrose does with a forwarded note is the
// built-in standing order (e2e/orders-builtin.spec.ts). The trust rule itself is covered by
// backend/tests/test_trusted_forwards.py and test_orders_consolidate.py.
import { expect, outbox, shotsDir, test } from './fixtures'

test('add an address, confirm it with the mailed code, name an address another assistant handles, then remove them', async ({ page }) => {
  const addr = `me-${Date.now()}@work.example`
  await page.goto('/settings/ambrose/forwards')
  const section = page.getByTestId('trusted-addresses')
  await expect(section).toContainText('No addresses yet.')
  await page.getByLabel('An address of yours').fill(addr)
  await page.getByRole('button', { name: 'Send code' }).click()
  const row = section.getByTestId('trusted-address').filter({ hasText: addr })
  await expect(row).toHaveAttribute('data-state', 'pending')
  await expect(row).toContainText('Code sent')
  await page.screenshot({ path: `${shotsDir('trusted')}/pending.png` })

  let code = ''
  await expect.poll(async () => {
    const mail = (await outbox(page.request)).find((o) => /Confirmation code \d{6}/.test(o.subject))
    code = mail?.subject.match(/(\d{6})/)?.[1] ?? ''
    return code
  }).toMatch(/^\d{6}$/)
  await row.getByLabel(`Code for ${addr}`).fill(code)
  await row.getByRole('button', { name: 'Confirm' }).click()
  await expect(row).toHaveAttribute('data-state', 'confirmed')
  await expect(page.locator('.toast').filter({ hasText: `${addr} confirmed` })).toBeVisible()

  // The identity page only: the note's handling is a standing order, one link away.
  await expect(page.getByTestId('trusted-order-link').getByRole('link')).toHaveAttribute('href', '/settings/ambrose/orders')
  await expect(page.getByRole('radio', { name: /Allow/ })).toHaveCount(0)

  const elsewhere = page.getByTestId('handled-elsewhere')
  const alias = `memory-${Date.now()}@example.com`
  await expect(elsewhere).toContainText('None.')
  await page.getByLabel('Address for another assistant').fill(alias)
  await elsewhere.getByRole('button', { name: 'Add' }).click()
  await expect(elsewhere).toContainText(alias)
  await page.reload()
  await expect(page.getByTestId('handled-elsewhere')).toContainText(alias)
  await page.screenshot({ path: `${shotsDir('orders-consolidate')}/identity.png`, fullPage: true })
  await page.getByTestId('handled-elsewhere').getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByTestId('handled-elsewhere')).toContainText('None.')

  await page.getByTestId('trusted-address').filter({ hasText: addr }).getByRole('button', { name: 'Remove' }).click()
  await page.getByRole('dialog', { name: `Remove ${addr}?` }).getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByTestId('trusted-addresses')).toContainText('No addresses yet.')
})

test('a wrong code is refused and the address keeps waiting', async ({ page }) => {
  const addr = `me-${Date.now()}@work.example`
  await page.goto('/settings/ambrose/forwards')
  await page.getByLabel('An address of yours').fill(addr)
  await page.getByRole('button', { name: 'Send code' }).click()
  const row = page.getByTestId('trusted-address').filter({ hasText: addr })
  await row.getByLabel(`Code for ${addr}`).fill('000000x')
  await row.getByRole('button', { name: 'Confirm' }).click()
  await expect(page.locator('.toast').filter({ hasText: 'wrong code' })).toBeVisible()
  await expect(row).toHaveAttribute('data-state', 'pending')
})
