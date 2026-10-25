// ACT-SURFACES: web.settings
// Mail accounts an administrator connects for a member (backend core/managed.py): the switch in
// Admin → Users (admin.managed_accounts_setting), the member's "mail accounts" dialog
// (admin.managed_account_list, admin.managed_account_test, admin.managed_account_connect,
// admin.managed_account_remove), and the
// member's side: "Connected by your administrator", Disconnect, and the Activity entry.
// The setting is instance-wide; this spec turns it off again at the end.
// The servers are made up, and nothing here reaches the network: the sign-in test and the server
// lookup (POST /connectors/discover, which asks DNS and autoconfig.thunderbird.net) are answered here.
import { accessTest as test, expect, signIn } from './fixtures'

const GUESS = {
  source: null, preset: null, kind: 'imap', supported: true, mx: [], tls_verify: true,
  notes: "Couldn't find settings automatically. Enter them from your provider's help page.",
  imap: { host: 'imap.northwind.example', port: 993, tls: 'ssl', username: 'desk@northwind.example' },
  smtp: { host: 'smtp.northwind.example', port: 465, tls: 'ssl', username: 'desk@northwind.example' },
}

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

test('an admin connects a mail account for a member, who sees it and disconnects it', async ({ page, access }) => {
  test.setTimeout(180_000)
  await signIn(page, access.admin)
  await page.route('**/api/v1/admin/users/*/accounts/test', (route) =>
    route.fulfill({ json: { ok: true, detail: 'Signed in', folders: [{ name: 'INBOX' }, { name: 'Sent' }], identities: [] } }))
  // The lookup starts when the address field loses focus. `held` keeps its answer back until released.
  let held: Promise<void> | null = null
  await page.route('**/api/v1/connectors/discover', async (route) => {
    if (held) await held
    await route.fulfill({ json: GUESS })
  })
  let passed = false
  await page.goto('/settings/admin/users')
  const sw = page.getByRole('switch', { name: 'Administrators may connect mail accounts for members' })
  await expect(sw).toBeEnabled()
  if ((await sw.getAttribute('aria-checked')) !== 'true') await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  try {
    await page.getByLabel('Search users').fill(access.family.email)
    const row = page.getByRole('row').filter({ hasText: access.family.email })
    await row.getByRole('button', { name: 'mail accounts' }).click()
    const dlg = page.getByRole('dialog', { name: /Mail accounts for/ })
    await dlg.getByLabel('Email address of the account').fill('desk@northwind.example')
    await dlg.getByLabel('Incoming (IMAP) server').fill('imap.northwind.example')
    await dlg.getByLabel('Outgoing (SMTP) server').fill('smtp.northwind.example')
    await dlg.getByLabel('Password').fill('upstream-secret-1')
    await dlg.getByRole('button', { name: 'Test' }).click()
    await expect(dlg.getByTestId('managed-test')).toContainText('Signed in. 2 folders found.')
    await dlg.getByRole('button', { name: 'Connect for them' }).click()
    await expect(page.getByText(/Connected desk@northwind.example for/)).toBeVisible()
    await expect(dlg.getByRole('listitem').filter({ hasText: 'desk@northwind.example' })).toBeVisible()
    await dlg.getByRole('button', { name: 'Close', exact: true }).last().click()

    // The member's side.
    await signIn(page, access.family)
    await page.goto('/settings/mail/accounts')
    const card = page.locator('.card').filter({ hasText: 'desk@northwind.example' })
    await expect(card.getByTestId('account-managed')).toHaveText('Connected by your administrator')
    await page.goto('/settings/you/activity')
    await expect(page.getByText(/connected desk@northwind.example for you/)).toBeVisible()
    await page.goto('/settings/mail/accounts')
    await card.getByRole('button', { name: 'Remove account' }).click()
    const confirm = page.getByRole('dialog', { name: /Disconnect desk@northwind.example/ })
    await expect(confirm).toContainText('Your administrator is notified')
    await confirm.getByRole('button', { name: 'Disconnect' }).click()
    await expect(card).toHaveCount(0)

    // Back as the admin: the disconnected mailbox is listed, and connecting it again needs a tick.
    await signIn(page, access.admin)
    await page.goto('/settings/admin/users')
    await page.getByLabel('Search users').fill(access.family.email)
    await page.getByRole('row').filter({ hasText: access.family.email }).getByRole('button', { name: 'mail accounts' }).click()
    await expect(page.getByTestId('managed-disconnected')).toContainText('desk@northwind.example')
    // ACT admin.managed_account_connect again: refused until the admin ticks "Connect it again anyway".
    const again = page.getByRole('dialog', { name: /Mail accounts for/ })
    // This time the lookup answers late, after the conflict, the tick and the test (a slow DNS
    // server at night did this): the answer must not undo any of them.
    let release = () => {}
    held = new Promise((resolve) => { release = resolve })
    await again.getByLabel('Email address of the account').fill('desk@northwind.example')
    await again.getByLabel('Incoming (IMAP) server').fill('imap.northwind.example')
    await again.getByLabel('Outgoing (SMTP) server').fill('smtp.northwind.example')
    await again.getByLabel('Password').fill('upstream-secret-1')
    await again.getByRole('button', { name: 'Connect for them' }).click()
    await expect(again.getByRole('alert')).toContainText('disconnected this account')
    await expect(again.getByRole('button', { name: 'Connect for them' })).toBeDisabled()
    await again.getByText('Connect it again anyway').click()
    await again.getByRole('button', { name: 'Test' }).click()
    await expect(again.getByTestId('managed-test')).toContainText('Signed in.')
    await expect(again.getByRole('button', { name: 'Finding…' })).toBeVisible()
    release()
    await expect(again.getByRole('button', { name: 'Find servers' })).toBeVisible()
    await expect(again.getByTestId('managed-test')).toContainText('Signed in.')
    await expect(again.getByRole('checkbox', { name: /Connect it again anyway/ })).toBeChecked()
    await expect(again.getByLabel('Incoming (IMAP) server')).toHaveValue('imap.northwind.example')
    await again.getByRole('button', { name: 'Connect for them' }).click()
    const item = again.getByRole('listitem').filter({ hasText: 'desk@northwind.example' })
    await expect(item).toBeVisible()
    // ACT admin.managed_account_remove: the admin removes it; the member is told.
    await item.getByRole('button', { name: 'Remove desk@northwind.example' }).click()
    await page.getByRole('dialog', { name: 'Remove desk@northwind.example?' }).getByRole('button', { name: 'Remove' }).click()
    await expect(item).toHaveCount(0)
    passed = true
  } finally {
    // Turn the setting off again. After a failure (or the test timeout) this must not replace the
    // first error: a request made after the timeout fails at once with the timeout's message.
    const r = await page.request.put('/api/v1/admin/managed-accounts', { data: { enabled: false } }).catch((e) => {
      if (passed) throw e
      return null
    })
    if (passed) expect(r?.ok()).toBeTruthy()
  }
})
