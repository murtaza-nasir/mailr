// ACT-SURFACES: web.setup web.settings
// The setup guide on first sign-in, as a new "family" user (mailr e2e-seed --onboarding), with the
// assistant's chat model replaced by a scripted model this spec serves (its replies are fixed).
// The whole flow runs in Classic and in Cards, on a desktop and on a phone-sized screen.
// Covers notification.subscribe on web.setup (the notifications step), note.from_template and
// note.create on web.setup (the notes card on the last screen) and settings.setup_guide on
// web.settings (reopening the guide).
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { expect, type Page } from '@playwright/test'
import { backend, bareTest as test } from './fixtures'

interface OnboardingSeed {
  group: string
  endpoint: string
  user: { email: string; password: string; id: number }
  upstream: { email: string; password: string; imap: { host: string; port: number } } | null
}

const QUESTIONS = [
  'Lovely! How do you like to work, and what would you like help with?',
  'That makes sense. How do your emails usually sound: warm, short, formal?',
  'And how do you sign off?',
  'Anything I should know, or avoid doing?',
  'Thank you, that is everything I need!\n[[DONE]]',
]
const SUMMARY = {
  preferred_name: 'Rosa', role: 'Primary school teacher', about: 'I teach year 3 and run our family paperwork.',
  writing: 'Warm and short. Start with Hi and the name.', signoff_name: 'Rosa', signature: 'Love,\nRosa', languages: 'English',
  memories: [{ text: 'Never schedule anything on Sunday mornings', kind: 'preference' }, { text: 'Sam is my husband', kind: 'relationship' }],
}

let model: http.Server
let modelUrl = ''
const calls: { system: string; users: number }[] = []

test.beforeAll(async () => {
  // An OpenAI-compatible endpoint with scripted replies: the next question, or the summary.
  model = http.createServer((req, res) => {
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      const b = JSON.parse(body || '{}') as { messages: { role: string; content: string }[] }
      const system = b.messages?.[0]?.content ?? ''
      const users = (b.messages ?? []).filter((m) => m.role === 'user').length
      calls.push({ system, users })
      const content = system.includes('ONE JSON object') ? JSON.stringify(SUMMARY) : QUESTIONS[Math.min(users - 1, QUESTIONS.length - 1)]
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content }, finish_reason: 'stop' }] }))
    })
  })
  await new Promise<void>((r) => model.listen(0, '127.0.0.1', r))
  modelUrl = `http://127.0.0.1:${(model.address() as AddressInfo).port}/v1`
})

test.afterAll(async () => {
  await new Promise((r) => model.close(r))
})

function seed(): OnboardingSeed {
  const out = backend(['e2e-seed', '--onboarding', '--model-url', modelUrl])
  return JSON.parse(out.trim().split('\n').pop()!) as OnboardingSeed
}

