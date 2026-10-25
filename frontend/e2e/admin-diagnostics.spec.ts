// ACT-SURFACES: web.settings
// Settings → Admin → Diagnostics (settings.admin_diagnostics): every readiness check with its
// plain name, a failing backup shown as a failure, push, background work, the accounts and the
// models; Model endpoints → Test (settings.model_test); Settings → Accounts' health line
// (settings.account_health). tests/test_diagnostics.py covers the routes against real
// servers; here the endpoint test and a failing account are answered by the test.
import type { Page } from '@playwright/test'
import { accessTest as test, expect, signIn } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

interface Diag {
  ok: boolean
  checks: { name: string; label: string; ok: boolean; detail: string; required: boolean }[]
  backup: Record<string, unknown>
}

async function diagnostics(page: Page, patch: (d: Diag) => void) {
  await page.route('**/api/v1/admin/diagnostics**', async (route) => {
    const res = await route.fetch()
    const d = (await res.json()) as Diag
    patch(d)
    await route.fulfill({ json: d })
  })
}

test('an admin sees every check, and an unmounted backup share as a failure (settings.admin_diagnostics)', async ({ page, access }) => {
  await signIn(page, access.admin)
  await diagnostics(page, (d) => {
    // The test server runs no worker: only the backup check fails here.
    for (const c of d.checks) Object.assign(c, { ok: true })
    const b = d.checks.find((c) => c.name === 'backup_target')!
    Object.assign(b, { ok: false, detail: 'backup target not mounted (no .mailr-backup-target file)' })
    d.ok = false
    d.backup = { ...d.backup, target_configured: true, target_problem: 'backup target not mounted (no .mailr-backup-target file)' }
    // A check the server gains later shows under its own name.
    d.checks.push({ name: 'db_backup', label: 'Db backup', ok: true, detail: 'dumped 3 h ago', required: true })
  })
  await page.goto('/settings/admin/diagnostics')
  const diag = page.getByTestId('diagnostics')
  await expect(diag).toBeVisible()
  await expect(page.getByTestId('diag-summary')).toContainText('1 check failing.')
  const checks = page.getByTestId('diag-checks')
  // The failing check comes first, with its plain name and the server's words.
  await expect(checks.locator('li').first()).toContainText('Backup copy')
  await expect(checks.locator('li').first()).toContainText('not mounted')
  await expect(checks.locator('li[data-check="db"]')).toContainText('Database')
  await expect(checks.locator('li[data-check="db_backup"]')).toContainText('dumped 3 h ago')
  await expect(page.getByTestId('diag-backup-problem')).toContainText('.mailr-backup-target')
  await expect(page.getByTestId('diag-models')).toContainText('Ambrose and writing')
  await expect(page.getByTestId('diag-proxy')).toContainText('127.0.0.1')
  // Run checks now asks the server for a fresh run.
  const fresh = page.waitForRequest((r) => r.url().includes('/admin/diagnostics') && r.url().includes('refresh=true'))
  await page.getByTestId('diag-run').click()
  await fresh
  await expect(page.getByTestId('diag-run')).toHaveText(/Run checks now/)
  const out = process.env.PW_OUTPUT_DIR ?? 'test-results'
  await page.screenshot({ path: `${out}/admin-diagnostics.png` })
  await page.getByTestId('diag-proxy').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${out}/admin-diagnostics-lower.png` })
})

test('the Test button of an endpoint shows the latency or the error in plain words (settings.model_test)', async ({ page, access }) => {
  await signIn(page, access.admin)
  let calls = 0
  await page.route('**/api/v1/admin/models/*/test', async (route) => {
    calls += 1
    await route.fulfill({ json: calls === 1
      ? { ok: true, kind: 'chat', model: 'qwen', latency_ms: 412, reply: 'OK' }
      : { ok: false, kind: 'chat', model: 'qwen', latency_ms: 31, error: 'The API key was refused.' } })
  })
  await page.goto('/settings/admin/models')
  const button = page.getByRole('button', { name: `Test ${access.endpoint}` })
  await button.click()
  await expect(page.getByText('Works: qwen answered in 412 ms (reply: OK)')).toBeVisible()
  await button.click()
  await expect(page.getByText('The API key was refused. (after 31 ms)')).toBeVisible()
})

test('a member sees their accounts in plain words, and which one needs its password (settings.account_health)', async ({ page, access }) => {
  await signIn(page, access.family)
  await page.route('**/api/v1/connectors', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const res = await route.fetch()
    const rows = await res.json()
    const list = Array.isArray(rows) ? rows : rows.items
    const base = { kind: 'imap', preset: null, mode: 'mirror', data_class: 'normal', config: {}, retention_days: 14, delete_upstream: false,
      enabled: true, created_at: '2026-10-01T10:00:00Z', has_password: true, progress: { initial_done: true } }
    list.push(
      { ...base, id: 990001, name: 'Home mail', status: 'idle', status_detail: '', last_sync_at: new Date(Date.now() - 120_000).toISOString(),
        health: { state: 'ok', text: 'Up to date' } },
      { ...base, id: 990002, name: 'Work mail', status: 'error', status_detail: "LoginError: b'[AUTHENTICATIONFAILED] Invalid credentials'",
        last_sync_at: new Date(Date.now() - 7_200_000).toISOString(), health: { state: 'signin', text: 'Password required' } },
    )
    await route.fulfill({ json: rows })
  })
  await page.goto('/settings/mail/accounts')
  await expect(page.getByTestId('account-health-line')).toHaveText(/Work mail: Password required\./)
  await expect(page.getByText('Password rejected by the provider. Choose Edit and enter it again.')).toBeVisible()
  await expect(page.getByTestId('account-health').filter({ hasText: 'Up to date' })).toBeVisible()
})
