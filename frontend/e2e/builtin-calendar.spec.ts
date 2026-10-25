// ACT-SURFACES: web.settings web.setup
// The built-in "Mailr calendar" (backend calendar/builtin.py) in the web app: the card in
// Settings → Calendar & Tasks (no address, no Remove, a switch that hides it), the setup guide's
// calendar step (connected, with both choices still on offer), and the admin switch. The instance
// setting stays off in the e2e database, because other specs share it: the connector and the admin
// setting come from mocked responses here. Backend: tests/test_builtin_calendar*.py.
// Actions covered: calendar.builtin_hide, settings.builtin_calendars.
import type { Page } from '@playwright/test'
import { accessTest, expect, signIn, test } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

const BUILTIN = {
  id: 9100, kind: 'caldav', name: 'Mailr calendar', builtin: true, preset: null, mode: 'mirror', data_class: 'normal',
  config: { account: 'rosa-7-abc123@mailr.internal' }, has_password: false, retention_days: 14, delete_upstream: false,
  enabled: true, status: 'idle', status_detail: '2 calendars', last_sync_at: new Date().toISOString(),
}

/** The connector list with the built-in calendar in it (enabled as given), and its PATCHes. */
async function withBuiltin(page: Page, state: { enabled: boolean; patches: unknown[] }) {
  await page.route('**/api/v1/connectors', async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const r = await route.fetch()
    await route.fulfill({ response: r, json: [...(await r.json()), { ...BUILTIN, enabled: state.enabled }] })
  })
  await page.route(`**/api/v1/connectors/${BUILTIN.id}`, async (route) => {
    const body = route.request().postDataJSON() as { enabled?: boolean }
    state.patches.push(body)
    if (typeof body.enabled === 'boolean') state.enabled = body.enabled
    await route.fulfill({ json: { ...BUILTIN, enabled: state.enabled } })
  })
}

test('settings show the Mailr calendar as fixed, and it can be hidden and shown (calendar.builtin_hide)', async ({ page }) => {
  const state = { enabled: true, patches: [] as unknown[] }
  await withBuiltin(page, state)
  await page.goto('/settings/calendar')
  const card = page.getByTestId('builtin-calendar')
  await expect(card).toContainText('Mailr calendar')
  await expect(card).toContainText('On this server')
  await expect(card).toContainText('Always connected')
  await expect(card.getByRole('button', { name: 'Remove' })).toHaveCount(0)
  await expect(card).not.toContainText('http')
  const sw = card.getByRole('switch', { name: 'Show the Mailr calendar' })
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  await sw.click()
  await expect.poll(() => state.patches).toEqual([{ enabled: false }])
  await expect(card).toContainText('Hidden')
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await sw.click()
  await expect.poll(() => state.patches.length).toBe(2)
  await expect(sw).toHaveAttribute('aria-checked', 'true')
})

test('the setup guide shows the Mailr calendar as connected and still offers the other choices', async ({ page }) => {
  await withBuiltin(page, { enabled: true, patches: [] })
  // Opening the guide again saves its state (PUT); both answers carry the prepared server.
  await page.route('**/api/v1/onboarding', async (route) => {
    const r = await route.fetch()
    const body = await r.json()
    await route.fulfill({ response: r, json: { ...body, offer: { ...body.offer, calendar_url: 'https://cal.example.com/dav/' } } })
  })
  await page.goto('/setup/calendar')
  await expect(page.getByRole('heading', { name: 'Your calendars' })).toBeVisible()
  const list = page.getByRole('list', { name: 'Connected calendars' })
  await expect(list).toContainText('Mailr calendar')
  await expect(list).toContainText('are on this server and ready')
  await expect(page.getByRole('radio', { name: 'Use our calendar server' })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'Connect a calendar I already use' })).toBeVisible()
  await expect(page.locator('.ob-foot').getByRole('button', { name: 'Continue without another' })).toBeVisible()
  await page.getByRole('radio', { name: 'Connect a calendar I already use' }).click()
  await page.locator('.ob-foot').getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Connect a calendar' })).toBeVisible()
})

