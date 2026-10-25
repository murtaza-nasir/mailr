// ACT-SURFACES: web.settings
// settings.phone_apps: Settings → Admin → Phone apps. The APKs are instance-wide (one server for all
// parallel specs), so the admin routes are answered by an in-memory stand-in here;
// tests/test_phone_apps.py covers the real routes, reading the version from the APK, the package
// check and the downloads.
import type { Page, Route } from '@playwright/test'
import { accessTest as test, expect, signIn } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

const SHA_A = 'a'.repeat(64)
const SHA_B = 'b'.repeat(64)

function release(app: 'mailr' | 'notes', v: { name: string; code: number; sha: string } | null) {
  return {
    app, name: app === 'mailr' ? 'Mailr' : 'Mailr Notes', package: app === 'mailr' ? 'io.github.murtazanasir.mailr' : 'io.github.murtazanasir.mailr.notes',
    url: v ? `http://localhost/api/v1/app/${app}.apk` : 'https://github.com/murtaza-nasir/mailr/releases/latest',
    on_server: !!v, version_name: v?.name ?? null, version_code: v?.code ?? null, size: v ? 5_904_946 : null,
    sha256: v?.sha ?? null, published_at: v ? '2026-10-05T18:00:00Z' : null,
  }
}

async function mockApps(page: Page) {
  const state = {
    mailr: { cur: { name: '0.6.2', code: 37, sha: SHA_A }, prev: null as null | { name: string; code: number; sha: string } },
    notes: { cur: null as null | { name: string; code: number; sha: string }, prev: null as null | { name: string; code: number; sha: string } },
  }
  const uploads: { app: string; type: string; body: string }[] = []
  const out = () => ({
    apps: (['mailr', 'notes'] as const).map((a) => ({ ...release(a, state[a].cur), previous: state[a].prev ? { version_name: state[a].prev!.name, version_code: state[a].prev!.code } : null })),
    max_bytes: 300 * 1024 * 1024,
  })
  await page.route('**/api/v1/admin/phone-apps**', async (route: Route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname.replace('/api/v1/admin/phone-apps', '')
    if (req.method() === 'GET' && path === '') return route.fulfill({ json: out() })
    const m = /^\/(mailr|notes)(\/rollback)?$/.exec(path)
    if (!m || req.method() !== 'POST') return route.fallback()
    const app = m[1] as 'mailr' | 'notes'
    if (m[2]) {
      const s = state[app]
      ;[s.cur, s.prev] = [s.prev, s.cur]
      return route.fulfill({ json: out() })
    }
    const body = req.postDataBuffer()?.toString('latin1') ?? ''
    uploads.push({ app, type: req.headers()['content-type'] ?? '', body })
    if (body.includes('wrong-app')) return route.fulfill({ status: 400, json: { detail: 'this APK is io.github.murtazanasir.mailr, not the Mailr Notes app (io.github.murtazanasir.mailr.notes)' } })
    state[app].prev = state[app].cur
    state[app].cur = { name: '1.0.2', code: 3, sha: SHA_B }
    return route.fulfill({ json: { ...out(), published: app, unchanged: false } })
  })
  return { uploads }
}

test('an admin publishes the Notes app and goes back to the previous Mailr app', async ({ page, access }) => {
  await signIn(page, access.admin)
  const { uploads } = await mockApps(page)
  await page.goto('/settings/admin/phones')
  const section = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Phone apps', exact: true }) })
  await expect(section.getByTestId('phone-app-mailr-current')).toContainText('Phones download version 0.6.2 (37)')
  await expect(section.getByTestId('phone-app-mailr-current')).toContainText(SHA_A)
  await expect(section.getByTestId('phone-app-notes')).toContainText('This server has no APK of it.')

  // An APK of the wrong app: the reason shows and nothing changes.
  const notes = section.getByTestId('phone-app-notes')
  await notes.getByLabel('Release APK of the Notes app').setInputFiles({ name: 'app-release.apk', mimeType: 'application/vnd.android.package-archive', buffer: Buffer.from('PK wrong-app') })
  await expect(page.getByText(/not the Mailr Notes app/)).toBeVisible()
  await expect(notes).toContainText('This server has no APK of it.')

  // The right one: published, with its version and checksum.
  await notes.getByLabel('Release APK of the Notes app').setInputFiles({ name: 'notes-release.apk', mimeType: 'application/vnd.android.package-archive', buffer: Buffer.from('PK notes') })
  await expect(section.getByTestId('phone-app-notes-current')).toContainText('Phones download version 1.0.2 (3)')
  await expect(section.getByTestId('phone-app-notes-current')).toContainText(SHA_B)
  expect(uploads.at(-1)!.type).toMatch(/^multipart\/form-data/)
  expect(uploads.at(-1)!.body).toContain('filename="notes-release.apk"')

  // ACT settings.phone_apps: publish over mailr, then go back to the version before it.
  const mailr = section.getByTestId('phone-app-mailr')
  await mailr.getByLabel('Release APK of the Mailr app').setInputFiles({ name: 'app-release.apk', mimeType: 'application/vnd.android.package-archive', buffer: Buffer.from('PK mailr') })
  await expect(section.getByTestId('phone-app-mailr-previous')).toContainText('Kept as the fallback: 0.6.2 (37)')
  await mailr.getByRole('button', { name: 'Go back to 0.6.2' }).click()
  await page.getByRole('dialog', { name: /Go back to Mailr 0.6.2/ }).getByRole('button', { name: 'Go back' }).click()
  await expect(section.getByTestId('phone-app-mailr-current')).toContainText('Phones download version 0.6.2 (37)')
})

