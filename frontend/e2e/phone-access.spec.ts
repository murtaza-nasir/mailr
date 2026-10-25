// ACT-SURFACES: web.settings
// Phones and desktop apps on the built-in calendars (backend calendar/phones.py,
// calendar/phone_check.py): the user's "Phones and other apps" section in Settings → Calendar &
// Tasks (app passwords shown once, the iPhone profile, delete) and the admin's switch, address
// and check in Settings → Admin → Calendars. The instance settings stay off in the e2e database
// (other specs share it), so the responses are mocked here. Backend: tests/test_phone_access*.py.
// Actions covered: settings.phone_passwords, settings.phone_access, settings.phone_access_check.
import type { Page } from '@playwright/test'
import { accessTest, expect, signIn, test } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

const BUILTIN = {
  id: 9200, kind: 'caldav', name: 'Mailr calendar', builtin: true, preset: null, mode: 'mirror', data_class: 'normal',
  config: { account: 'sam-7-abc123@mailr.internal' }, has_password: false, retention_days: 14, delete_upstream: false,
  enabled: true, status: 'idle', status_detail: '2 calendars', last_sync_at: new Date().toISOString(),
}
const NOTE = 'Invitations are not sent from phones. An event with guests that you make in a phone or desktop calendar app is saved, but nobody is invited. To invite people, make the event in Mailr.'

async function withBuiltin(page: Page) {
  await page.route('**/api/v1/connectors', async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const r = await route.fetch()
    await route.fulfill({ response: r, json: [...(await r.json()), BUILTIN] })
  })
}

function info(over: Record<string, unknown> = {}) {
  return {
    available: true, reason: '', instance: true, url: 'https://dav.northwind.example', server: 'dav.northwind.example',
    username: 'sam@northwind.example', passwords: [], scheduling_note: NOTE, max: 20, ...over,
  }
}

test('a user makes an app password for a phone, sees it once with the profile, and deletes it (settings.phone_passwords)', async ({ page }) => {
  await withBuiltin(page)
  const passwords: { id: string; name: string; created_at: string; paused: boolean }[] = []
  const posts: unknown[] = []
  const deletes: string[] = []
  await page.route('**/api/v1/me/dav', (route) => route.fulfill({ json: info({ passwords }) }))
  await page.route('**/api/v1/me/dav/app-passwords', async (route) => {
    posts.push(route.request().postDataJSON())
    passwords.push({ id: 'b', name: 'Sam’s iPhone', created_at: '2026-10-08T10:00:00Z', paused: false })
    await route.fulfill({ json: {
      id: 'b', name: 'Sam’s iPhone', password: 'app_aaaaexamplesecretxyz', created_at: '2026-10-08T10:00:00Z', paused: false,
      username: 'sam@northwind.example', url: 'https://dav.northwind.example', server: 'dav.northwind.example',
      profile: btoa('<?xml version="1.0"?><plist></plist>'), profile_name: 'mailr-sam-s-iphone.mobileconfig', scheduling_note: NOTE,
    } })
  })
  await page.route('**/api/v1/me/dav/app-passwords/*', async (route) => {
    deletes.push(route.request().url().split('/').pop()!)
    passwords.splice(0, passwords.length)
    await route.fulfill({ json: { ok: true } })
  })
  await page.goto('/settings/calendar')
  const section = page.getByTestId('phone-apps')
  await expect(page.getByRole('heading', { name: 'Phones and other apps' })).toBeVisible()
  await expect(section.getByTestId('phone-server')).toHaveText('dav.northwind.example')
  await expect(section.getByTestId('phone-username')).toHaveText('sam@northwind.example')
  await expect(section).toContainText('nobody is invited')
  await page.getByTestId('phone-new').click()
  const dialog = page.getByRole('dialog', { name: 'New app password' })
  await expect(page.getByTestId('phone-make')).toBeDisabled()
  await dialog.getByLabel('Device name').fill('Sam’s iPhone')
  await page.getByTestId('phone-make').click()
  await expect.poll(() => posts).toEqual([{ name: 'Sam’s iPhone' }])
  const made = page.getByRole('dialog', { name: 'App password for Sam’s iPhone' })
  await expect(made.getByTestId('phone-secret')).toHaveText('app_aaaaexamplesecretxyz')
  await expect(made).toContainText('shown only now')
  await expect(made).toContainText('DAVx5')
  await expect(made).toContainText('Thunderbird')
  await expect(made).toContainText('nobody is invited')
  const download = page.waitForEvent('download')
  await made.getByTestId('phone-profile').click()
  expect((await download).suggestedFilename()).toBe('mailr-sam-s-iphone.mobileconfig')
  await made.getByRole('button', { name: 'Done' }).click()
  // Shown once: the list has the name and the date, never the password.
  const list = page.getByTestId('phone-passwords')
  await expect(list).toContainText('Sam’s iPhone')
  await expect(page.getByText('app_aaaaexamplesecretxyz')).toHaveCount(0)
  await list.getByRole('button', { name: 'Delete the app password Sam’s iPhone' }).click()
  await page.getByRole('dialog', { name: 'Delete “Sam’s iPhone”?' }).getByRole('button', { name: 'Delete' }).click()
  await expect.poll(() => deletes).toEqual(['b'])
  await expect(page.getByTestId('phone-passwords')).toHaveCount(0)
})

