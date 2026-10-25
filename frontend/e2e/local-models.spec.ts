// ACT-SURFACES: web.settings web.panel
// Where my data went: the household rule in Admin (settings.local_models_household), the
// person's "Local models only for me" switch and its lock (settings.local_models_me), the Model use
// table (settings.model_use), and the chip on each Ambrose answer (agent.answer_model). The answer
// comes from a scripted, streaming model this spec serves; the user is the setup guide's "family"
// user (mailr e2e-seed --onboarding) with the guide closed.
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { accessTest, backend, bareTest, expect, type Page, shot, signIn } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
bareTest.describe.configure({ mode: 'default' })

interface OnboardingSeed {
  endpoint: string
  user: { email: string; password: string; id: number }
}

let model: http.Server
let modelUrl = ''

bareTest.beforeAll(async () => {
  // An OpenAI-compatible endpoint: a streamed answer for the assistant, a plain one otherwise.
  model = http.createServer((req, res) => {
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      const b = JSON.parse(body || '{}') as { stream?: boolean }
      if (b.stream) {
        res.writeHead(200, { 'content-type': 'text/event-stream' })
        res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'Hello Rosa, your inbox is quiet today.' } }] })}\n\n`)
        res.write(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] })}\n\n`)
        res.end('data: [DONE]\n\n')
        return
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'Hello' }, finish_reason: 'stop' }] }))
    })
  })
  await new Promise<void>((r) => model.listen(0, '127.0.0.1', r))
  modelUrl = `http://127.0.0.1:${(model.address() as AddressInfo).port}/v1`
})

bareTest.afterAll(async () => {
  await new Promise((r) => model.close(r))
})

function seed(): OnboardingSeed {
  const out = backend(['e2e-seed', '--onboarding', '--model-url', modelUrl])
  return JSON.parse(out.trim().split('\n').pop()!) as OnboardingSeed
}

async function signInSeed(page: Page, s: OnboardingSeed) {
  const r = await page.request.post('/api/v1/auth/login', { data: { email: s.user.email, password: s.user.password } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const csrf = (await r.json()).csrf as string
  await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': csrf })
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  // Close the setup guide, so the inbox opens.
  const g = await page.request.put('/api/v1/onboarding', { data: { status: 'dismissed' }, headers: { 'X-CSRF-Token': csrf } })
  expect(g.ok(), await g.text()).toBeTruthy()
}

bareTest('each Ambrose answer names its model, and Model use counts it', async ({ page }) => {
  bareTest.setTimeout(180_000)
  const s = seed()
  await signInSeed(page, s)
  await page.goto('/')
  await page.locator('[data-ambrose]:visible').click()
  const panel = page.locator('aside.side-panel')
  const box = panel.getByRole('textbox', { name: 'Message Ambrose' })
  await box.fill('Anything new today?')
  await box.press('Enter')
  await expect(panel.getByText('Hello Rosa, your inbox is quiet today.')).toBeVisible({ timeout: 60_000 })
  // agent.answer_model: the chip beside the answer's actions, with the endpoint in its label.
  const chip = panel.getByTestId('answer-model')
  await expect(chip).toHaveText('Local · e2e-small')
  await expect(chip).toHaveAttribute('aria-label', 'Local model: e2e-small')
  await expect(chip).toHaveAttribute('data-tip', new RegExp(`on ${s.endpoint}: a local model`))
  await page.screenshot({ path: shot('answer-model-chip.png') })

  // settings.model_use: the answer is counted as Ambrose, on a local model.
  await page.goto('/settings/assistant?section=models')
  const use = page.getByTestId('model-use')
  await expect(use.getByRole('row').filter({ hasText: 'Ambrose' })).toContainText('Local')
  await expect(use.getByRole('row').filter({ hasText: 'Ambrose' })).toContainText('e2e-small')

  // settings.local_models_me: the person's own switch, saved at once.
  const sw = page.getByRole('switch', { name: 'Local models only for me' })
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  await expect.poll(async () => (await (await page.request.get('/api/v1/me/local-models')).json()).effective).toBe(true)
  await page.screenshot({ path: shot('local-models-me.png'), fullPage: true })
})

accessTest('local models only for everyone locks the switch on and says why', async ({ page, access, playwright, baseURL }) => {
  // settings.local_models_household: the admin chooses in Admin.
  await signIn(page, access.admin)
  await page.goto('/settings/admin/models')
  const rule = page.getByTestId('local-policy')
  await expect(rule.getByRole('radio', { name: /Each person chooses/ })).toBeChecked()
  try {
    await expect(rule.getByRole('radio')).toHaveCount(2)  // no choice locks anyone's switch off
    await rule.getByRole('radio', { name: /Local models only for everyone/ }).click()
    await expect(rule.getByRole('radio', { name: /Local models only for everyone/ })).toBeChecked()
    await expect.poll(async () => (await (await page.request.get('/api/v1/admin/models/local-policy')).json()).policy).toBe('local_all')

    // The family member's switch is locked, with the reason.
    const member = await page.context().browser()!.newContext({ baseURL })
    const mp = await member.newPage()
    await signIn(mp, access.family)
    await mp.goto('/settings/assistant?section=models')
    const sw = mp.getByRole('switch', { name: 'Local models only for me' })
    await expect(sw).toBeDisabled()
    await expect(sw).toHaveAttribute('aria-checked', 'true')
    await expect(mp.getByTestId('local-models-reason')).toHaveText(/Your administrator set local models only for everyone/)
    await member.close()
  } finally {
    const admin = await playwright.request.newContext({ baseURL })
    const login = await admin.post('/api/v1/auth/login', { data: { email: access.admin.email, password: access.admin.password } })
    await admin.put('/api/v1/admin/models/local-policy', { headers: { 'X-CSRF-Token': (await login.json()).csrf }, data: { policy: 'per_user' } })
    await admin.dispose()
  }
})