accessTest('an admin turns built-in calendars on (settings.builtin_calendars)', async ({ page, access }) => {
  await signIn(page, access.admin)
  const puts: unknown[] = []
  let on = false
  await page.route('**/api/v1/admin/calendars', async (route) => {
    if (route.request().method() === 'PUT') {
      puts.push(route.request().postDataJSON())
      on = (route.request().postDataJSON() as { builtin: boolean }).builtin
    }
    await route.fulfill({ json: { builtin: on, connectors: on ? 3 : 0, users: 3 } })
  })
  await page.goto('/settings/admin/calendars')
  await expect(page.getByRole('heading', { name: 'Calendars', exact: true })).toBeVisible()
  const box = page.getByRole('checkbox', { name: 'Give users a Mailr calendar' })
  await expect(box).not.toBeChecked()
  await expect(page.getByText(/When off, these calendars are hidden/)).toBeVisible()
  await box.click()
  await expect.poll(() => puts).toEqual([{ builtin: true }])
  await expect(box).toBeChecked()
  await expect(page.getByText('On. 3 of 3 users have one')).toBeVisible()
})

// Built-in calendars off (the default) and no calendar account: the empty Calendar and Tasks apps
// and the setup guide's calendar step offer an admin the switch, and tell a member who can turn it on.
accessTest('built-in calendars off: an admin is offered the switch where a calendar is missing', async ({ page, access }) => {
  await signIn(page, access.admin)
  let on = false
  await page.route('**/api/v1/admin/calendars', async (route) => {
    if (route.request().method() === 'PUT') on = (route.request().postDataJSON() as { builtin: boolean }).builtin
    await route.fulfill({ json: { builtin: on, connectors: 0, users: 2 } })
  })
  await page.goto('/calendar/week')
  const cal = page.getByTestId('cal-not-connected')
  await expect(cal).toContainText('Built-in calendars are off.')
  const turnOn = cal.getByRole('link', { name: 'Turn on built-in calendars' })
  await expect(turnOn).toHaveAttribute('href', '/settings/admin/calendars')
  await page.goto('/tasks/today')
  await expect(page.getByTestId('tasks-no-account').getByRole('link', { name: 'Turn on built-in calendars' })).toHaveAttribute('href', '/settings/admin/calendars')
  await page.goto('/setup/calendar')
  await expect(page.getByRole('heading', { name: 'Your calendars' })).toBeVisible()
  await page.getByRole('link', { name: 'Turn on built-in calendars' }).click()
  await expect(page).toHaveURL(/\/settings\/admin\/calendars$/)
  await expect(page.getByRole('checkbox', { name: 'Give users a Mailr calendar' })).not.toBeChecked()
  // Once they are on, the line is gone.
  on = true
  await page.goto('/calendar/week')
  await expect(page.getByTestId('cal-not-connected')).toBeVisible()
  await expect(page.getByTestId('builtin-cal-hint')).toHaveCount(0)
})

accessTest('built-in calendars off: a member is told to ask for them', async ({ page, access }) => {
  await signIn(page, access.family)
  await page.goto('/calendar/week')
  await expect(page.getByTestId('cal-not-connected')).toContainText('Ask whoever runs this Mailr server to turn on built-in calendars.')
  await expect(page.getByRole('link', { name: 'Turn on built-in calendars' })).toHaveCount(0)
  await page.goto('/tasks/today')
  await expect(page.getByTestId('tasks-no-account')).toContainText('Ask whoever runs this Mailr server to turn on built-in calendars')
  // The setup guide shows it to admins only.
  await page.goto('/setup/calendar')
  await expect(page.getByRole('heading', { name: 'Your calendars' })).toBeVisible()
  await expect(page.getByTestId('builtin-cal-hint')).toHaveCount(0)
})
