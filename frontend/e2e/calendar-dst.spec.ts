// ACT-SURFACES: web.calendar web.panel
// The repeated hour of the autumn change in America/Chicago (1 Nov 2026, 02:00 CDT → 01:00 CST).
// An event from 06:42Z to 07:27Z reads 01:42 CDT → 01:27 CST: its clock end comes before its start.
// On 1 Nov 2026 the day grid drew it with no height, and the Join link (ACT event.join), Record
// this meeting (ACT recording.record) and the calendar panel's Next line (ACT event.last_recording)
// were gone, because the stored end read back an hour early. The browser's clock is fixed at
// 06:43Z (01:43 CDT), inside the event.
import type { Page, Route } from '@playwright/test'
import { createEvent, openIn } from './calendar-data'
import { apiSettled, connectCalendar, expect, test } from './fixtures'

const ZONE = 'America/Chicago'
const NOW = '2026-11-01T06:43:00Z'
const START = '2026-11-01T06:42:00Z'
const END = '2026-11-01T07:27:00Z'
const JOIN = 'https://meet.jit.si/mailr-e2e-dst'
const TITLE = 'DST fallback call'

test.use({ timezoneId: ZONE })

/** Mocks /api/v1/speakr: connected, nothing linked, and a "last time" for the next meeting. */
async function mockSpeakr(page: Page): Promise<void> {
  await page.route('**/api/v1/speakr/**', async (route: Route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname.replace('/api/v1/speakr', '')
    const key = `${req.method()} ${path}`
    if (path === '/status') return route.fulfill({ json: { connected: true, connectors: [{ id: 3, name: 'Speakr', base_url: 'https://speakr.example' }] } })
    if (key === 'GET /events/recordings') return route.fulfill({ json: { connected: true, items: [] } })
    if (key === 'GET /events/last-time') {
      return route.fulfill({
        json: {
          connected: true, upcoming: true,
          item: {
            connector_id: 3, recording_id: 20, title: TITLE, meeting_date: '2026-10-25T06:42:00Z', created_at: '2026-10-25T06:42:00Z',
            duration_s: 2700, participants: [], people: [], tags: [], folder: null, status: 'completed', has_transcript: true,
            has_summary: true, audio_available: true, is_archived: false, local_only: true, url: 'https://speakr.example/recordings/20',
            event_link: null, why: 'the same series', summary_line: 'Agree the rota',
          },
        },
      })
    }
    if (key === 'GET /recordings') return route.fulfill({ json: { items: [], more: false } })
    return route.fulfill({ status: 404, json: { detail: `unmocked ${key}` } })
  })
}

test('an event across the repeated autumn hour keeps its height, Join, Record and the panel Next line', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  expect((await page.request.patch('/api/v1/me/settings', { data: { timezone: ZONE } })).ok()).toBeTruthy()
  const ev = await createEvent(page.request, { summary: TITLE, start: START, end: END, url: JOIN })
  // The stored event keeps both instants (the end used to read back as 06:27Z).
  const agenda = await (await page.request.get('/api/v1/calendar/agenda', { params: { from: '2026-10-31', to: '2026-11-02' } })).json() as { events: { uid: string; start: string; end: string }[] }
  const got = agenda.events.find((e) => e.uid === ev.uid)
  expect(got && [new Date(got.start).toISOString(), new Date(got.end).toISOString()]).toEqual(['2026-11-01T06:42:00.000Z', '2026-11-01T07:27:00.000Z'])

  await page.clock.setFixedTime(new Date(NOW))
  await mockSpeakr(page)
  await openIn(page, 'classic', '/calendar/day/2026-11-01')
  const block = page.getByTestId('cal-event').filter({ hasText: TITLE }).first()
  await expect(block).toBeVisible()
  // 45 minutes tall, not the 20-minute floor of an empty block.
  await expect(block).not.toHaveClass(/\bcompact\b/)
  const now = page.locator('.cal-now')
  const [b, n] = [await block.boundingBox(), await now.boundingBox()]
  expect(b && n).toBeTruthy()
  const hourH = await page.getByTestId('cal-timegrid').evaluate((el) => parseFloat(getComputedStyle(el).getPropertyValue('--hour-h')))
  expect(b!.height).toBeGreaterThan(hourH * 0.7)
  // The now line (01:43 CDT, a minute after the start) meets the block near its top, well above its end.
  expect(n!.y + n!.height).toBeGreaterThan(b!.y - 2)
  expect(n!.y).toBeLessThan(b!.y + b!.height - hourH * 0.5)
  await expect(block.getByRole('link', { name: 'Join' })).toHaveAttribute('href', JOIN)

  // The detail offers Record this meeting while it is on.
  await block.click()
  await expect(page.getByTestId('cal-recording-find').getByRole('button', { name: 'Record this meeting' })).toBeVisible()
  await page.keyboard.press('Escape')

  // The calendar panel beside mail: the meeting going on is today's next.
  await page.goto('/mail/inbox')
  await apiSettled(page, /\/api\/v1\//)
  await page.locator('.rail').getByRole('button', { name: 'Calendar beside' }).click()
  const next = page.getByTestId('calendar-panel').getByTestId('next-last-time')
  await expect(next).toContainText('Next:')
  await expect(next).toContainText(TITLE)
  await expect(next).toContainText('Agree the rota')
})