async function signIn(page: Page, s: OnboardingSeed, theme: string) {
  const r = await page.request.post('/api/v1/auth/login', { data: { email: s.user.email, password: s.user.password } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': (await r.json()).csrf })
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
}

const next = (page: Page, name = 'Continue') => page.locator('.ob-foot').getByRole('button', { name, exact: true }).click()
const onboarding = async (page: Page) => (await (await page.request.get('/api/v1/onboarding')).json()) as { status: string; step: string; steps: Record<string, string> }

for (const theme of ['classic', 'cards'] as const) {
  for (const vp of ['desktop', 'phone'] as const) {
    test(`a new family user goes through setup (${theme}, ${vp})`, async ({ page, context }) => {
      test.setTimeout(240_000)
      if (vp === 'phone') await page.setViewportSize({ width: 390, height: 844 })
      const s = seed()
      await signIn(page, s, theme)
      await context.grantPermissions(['notifications'])

      // First sign-in: the guide opens by itself.
      await page.goto('/')
      await expect(page).toHaveURL(/\/setup\/welcome$/)
      await expect(page.getByRole('heading', { name: 'Welcome to Mailr, Rosa' })).toBeVisible()
      await expect(page.getByText('Step 1 of 8')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme)
      await next(page, 'Start')

      // Connect your email: Gmail gets the app-password help; the dev upstream really connects.
      await expect(page).toHaveURL(/\/setup\/accounts$/)
      await page.getByLabel('Your email address').fill('rosa.lind@gmail.com')
      await page.getByRole('button', { name: 'Next', exact: true }).click()
      const guide = page.getByTestId('password-guide')
      await expect(guide).toBeVisible({ timeout: 30_000 })
      await expect(guide).toContainText('2-Step Verification')
      await expect(guide.getByRole('link', { name: /Open App passwords/ })).toHaveAttribute('href', 'https://myaccount.google.com/apppasswords')
      // The old account: mirror is the default and says that changes go back.
      await expect(page.getByRole('radio', { name: 'Keep in sync' })).toHaveAttribute('aria-checked', 'true')
      await expect(page.getByTestId('ob-link')).toContainText('also change the mail at your old account')
      await page.getByRole('button', { name: 'Change' }).click()
      // Microsoft accounts are named as not supported.
      await page.getByLabel('Your email address').fill('rosa.lind@hotmail.com')
      await page.getByRole('button', { name: 'Next', exact: true }).click()
      await expect(page.getByText("Outlook, Hotmail, Live and MSN accounts can't be connected yet.")).toBeVisible({ timeout: 30_000 })
      await page.getByRole('button', { name: 'Change' }).click()
      if (s.upstream) {
        await page.getByLabel('Your email address').fill(s.upstream.email)
        await page.getByRole('button', { name: 'Next', exact: true }).click()
        await expect(page.getByLabel('Your email password')).toBeVisible({ timeout: 60_000 })
        await expect(page.getByRole('radio', { name: 'Start with the last 3 months' })).toHaveAttribute('aria-checked', 'true')
        const servers = page.locator('details.ob-servers')
        if (!(await servers.getAttribute('open'))) await servers.locator('summary').click()
        await page.getByRole('textbox', { name: 'Incoming (IMAP) server' }).fill(s.upstream.imap.host)
        await servers.getByRole('textbox').nth(1).fill(String(s.upstream.imap.port))
        await servers.getByRole('combobox').first().selectOption('none')
        await page.getByRole('textbox', { name: 'Outgoing (SMTP) server' }).fill('')
        await page.getByLabel('Your email password').fill(s.upstream.password)
        await page.getByRole('button', { name: 'Connect', exact: true }).click()
        await expect(page.getByTestId('ob-account')).toContainText(s.upstream.email, { timeout: 30_000 })
        await expect(page.getByRole('button', { name: 'Add another account' })).toBeVisible()
        const conns = (await (await page.request.get('/api/v1/connectors')).json()) as { config: { since?: string; backfill?: boolean }; progress: { backfill?: unknown } }[]
        expect(conns[0].config.backfill).toBe(true)
        expect(conns[0].config.since).toMatch(/^\d{4}-\d{2}-\d{2}$/)
        expect(conns[0].progress.backfill).toBeTruthy()
        await next(page)
      } else {
        await next(page, 'Skip for now')
      }

      // Choose your look: applies at once.
      await expect(page).toHaveURL(/\/setup\/look$/)
      await page.getByTestId(`ob-theme-${theme}`).click()
      await page.getByRole('radio', { name: 'Dark' }).click()
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
      await page.getByRole('radio', { name: 'Light' }).click()
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
      await page.getByRole('radio', { name: 'Roomy' }).click()
      await next(page)

      // Inbox style: previews with her own mail.
      await expect(page).toHaveURL(/\/setup\/inbox$/)
      await expect(page.getByTestId('ob-own-preview')).toBeVisible()
      await expect(page.getByTestId('ob-style-simple')).toContainText('Dinner on Sunday?')
      await page.getByTestId('ob-style-simple').click()
      await expect(page.getByTestId('ob-style-simple')).toHaveAttribute('aria-checked', 'true')
      await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.inbox_type).toBe('simple')
      await next(page)

      // Your phone: a QR code to the Get the app page for Android, Home Screen steps for iPhone.
      await expect(page).toHaveURL(/\/setup\/phone$/)
      await page.getByRole('radio', { name: 'Android' }).click()
      await expect(page.getByRole('img', { name: 'QR code to get the Mailr app' })).toHaveAttribute('data-value', /\/get-app$/)
      await expect(page.getByTestId('install-android')).toBeVisible()
      // The Notes app is offered too (optional); on an iPhone, notes are in the web app.
      await expect(page.getByTestId('install-notes-app')).toContainText('Notes app (optional)')
      await page.getByRole('radio', { name: 'iPhone' }).click()
      await expect(page.getByTestId('install-ios')).toContainText('Add to Home Screen')
      await expect(page.getByTestId('ios-notes')).toContainText('iPhone has no separate Notes app.')
      await next(page)

      // Calendar: optional.
      await expect(page).toHaveURL(/\/setup\/calendar$/)
      await next(page, 'Skip for now')

      // Notifications: permission after a click, a test, and what notifies her.
      // ACT notification.subscribe
      await expect(page).toHaveURL(/\/setup\/notifications$/)
      await expect(page.getByTestId('ob-permission')).toContainText('Notifications are on for this browser.')
      await page.getByRole('button', { name: 'Send me a test notification' }).click()
      await page.getByRole('radio', { name: 'Important mail' }).click()
      await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).settings.mail_notifications).toBe('important')
      await next(page)

      // About you: the quick chat, one question at a time; nothing saved until Save.
      await expect(page).toHaveURL(/\/setup\/about$/)
      await expect(page.getByRole('radio', { name: 'Have a quick chat' })).toHaveAttribute('aria-checked', 'true')
      await next(page, 'Start')
      const chat = page.getByTestId('quick-chat')
      await expect(chat).toContainText('Hi Rosa!')
      for (const [i, a] of ['I teach year 3.', 'Help me keep up with school mail.', 'Warm and short.', 'Love, Rosa', 'Never Sunday mornings.'].entries()) {
        await page.getByRole('textbox', { name: 'Your answer' }).fill(a)
        await page.getByRole('button', { name: 'Send' }).click()
        await expect(chat.locator('.ob-bubble.assistant').nth(i + 1)).toHaveText(QUESTIONS[i].replace('\n[[DONE]]', ''))
      }
      const before = (await (await page.request.get('/api/v1/assistant/profile')).json()) as { role: string }
      expect(before.role).toBe('')
      await page.getByRole('button', { name: 'Show summary' }).click()
      const review = page.getByTestId('about-review')
      await expect(review.getByLabel('Occupation')).toHaveValue('Primary school teacher')
      await expect(review.getByLabel('Sign-off (also your signature)')).toHaveValue('Love,\nRosa')
      expect((await (await page.request.get('/api/v1/assistant/profile')).json()).role).toBe('')
      await review.getByLabel('Occupation').fill('Teacher (year 3)')
      await review.getByRole('button', { name: 'Leave out note 2' }).click()
      await next(page, 'Save')
      await expect(page.getByTestId('about-saved')).toBeVisible()
      // Back on the step (a reload here), it still says Saved, not the chooser.
      await page.reload()
      await expect(page).toHaveURL(/\/setup\/about$/)
      await expect(page.getByTestId('about-saved')).toBeVisible()
      await expect(page.getByRole('radio', { name: 'Have a quick chat' })).toHaveCount(0)
      const prof = (await (await page.request.get('/api/v1/assistant/profile')).json()) as { role: string; signoff_name: string; writing: string }
      expect(prof.role).toBe('Teacher (year 3)')
      expect(prof.signoff_name).toBe('Rosa')
      const mems = (await (await page.request.get('/api/v1/assistant/memories')).json()) as { items?: { text: string }[] } | { text: string }[]
      const list = Array.isArray(mems) ? mems : (mems.items ?? [])
      expect(list.map((m) => m.text)).toEqual(['Never schedule anything on Sunday mornings'])
      // Her policy's model was used for every turn and the summary.
      expect(calls.some((c) => c.system.includes('ONE JSON object'))).toBe(true)
      await next(page)

      // Done: the five-tip tour, then the inbox.
      await expect(page).toHaveURL(/\/setup\/done$/)
      await expect(page.getByRole('heading', { name: "You're all set, Rosa" })).toBeVisible()
      // Notes and lists: one tap to a shopping list or a note; on a phone-sized screen the Notes app
      // (Android) and the Home Screen (iPhone) as well.
      const notesIntro = page.getByTestId('notes-intro')
      await expect(notesIntro.getByRole('button', { name: 'Start a shopping list' })).toBeVisible()
      await expect(notesIntro.getByRole('button', { name: 'Start a note' })).toBeVisible()
      await expect(page.getByTestId('notes-intro-android')).toHaveCount(vp === 'phone' ? 1 : 0)
      await expect(page.getByTestId('notes-intro-ios')).toHaveCount(vp === 'phone' ? 1 : 0)
      await next(page, 'Show me around')
      for (let i = 0; i < 4; i++) await next(page, 'Next tip')
      await next(page, 'Go to my inbox')
      await expect(page).toHaveURL(/\/mail\/inbox/)
      await expect.poll(async () => (await onboarding(page)).status).toBe('done')
      await expect(page.getByTestId('setup-card')).toHaveCount(0)
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme)
      await page.screenshot({ path: test.info().outputPath(`onboarding-${theme}-${vp}-inbox.png`) })
    })
  }
}

