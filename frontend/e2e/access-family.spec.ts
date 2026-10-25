// A user whose group policy sets the models (the "family" pattern): Settings → Assistant → Models
// is read-only with only the allowed model, and simple Settings leave out Agents & API and the lab.
import { accessTest as test, expect, shot, signIn } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

test('a family user sees their models read-only, set by the administrator', async ({ page, access }) => {
  await signIn(page, access.family)
  await page.goto('/settings/assistant?section=models')
  await expect(page.getByRole('heading', { name: 'Models', exact: true })).toBeVisible()

  for (const task of ['chat', 'classify']) {
    await expect(page.getByTestId(`model-locked-${task}`)).toContainText('Set by your administrator')
    await expect(page.getByTestId(`model-allowed-${task}`)).toHaveText(`Allowed: ${access.endpoint} · e2e-small`)
  }
  await expect(page.getByTestId('model-locked-chat')).toContainText(`${access.endpoint} · e2e-small`)
  await expect(page.getByRole('combobox', { name: /^Model for/ })).toHaveCount(0)

  // Simple settings: no Agents & API group, no decisions lab.
  const groups = page.getByRole('navigation', { name: 'Settings groups' })
  const ambrose = page.getByRole('navigation', { name: 'Ambrose pages' })
  await expect(groups.getByRole('link', { name: 'Agents & API' })).toHaveCount(0)
  await expect(groups.getByRole('link', { name: 'Admin' })).toHaveCount(0)
  await expect(ambrose.getByRole('link', { name: 'Decisions lab' })).toHaveCount(0)
  await page.screenshot({ path: shot('access-family-models.png'), fullPage: false })

  // Learn from my choices (Mail → Labels): the simple levels, without the recency detail; blocked
  // addresses only.
  await page.goto('/settings/mail/labels')
  const mail = page.getByRole('navigation', { name: 'Mail pages' })
  await expect(mail.getByRole('link', { name: 'Blocked addresses' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Learn from my choices' })).toBeVisible()
  await expect(page.getByRole('radio', { name: /Balanced/ })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: /Favour recent labels/ })).toHaveCount(0)
  // The setting saves at once; the radio follows the saved settings.
  await page.getByRole('radio', { name: /Cautious/ }).click()
  await expect(page.getByRole('radio', { name: /Cautious/ })).toBeChecked()
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings?.auto_label_min_examples).toBe(50)

  // Advanced settings: the lab is on in this policy, API tokens are off.
  await page.getByRole('switch', { name: 'Advanced settings' }).click()
  await expect(page.getByRole('checkbox', { name: /Favour recent labels/ })).toBeVisible()
  await expect(mail.getByRole('link', { name: 'Filters & blocking' })).toBeVisible()
  await expect(groups.getByRole('link', { name: 'Agents & API' })).toHaveCount(0)
  await page.goto('/settings/ambrose/models')
  await expect(ambrose.getByRole('link', { name: 'Decisions lab' })).toBeVisible()
})

test('with the assistant off, the panel and Settings show the message', async ({ page, access, playwright, baseURL }) => {
  // The admin turns the assistant off for the family group.
  const admin = await playwright.request.newContext({ baseURL })
  const login = await admin.post('/api/v1/auth/login', { data: { email: access.admin.email, password: access.admin.password } })
  const headers = { 'X-CSRF-Token': (await login.json()).csrf }
  const overview = await (await admin.get('/api/v1/admin/access')).json()
  const pol = overview.policies.find((p: { group: string }) => p.group === access.group)
  const entry = { endpoint: access.endpoint, model: 'e2e-small' }
  const task = { any: false, allowed: [entry], default: entry }
  const r = await admin.put(`/api/v1/admin/access/policies/${pol.id}`, { headers, data: {
    group: access.group, description: pol.description, models: { chat: task, classify: task },
    features: { ...pol.features, assistant: false },
  } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await admin.dispose()

  await signIn(page, access.family)
  await page.goto('/settings/assistant')
  await expect(page.getByTestId('no-assistant')).toHaveText("Your administrator hasn't given you access to Ambrose yet.")
  await expect(page.getByRole('button', { name: 'Standing orders & routines' })).toHaveCount(0)
  await page.locator('[data-ambrose]:visible').click()
  const panel = page.locator('aside.side-panel')
  await expect(panel.getByTestId('no-assistant')).toBeVisible()
  await expect(panel.getByRole('textbox', { name: 'Message Ambrose' })).toHaveCount(0)
})