test('Phone apps shows what members do for push and where phones push through', async ({ page, access }) => {
  await signIn(page, access.admin)
  // ACT admin.push_setup: counts only, from GET /admin/push-setup (answered here: the counts are instance-wide).
  await page.route('**/api/v1/admin/push-setup', (route) => route.fulfill({ json: {
    server: { url: 'https://ntfy.example.com', host: 'ntfy.example.com', secure: true, app: 'ntfy' },
    steps: ['install_ntfy', 'set_default_server'],
    people: { phones: 3, push: 2, no_push: 1 },
    servers: [{ host: 'ntfy.example.com', people: 1, own: true }, { host: 'ntfy.sh', people: 1, own: false }],
  } }))
  await page.goto('/settings/admin/phones')
  const panel = page.getByTestId('push-setup')
  await expect(panel.getByTestId('push-steps')).toContainText('Copy and open ntfy')
  await expect(panel.getByTestId('push-steps')).toContainText('https://ntfy.example.com')
  await expect(panel.getByTestId('push-steps')).toContainText('Live connection')
  await expect(panel.getByTestId('push-phones')).toHaveText('3 people')
  await expect(panel.getByRole('row', { name: /ntfy.example.com \(your server\)/ })).toContainText('1 person')
  await expect(panel.getByTestId('push-none')).toHaveText('1 person')
})

// ACT settings.notes_min_version: the Notes app's oldest version, on the real route. A wrong value is
// refused in place; a right one saves on Enter with Undo; the mailr app's limit is read-only.
test('an admin sets the oldest Notes app version and undoes it', async ({ page, access }) => {
  await signIn(page, access.admin)
  await page.goto('/settings/admin/phones')
  const section = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Phone apps', exact: true }) })
  await expect(section.getByTestId('phone-app-mailr-floor')).toContainText('MAILR_ANDROID_MIN_VERSION')
  const field = section.getByLabel('Oldest accepted version')
  await expect(field).toHaveValue('')
  await field.fill('1.1.x')
  await field.press('Enter')
  await expect(section.getByText('Numbers with dots, such as 1.1.0.')).toBeVisible()
  await expect(field).toHaveAttribute('aria-invalid', 'true')

  const saved = page.waitForResponse((r) => r.url().endsWith('/admin/phone-apps/notes/min-version') && r.request().method() === 'PUT')
  await field.fill('1.1.0')
  await field.press('Enter')
  expect((await saved).status()).toBe(200)
  await expect(page.getByText('Notes apps older than 1.1.0 must now update.')).toBeVisible()
  // An older Notes app is now refused with 426; a newer one is answered.
  const old = await page.request.get('/api/v1/me', { headers: { 'User-Agent': 'mailr-android-notes/1.0.0' } })
  expect(old.status()).toBe(426)
  expect((await page.request.get('/api/v1/me', { headers: { 'User-Agent': 'mailr-android-notes/1.1.0' } })).status()).toBe(200)

  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('All Notes app versions are accepted again.')).toBeVisible()
  await expect(field).toHaveValue('')
  expect((await page.request.get('/api/v1/me', { headers: { 'User-Agent': 'mailr-android-notes/1.0.0' } })).status()).toBe(200)
})
