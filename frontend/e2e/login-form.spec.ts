// The sign-in page itself (src/features/auth/LoginPage.tsx). Every other spec signs in through
// POST /auth/login; these tests start signed out and use the form: sign-in and the page asked for
// before it, a wrong password, the lockout message, Sign out, a session that ends on the server
// (unsent text comes back after the next sign-in, as site/docs/user-guide/reading.md says), and the
// single sign-on button when an identity provider is configured (no real provider: only where the
// button leads). The page has no theme of its own (login.css), so one look runs.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const form = (page: Page) => page.locator('.login-card')
const alert = (page: Page) => form(page).getByRole('alert')

/** The page fixture signed in through the API; these tests start without that session. */
async function signedOut(page: Page) {
  await page.context().clearCookies()
}

async function fillAndSubmit(page: Page, email: string, password: string) {
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

const listRows = (page: Page) => page.locator('[data-mail-list] [role="row"]')

test('sign-in with the password lands on the page asked for', async ({ page, seeded }) => {
  await signedOut(page)
  await page.goto('/tasks')
  await expect(page).toHaveURL(/\/login\?next=%2Ftasks/)
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  // The button waits for both fields.
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeDisabled()
  await fillAndSubmit(page, `  ${seeded.email}  `, seeded.password)
  await expect(page).toHaveURL(/\/tasks/)

  // A plain /login goes to the inbox.
  await signedOut(page)
  await page.goto('/login')
  await fillAndSubmit(page, seeded.email, seeded.password)
  await expect(page).toHaveURL(/\/mail\/inbox/)
  await expect(listRows(page).first()).toBeVisible()
})

test('a wrong password says so and keeps the form; the right one then signs in', async ({ page, seeded }) => {
  await signedOut(page)
  await page.goto('/login')
  await fillAndSubmit(page, seeded.email, `${seeded.password}-wrong`)
  await expect(alert(page)).toHaveText('Wrong email or password.')
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByLabel('Email')).toHaveValue(seeded.email)

  await page.getByLabel('Password').fill(seeded.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/mail\/inbox/)
  await expect(alert(page)).toHaveCount(0)
})

test('too many attempts: the lockout message is shown', async ({ page, seeded }) => {
  // The real limit counts per address and per IP, and every test signs in from localhost: ten
  // failures here would lock out the tests beside it. The server's answer is stood in for.
  await signedOut(page)
  await page.route('**/api/v1/auth/login', (r) => r.fulfill({ status: 429, contentType: 'application/json',
    body: JSON.stringify({ detail: 'too many attempts, try again later' }) }))
  await page.goto('/login')
  await fillAndSubmit(page, seeded.email, seeded.password)
  await expect(alert(page)).toHaveText('too many attempts, try again later')
  await expect(page).toHaveURL(/\/login/)
})

test('Sign out ends the session: the sign-in page, and the app stays closed after a reload', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(listRows(page).first()).toBeVisible()
  await page.getByTestId('account-btn').click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  expect((await (await page.request.get('/api/v1/auth/session')).json()).authenticated).toBe(false)
  await page.goto('/mail/inbox')
  await expect(page).toHaveURL(/\/login/)
})

test('a session ended elsewhere: the sign-in page, and the unsent text comes back after signing in', async ({ page, seeded, browser }) => {
  await page.clock.install()
  await page.goto('/mail/inbox?compose=1')
  const win = page.locator('.compose-win')
  await expect(win).toBeVisible()
  await win.getByLabel('To recipients').fill('pat@example.net')
  await page.keyboard.press('Enter')
  await win.getByLabel('Subject').fill('E2E session ended')
  await win.locator('.ProseMirror').click()
  await page.keyboard.type('Written before the session ended.')

  // Another device signs in and ends this browser's session (Settings, Security).
  const other = await browser.newContext({ baseURL: new URL(page.url()).origin })
  const r = await other.request.post('/api/v1/auth/login', { data: { email: seeded.email, password: seeded.password } })
  expect(r.ok()).toBeTruthy()
  const csrf = (await r.json()).csrf as string
  const list = (await (await other.request.get('/api/v1/me/sessions')).json()) as { id: string; current: boolean }[]
  for (const s of list.filter((x) => !x.current)) {
    const d = await other.request.delete(`/api/v1/me/sessions/${s.id}`, { headers: { 'X-CSRF-Token': csrf } })
    expect(d.ok(), await d.text()).toBeTruthy()
  }
  await other.close()

  // The next session check (on returning to the tab) finds it ended.
  await page.clock.fastForward('01:30')
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')))
  await expect(page).toHaveURL(/\/login/)

  await fillAndSubmit(page, seeded.email, seeded.password)
  await expect(page).toHaveURL(/\/mail\/inbox/)
  const back = page.locator('.compose-win')
  await expect(back).toHaveCount(1)
  await expect(back.getByText('Restored unsent draft')).toBeVisible()
  await expect(back.getByLabel('Subject')).toHaveValue('E2E session ended')
  await expect(back.locator('.ProseMirror')).toContainText('Written before the session ended.')
})

test('single sign-on configured: the button leads to the server\'s start of the sign-in', async ({ page }) => {
  await signedOut(page)
  // The test server has no identity provider; its configuration answer is stood in for.
  await page.route('**/api/v1/auth/config', async (r) => {
    const real = await (await r.fetch()).json()
    await r.fulfill({ json: { ...real, oidc: true, oidc_name: 'id.example.test', local_login: true } })
  })
  let started: URL | null = null
  await page.route('**/api/v1/auth/oidc/start**', (r) => {
    started = new URL(r.request().url())
    return r.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>provider</title>' })
  })
  await page.goto('/login?next=%2Fcalendar')
  const sso = page.getByRole('link', { name: 'Continue with id.example.test' })
  await expect(sso).toBeVisible()
  await expect(page.getByText('or use a password')).toBeVisible()
  await expect(page.getByText('secured by id.example.test')).toBeVisible()
  // The password form stays beside it while local sign-in is on.
  await expect(page.getByLabel('Password')).toBeVisible()
  await sso.click()
  await expect.poll(() => started?.pathname).toBe('/api/v1/auth/oidc/start')
  expect(started!.searchParams.get('next')).toBe('/calendar')
})

test('single sign-on only: no password form', async ({ page }) => {
  await signedOut(page)
  await page.route('**/api/v1/auth/config', async (r) => {
    const real = await (await r.fetch()).json()
    await r.fulfill({ json: { ...real, oidc: true, oidc_name: 'id.example.test', local_login: false } })
  })
  await page.goto('/login')
  await expect(page.getByRole('link', { name: 'Continue with id.example.test' })).toHaveAttribute('href', /\/api\/v1\/auth\/oidc\/start\?next=%2Fmail%2Finbox$/)
  await expect(page.getByLabel('Password')).toHaveCount(0)
  await expect(page.getByText('or use a password')).toHaveCount(0)
})