test('Finish later keeps the place; the card and Settings reopen the guide', async ({ page }) => {
  test.setTimeout(180_000)
  const s = seed()
  await signIn(page, s, 'classic')
  await page.goto('/setup')
  await next(page, 'Start')
  await next(page, 'Skip for now')
  await expect(page).toHaveURL(/\/setup\/look$/)
  await page.getByRole('button', { name: 'Finish later' }).click()
  await expect(page).toHaveURL(/\/mail\/inbox/)
  const card = page.getByTestId('setup-card')
  await expect(card).toContainText('Continue setting up Mailr')
  await expect(card).toContainText('2 of 8 steps done')
  expect(await onboarding(page)).toMatchObject({ status: 'active', step: 'look', steps: { welcome: 'done', accounts: 'skipped' } })
  // A reload does not reopen the guide by itself.
  await page.reload()
  await expect(page).toHaveURL(/\/mail\/inbox/)
  await card.getByRole('link', { name: /Continue setup/ }).click()
  await expect(page).toHaveURL(/\/setup\/look$/)
  await page.getByRole('button', { name: 'Finish later' }).click()
  // Hide the card; Settings → General → Setup guide opens the guide again at the start.
  await page.getByRole('button', { name: 'Hide the setup reminder' }).click()
  await expect(card).toHaveCount(0)
  // ACT settings.setup_guide
  await page.goto('/settings/you')
  await page.getByRole('button', { name: 'Open setup guide' }).click()
  await expect(page).toHaveURL(/\/setup\/welcome$/)
  expect((await onboarding(page)).status).toBe('active')
})

