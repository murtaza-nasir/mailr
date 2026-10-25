// ACT-SURFACES: web.calendar
// Windows zone names never take Calendar down (9 Oct 2026). Outlook writes "Central Standard
// Time"; an event added from an Outlook invite was stored and served with it, and the browser's
// formatter threw ("invalid time zone"), so the whole screen showed "Something went wrong".
// The server now stores and serves IANA names only (backend tests/test_tz_iana_only.py). Here the
// raw name is put back into every API response, as an old server or record would send it, and each
// view and the event's detail must still show the event. Covers: web.calendar.
import type { Page } from '@playwright/test'
import { at, createEvent, dayOf, pinNow } from './calendar-data'
import { connectCalendar, expect, test } from './fixtures'

const RAW = 'Central Standard Time'

/** Rewrites every IANA zone field the API sends for the event's zone back to the Windows name;
 *  returns a counter of the fields rewritten. */
async function serveRawZones(page: Page): Promise<{ n: number }> {
  const seen = { n: 0 }
  await page.route('**/api/v1/**', async (route) => {
    const res = await route.fetch()
    const type = res.headers()['content-type'] ?? ''
    if (!type.includes('json')) return route.fulfill({ response: res })
    const body = (await res.text()).replace(/"(tz|end_tz|organizer_tz)":\s*"America\/Chicago"/g, (_m, key: string) => {
      seen.n++
      return `"${key}":"${RAW}"`
    })
    return route.fulfill({ response: res, body })
  })
  return seen
}

test('an event whose zone is a Windows name shows in every view and opens its detail', async ({ page, seeded }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await connectCalendar(page.request, seeded)
  // The API takes the Windows name and stores the IANA one.
  const ev = await createEvent(page.request, { summary: 'Outlook budget call', start: at(3, 14), end: at(3, 15), tz: RAW })
  const stored = await (await page.request.get(`/api/v1/calendar/events/${encodeURIComponent(ev.uid)}`)).json()
  const one = stored.event ?? stored
  expect(one.tz).toBe('America/Chicago')

  const raw = await serveRawZones(page)
  for (const view of ['day', 'ndays', 'workweek', 'week', 'month', 'schedule']) {
    await page.goto(`/calendar/${view}/${dayOf(3)}`)
    const chip = page.getByTestId('cal-event').filter({ hasText: 'Outlook budget call' }).first()
    await expect(chip, view).toBeVisible()
    await expect(page.getByText('Something went wrong')).toHaveCount(0)
    await expect(page.getByTestId('item-error')).toHaveCount(0)
    await chip.click()
    const detail = page.getByTestId('cal-detail')
    await expect(detail.getByRole('heading', { name: 'Outlook budget call' }), view).toBeVisible()
    await expect(detail).toContainText(/2:00|14:00/)
    await page.keyboard.press('Escape')
  }
  await page.goto(`/calendar/event/${encodeURIComponent(ev.uid)}/${encodeURIComponent(one.occurrence)}`)
  await expect(page.getByTestId('cal-detail').getByRole('heading', { name: 'Outlook budget call' })).toBeVisible()
  await expect(page.getByText('Something went wrong')).toHaveCount(0)
  // Today, on the event's day.
  await pinNow(page, 3, 9)
  await page.goto('/calendar/today')
  await expect(page.getByTestId('cal-today')).toContainText('Outlook budget call')
  await expect(page.getByText('Something went wrong')).toHaveCount(0)
  expect(raw.n).toBeGreaterThan(0) // the browser really got the Windows name
  expect(errors.filter((m) => /time zone/i.test(m))).toEqual([])
})