test('the section says why when phone access is off for the user, and shows paused passwords (settings.phone_passwords)', async ({ page }) => {
  await withBuiltin(page)
  let body = info({ instance: false, available: false, reason: 'Phone access is off on this server. Ask your administrator to turn it on.' })
  await page.route('**/api/v1/me/dav', (route) => route.fulfill({ json: body }))
  await page.goto('/settings/calendar')
  await expect(page.getByTestId('builtin-calendar')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Phones and other apps' })).toHaveCount(0)
  body = info({ instance: false, available: false, reason: 'Phone access is off on this server. Ask your administrator to turn it on.',
                passwords: [{ id: 'c', name: 'Old iPad', created_at: '2026-10-01T09:00:00Z', paused: true }] })
  await page.reload()
  const section = page.getByTestId('phone-apps')
  await expect(section).toContainText('Phone access is off on this server')
  await expect(section).toContainText('Old iPad')
  await expect(section).toContainText('Paused')
  await expect(page.getByTestId('phone-new')).toHaveCount(0)
})

accessTest('an admin sets the address, turns phone access on and checks the address (settings.phone_access, settings.phone_access_check)', async ({ page, access }) => {
  await signIn(page, access.admin)
  const puts: Record<string, unknown>[] = []
  const state = { phone_access: false, phone_url: '' }
  await page.route('**/api/v1/admin/calendars', async (route) => {
    if (route.request().method() === 'PUT') {
      const b = route.request().postDataJSON() as Record<string, unknown>
      puts.push(b)
      if (typeof b.phone_url === 'string') state.phone_url = b.phone_url
      if (typeof b.phone_access === 'boolean') state.phone_access = b.phone_access
    }
    await route.fulfill({ json: { builtin: true, who: 'everyone', sharing: 'anyone', connectors: 3, users: 3, ...state } })
  })
  let checkRuns = 0
  await page.route('**/api/v1/admin/calendars/phone-check', async (route) => {
    checkRuns += 1
    if (checkRuns === 1) {
      await route.fulfill({ json: {
        ran: true, ok: false, url: 'https://dav.northwind.example', via: 'through the VPN egress proxy', reason: '', commands: [],
        checks: [
          { id: 'wellknown_caldav', label: '/.well-known/caldav leads to the calendars', ok: true, detail: 'redirects to /dav/cal/' },
          { id: 'dav_auth', label: '/dav/ asks for a password', ok: true, detail: 'asks for a password' },
          { id: 'acl_blocked', label: 'The ACL method is refused', ok: false, detail: 'answered 401: it reached the mail server' },
        ],
      } })
    } else {
      await route.fulfill({ json: {
        ran: false, ok: false, url: 'https://dav.northwind.example', checks: [],
        reason: 'Remote content is paused: no VPN egress proxy is configured. The check reaches the public address the same way Mailr fetches remote content, so it cannot run now.',
        commands: ["curl -s -o /dev/null -w '%{http_code}\\n' -X PROPFIND https://dav.northwind.example/dav/cal/    # 401"],
      } })
    }
  })
  await page.goto('/settings/admin/calendars')
  const panel = page.getByTestId('phone-access')
  await expect(panel.getByRole('heading', { name: 'Phones and desktop apps' })).toBeVisible()
  const sw = panel.getByRole('checkbox', { name: 'Let phones and desktop apps connect' })
  await expect(sw).toBeDisabled()
  await expect(panel).toContainText('Enter the public address first')
  await panel.getByTestId('phone-url').fill('https://dav.northwind.example')
  await panel.getByTestId('phone-url').press('Enter')
  await expect.poll(() => puts).toEqual([{ builtin: true, phone_url: 'https://dav.northwind.example' }])
  await expect(sw).toBeEnabled()
  await sw.click()
  await expect.poll(() => puts.length).toBe(2)
  expect(puts[1]).toEqual({ builtin: true, phone_access: true })
  await expect(sw).toBeChecked()
  await expect(panel).toContainText('Users create app passwords')
  await panel.getByTestId('phone-check').click()
  const res = panel.getByTestId('phone-check-result')
  await expect(res).toContainText('Check failed for')
  await expect(res).toContainText('through the VPN egress proxy')
  await expect(res).toContainText('The ACL method is refused')
  await expect(res).toContainText('it reached the mail server')
  await panel.getByTestId('phone-check').click()
  await expect(res).toContainText('no VPN egress proxy is configured')
  await expect(res).toContainText('-X PROPFIND https://dav.northwind.example/dav/cal/')
})