test('the last screen starts a shopping list with store sections and finishes the guide', async ({ page }) => {
  test.setTimeout(120_000)
  const s = seed()
  await signIn(page, s, 'classic')
  await page.goto('/setup/done')
  // ACT note.from_template
  await page.getByTestId('notes-intro').getByRole('button', { name: 'Start a shopping list' }).click()
  await expect(page).toHaveURL(/\/notes\/n\/[^/]+$/)
  await expect.poll(async () => (await onboarding(page)).status).toBe('done')
  const id = decodeURIComponent(new URL(page.url()).pathname.split('/').pop()!)
  const note = (await (await page.request.get(`/api/v1/notes/${encodeURIComponent(id)}`)).json()) as { note?: { kind: string; shopping: boolean; sections?: { name: string }[] }; kind?: string; shopping?: boolean; sections?: { name: string }[] }
  const n = note.note ?? note
  expect(n.kind).toBe('checklist')
  expect(n.shopping).toBe(true)
  expect((n.sections ?? []).map((x) => x.name)).toContain('Produce')
})

test('the last screen starts a blank note; a preset can leave notes out', async ({ page }) => {
  test.setTimeout(120_000)
  const s = seed()
  await signIn(page, s, 'cards')
  // The preset's offer as every answer of the guide carries it (GET, and the PUT that opens it).
  // A request still in this handler when the test unroutes it is already handled by then; that is fine.
  await page.route('**/api/v1/onboarding', async (route) => {
    try {
      const r = await route.fetch()
      const body = await r.json()
      await route.fulfill({ response: r, json: { ...body, offer: { ...body.offer, notes: false } } })
    } catch {
      /* unrouted mid-request (the reload below) */
    }
  })
  await page.goto('/setup/done')
  await expect(page.getByRole('heading', { name: /all set/ })).toBeVisible()
  await expect(page.getByTestId('notes-intro')).toHaveCount(0)
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await page.reload()
  // ACT note.create
  await page.getByTestId('notes-intro').getByRole('button', { name: 'Start a note' }).click()
  await expect(page).toHaveURL(/\/notes\/n\/[^/]+$/)
  await expect.poll(async () => (await onboarding(page)).status).toBe('done')
})

test('the Get the app page needs no sign-in and opens the app with the server filled in', async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => Object.defineProperty(navigator, 'userAgent', { get: () => 'Mozilla/5.0 (Linux; Android 14; Pixel 8)' }))
  await page.goto('/get-app')
  await expect(page.getByRole('heading', { name: 'Get Mailr on your phone' })).toBeVisible()
  const origin = new URL(baseURL!).origin
  // The server's own answer: the release page when it holds no APK, its own download when it does.
  const real = (await (await page.request.get('/api/v1/app/releases')).json()) as { apps: { app: string; url: string }[] }
  const mailrUrl = real.apps.find((a) => a.app === 'mailr')!.url
  await expect(page.getByRole('link', { name: /Download the Mailr app/ })).toHaveAttribute('href', mailrUrl)
  await expect(page.getByRole('link', { name: /Open Mailr, ready to sign in/ })).toHaveAttribute('href', `mailr://setup?server=${encodeURIComponent(origin)}`)
  await expect(page.getByRole('link', { name: /Open Notes, ready to sign in/ })).toHaveAttribute('href', `mailr-notes://setup?server=${encodeURIComponent(origin)}`)
})

