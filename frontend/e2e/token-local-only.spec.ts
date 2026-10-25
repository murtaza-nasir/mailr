// The local-only box of the Create API token dialog (Settings → Agents & API) follows the user's
// local-only accounts and the policy switch "Mark agents as local-only" (backend core/access.py).
import type { APIRequestContext, Page } from '@playwright/test'
import { accessTest as test, expect, signIn, type AccessSeed } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

/** The admin sets the family group's switches. */
async function setFeatures(request: APIRequestContext, access: AccessSeed, features: Record<string, boolean>) {
  const login = await request.post('/api/v1/auth/login', { data: { email: access.admin.email, password: access.admin.password } })
  const headers = { 'X-CSRF-Token': (await login.json()).csrf }
  const overview = await (await request.get('/api/v1/admin/access')).json()
  const pol = overview.policies.find((p: { group: string }) => p.group === access.group)
  const entry = { endpoint: access.endpoint, model: 'e2e-small' }
  const task = { any: false, allowed: [entry], default: entry }
  const r = await request.put(`/api/v1/admin/access/policies/${pol.id}`, { headers, data: {
    group: access.group, description: pol.description, models: { chat: task, classify: task },
    features: { ...pol.features, ...features },
  } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

/** The family user's accounts, plus a local-only one when `clinic` is set (the list is answered
 *  in the browser; the dialog reads only the list). */
async function accounts(page: Page, clinic: boolean) {
  await page.route('**/api/v1/connectors', async (route) => {
    // A navigation can cancel the request while the real answer is on its way (openDialog goes to
    // the page again): then the route is gone, and there is nothing to answer.
    const res = await route.fetch().catch(() => null)
    if (!res) return
    const real = await res.json()
    const items = Array.isArray(real) ? real : (real.items ?? [])
    if (clinic) items.push({
      id: 990001, kind: 'imap', name: 'Clinic', preset: null, mode: 'mirror', data_class: 'local_only', config: {},
      retention_days: 0, delete_upstream: false, enabled: true, status: 'idle', status_detail: '', last_sync_at: null,
    })
    await route.fulfill({ response: res, json: Array.isArray(real) ? items : { ...real, items } }).catch(() => {})
  })
}

async function openDialog(page: Page) {
  await page.goto('/settings/agents')
  await page.getByRole('button', { name: 'Create token' }).click()
  const d = page.getByRole('dialog', { name: 'Create API token' })
  await expect(d.getByLabel(/^Name/)).toBeVisible()
  return d
}

test('the local-only box follows local-only accounts and the policy switch', async ({ page, access, playwright, baseURL }) => {
  const admin = await playwright.request.newContext({ baseURL })
  await setFeatures(admin, access, { api_tokens: true, local_only_tokens: false })
  await signIn(page, access.family)
  await page.request.patch('/api/v1/me/settings', { data: { advanced_settings: true } })

  // No local-only account: neither the box nor the note.
  await accounts(page, false)
  let d = await openDialog(page)
  await expect(d.getByTestId('token-local-only')).toHaveCount(0)
  await expect(d.getByTestId('token-local-only-note')).toHaveCount(0)
  await expect(d.getByRole('checkbox', { name: /local models only/ })).toHaveCount(0)
  await page.keyboard.press('Escape')

  // A local-only account without the switch: a short note, no box.
  await page.unroute('**/api/v1/connectors')
  await accounts(page, true)
  d = await openDialog(page)
  await expect(d.getByTestId('token-local-only-note')).toContainText('Your administrator decides whether agents may read local-only accounts')
  await expect(d.getByRole('checkbox', { name: /local models only/ })).toHaveCount(0)
  await page.keyboard.press('Escape')

  // With the switch: the box, with the examples.
  await setFeatures(admin, access, { api_tokens: true, local_only_tokens: true })
  d = await openDialog(page)
  await expect(d.getByTestId('token-local-only-note')).toHaveCount(0)
  await expect(d.getByRole('checkbox', { name: /local models only/ })).toBeVisible()
  await expect(d.getByTestId('token-local-only')).toContainText('Required to read Clinic')
  await expect(d.getByTestId('token-local-only')).toContainText('Claude, ChatGPT, Gemini and Copilot are not local')
  await admin.dispose()
})
