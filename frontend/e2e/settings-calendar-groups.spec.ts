// ACT-SURFACES: web.settings web.calendar
// Settings → Calendar & tasks: the calendars and task lists grouped under where they come from
// (the account by name and host, shared with you, a phone, calendar links), the default pickers in
// the same groups, and an account in error explained in plain words with the technical text under
// Details; the Calendar app's banner says the same. The account is a real one on the dev Radicale;
// the other sources and the failure are added to the server's answers here (the server side is
// backend/tests/test_calendar_sync_errors.py).
import type { Page } from '@playwright/test'
import { connectCalendar, expect, shot, test } from './fixtures'

const RAW = 'PROPFIND https://cal.example.org/dav/: not found'

/** The real list plus a group calendar, a phone calendar, a calendar link and a second account whose
 *  calendar has the same name as one of the first account's. */
async function mockSources(page: Page) {
  await page.route('**/api/v1/calendar/calendars*', async (route) => {
    const r = await route.fetch().catch(() => null)
    if (!r) return
    const list = (await r.json().catch(() => null)) as { connector_id: number; name: string }[] | null
    if (!list) return
    const base = { events: true, tasks: false, components: ['VEVENT'], hidden: false, busy: true, notify: true, sort_order: 0, local_only: false, read_only: true }
    const extra = [
      { ...base, id: 'http://stalwart:8080/dav/cal/g/family/', url: 'http://stalwart:8080/dav/cal/g/family/', calendar_id: 990301, connector_id: 990001, name: 'Family', color: '#8e24aa',
        shared: { kind: 'group', group: 'Family', access: 'read' }, source: { kind: 'shared' } },
      { ...base, id: 'phone:k1', url: 'phone:k1', calendar_id: 990302, connector_id: 990002, name: 'Work phone', color: '#616161',
        phone: { key: 'k1', device: 'Pixel 9', synced_at: null, stale: false }, source: { kind: 'phone', device: 'Pixel 9' } },
      { ...base, id: 'webcal:990003', url: 'webcal:990003', calendar_id: 990303, connector_id: 990003, name: 'Holidays', color: '#d50000',
        subscription: { url: 'https://example.org/holidays.ics', refresh_hours: 12 }, source: { kind: 'link' } },
      { ...base, read_only: false, id: 'https://dav.example.net/u/work/', url: 'https://dav.example.net/u/work/', calendar_id: 990304, connector_id: 990004,
        name: list[0]?.name ?? 'E2E Work', color: '#039be5', source: { kind: 'account', name: 'Office', host: 'dav.example.net' } },
    ]
    await route.fulfill({ response: r, json: [...list, ...extra] }).catch(() => undefined)
  })
}

/** The real account, shown as failing: its address no longer has calendars. */
async function mockFailure(page: Page) {
  await page.route('**/api/v1/connectors', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch().catch(() => null)
    if (!r) return
    const list = (await r.json().catch(() => null)) as { kind: string; builtin?: boolean }[] | null
    if (!list) return
    await route.fulfill({ response: r, json: list.map((c) => (c.kind === 'caldav' && !c.builtin
      ? { ...c, status: 'error', status_detail: RAW, error_kind: 'not_found', error_host: 'cal.example.org' } : c)) }).catch(() => undefined)
  })
  await page.route('**/api/v1/calendar/sync-status', async (route) => {
    const r = await route.fetch().catch(() => null)
    if (!r) return
    const st = (await r.json().catch(() => null)) as { connectors: { id: number }[] } | null
    if (!st) return
    await route.fulfill({ response: r, json: { ...st, state: 'error', connectors: st.connectors.map((c) => ({
      ...c, status: 'error', detail: RAW, error_kind: 'not_found', error_host: 'cal.example.org' })) } }).catch(() => undefined)
  })
}

test('settings: calendars grouped under their account, phone, shared and links, with the pickers grouped too', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await mockSources(page)
  await page.goto('/settings/calendar')
  const groups = page.getByTestId('settings-collection-group')
  await expect(groups.filter({ hasText: 'Work phone' })).toBeVisible()
  const headings = page.getByTestId('settings-collection-heading')
  const n = await headings.count()
  const texts = (await headings.allInnerTexts()).map((s) => s.replace(/\s+/g, ' ').trim())
  // The connected account first (by its name and host), then shared, the phone, the links.
  expect(texts.slice(-4)).toEqual(['Office dav.example.net', 'Shared with you', 'Calendars from your phone Pixel 9', 'Calendar links'])
  expect(n).toBeGreaterThanOrEqual(5)
  const realHost = new URL(seeded.caldav!.url).host
  expect(texts[0]).toContain(realHost)
  await expect(groups.filter({ hasText: 'Shared with you' }).getByTestId('settings-collection')).toHaveText([/Family/])
  await expect(groups.filter({ hasText: 'Calendar links' }).getByTestId('settings-collection')).toHaveText([/Holidays/])
  // Two calendars of the same name sit under different headings in the default calendar picker.
  const picker = page.getByLabel('Default calendar')
  const optgroups = await picker.locator('optgroup').evaluateAll((els) => els.map((e) => (e as HTMLOptGroupElement).label))
  expect(optgroups).toContain('Office · dav.example.net')
  expect(optgroups.some((l) => l.includes(realHost))).toBe(true)
  await page.getByTestId('settings-collections').screenshot({ path: shot('settings-calendar-groups.png') })
})

test('settings and calendar: an account in error says what to do, with the technical text under Details', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await mockFailure(page)
  await page.goto('/settings/calendar')
  const problem = page.getByTestId('sync-problem').first()
  await expect(problem).toContainText('The calendar server at cal.example.org no longer has calendars at this address.')
  await expect(problem).toContainText('disconnect this account if you no longer use it')
  const raw = problem.locator('code')
  await expect(raw).toBeHidden()
  await problem.getByText('Details').click()
  await expect(raw).toHaveText(RAW)
  await page.screenshot({ path: shot('settings-calendar-error.png') })

  await page.goto('/calendar')
  const banner = page.getByTestId('cal-account-error').first()
  await expect(banner).toContainText('no longer has calendars at this address')
  await expect(banner.locator('code')).toBeHidden()
  await banner.getByText('Details').click()
  await expect(banner.locator('code')).toHaveText(RAW)
  await page.screenshot({ path: shot('calendar-account-error.png') })
})