test('the Get the app page shows both apps with their version and checksum', async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => Object.defineProperty(navigator, 'userAgent', { get: () => 'Mozilla/5.0 (Linux; Android 14; Pixel 8)' }))
  const origin = new URL(baseURL!).origin
  const rel = (app: 'mailr' | 'notes', v: string, code: number, size: number, sha: string) => ({
    app, name: app === 'mailr' ? 'Mailr' : 'Mailr Notes', package: '', url: `${origin}/api/v1/app/${app}.apk`, on_server: true,
    version_name: v, version_code: code, size, sha256: sha, published_at: '2026-10-05T18:00:00Z',
  })
  await page.route('**/api/v1/app/releases', (route) => route.fulfill({ json: { apps: [rel('mailr', '0.6.2', 37, 10_453_670, 'a'.repeat(64)), rel('notes', '1.0.2', 3, 5_904_946, 'b'.repeat(64))] } }))
  await page.goto('/get-app')
  await expect(page.getByRole('link', { name: /Download the Mailr app/ })).toHaveAttribute('href', `${origin}/api/v1/app/mailr.apk`)
  await expect(page.getByTestId('app-version-mailr')).toContainText('Version 0.6.2 · 10 MB')
  const notes = page.getByTestId('install-notes-app')
  await expect(notes.getByRole('link', { name: /Download Mailr Notes/ })).toHaveAttribute('href', `${origin}/api/v1/app/notes.apk`)
  await expect(notes.getByTestId('app-version-notes')).toContainText('Version 1.0.2 · 5.6 MB')
  await notes.getByText('SHA-256 checksum').click()
  await expect(notes.getByTestId('app-version-notes')).toContainText('b'.repeat(64))
})

test('the calendar step offers the prepared calendar server and connects more than one calendar', async ({ page }) => {
  test.setTimeout(180_000)
  const s = seed()
  await signIn(page, s, 'classic')
  // The admin prepared a calendar server; she already connected one calendar elsewhere.
  await page.route('**/api/v1/onboarding', async (route) => {
    const r = await route.fetch()
    const body = await r.json()
    await route.fulfill({ response: r, json: { ...body, offer: { ...body.offer, calendar_url: 'https://cal.example.com/dav/' } } })
  })
  await page.route('**/api/v1/connectors', async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const r = await route.fetch()
    const list = await r.json()
    await route.fulfill({ response: r, json: [...list, { id: 9001, kind: 'caldav', name: 'Fastmail', config: { url: 'https://caldav.fastmail.example/' }, enabled: true, status: 'idle' }] })
  })
  await page.goto('/setup/calendar')
  await expect(page.getByRole('heading', { name: 'Your calendars' })).toBeVisible()
  await expect(page.getByRole('list', { name: 'Connected calendars' })).toContainText('Fastmail')
  await page.getByRole('button', { name: 'Add another calendar' }).click()
  const server = page.getByRole('radio', { name: 'Use our calendar server' })
  await expect(server).toBeChecked()
  await expect(server).toContainText('cal.example.com')
  await next(page, 'Connect')
  const dialog = page.getByRole('dialog', { name: 'Connect a calendar' })
  await expect(dialog.getByLabel('Calendar server address')).toHaveValue('https://cal.example.com/dav/')
})

test('the name starts empty when the account has only its address local part', async ({ page }) => {
  const s = seed()
  await signIn(page, s, 'classic')
  const local = s.user.email.split('@')[0]
  expect((await page.request.patch('/api/v1/me', { data: { name: local } })).ok()).toBeTruthy()
  await page.goto('/setup')
  await expect(page).toHaveURL(/\/setup\/welcome$/)
  const name = page.getByLabel('Your name')
  await expect(name).toHaveValue('')
  await expect(page.getByRole('heading', { name: 'Welcome to Mailr', exact: true })).toBeVisible()
  const start = page.locator('.ob-foot').getByRole('button', { name: 'Start', exact: true })
  await expect(start).toBeDisabled()
  await name.fill('Rosa Lind')
  await start.click()
  await expect(page).toHaveURL(/\/setup\/accounts$/)
  await expect.poll(async () => (await (await page.request.get('/api/v1/me')).json()).user.name).toBe('Rosa Lind')
})
