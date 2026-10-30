// ACT-SURFACES: web.calendar web.settings web.panel
// Phone calendars (backend calendar/phonecal.py): busy times sent by the Android app show in the
// Calendar app as read-only "Busy" blocks in the calendar's colour, with "Updated from <device> …"
// in the calendar list and the event detail; Settings → Calendar lists them with what the phone sends
// (busy times only, or with titles and places) and Remove deletes them. A calendar set to send details
// shows each block's title and place, read-only, with the same freshness line.
// Covers: ACT calendar.phone_list (web.calendar, web.panel, web.settings), ACT calendar.phone_remove (web.settings).
import { expect, test } from './fixtures'

const KEY = () => Array.from({ length: 32 }, () => '0123456789abcdef'[Math.floor(Math.random() * 16)]).join('')

function isoDay(offset: number): string {
  const d = new Date()
  d.setUTCHours(0, 0, 0, 0)
  d.setUTCDate(d.getUTCDate() + offset)
  return d.toISOString().slice(0, 10)
}

test('a phone calendar shows as busy blocks with the freshness line, and Remove in Settings deletes it', async ({ page, seeded }) => {
  void seeded
  await page.setViewportSize({ width: 1400, height: 900 })
  const key = KEY()
  const day = isoDay(3)
  const put = await page.request.put(`/api/v1/calendar/phone-calendars/${key}`, {
    data: {
      name: 'Work phone calendar', color: '#0078d4', device: 'Pixel 9 Pro Fold',
      window_start: `${isoDay(-30)}T00:00:00Z`, window_end: `${isoDay(180)}T00:00:00Z`,
      busy: [{ id: 'a1'.repeat(12), start: `${day}T12:00:00Z`, end: `${day}T13:00:00Z`, all_day: false }],
    },
  })
  expect(put.ok(), await put.text()).toBeTruthy()

  await page.goto(`/calendar/day/${day}`)
  const block = page.locator('.cal-block', { hasText: 'Busy' })
  await expect(block).toHaveCount(1)
  await expect(block).toHaveClass(/\bro\b/)
  // The calendar list says where the busy times come from and how fresh they are.
  const row = page.getByTestId('cal-calendar').filter({ hasText: 'Work phone calendar' })
  await expect(row.getByTestId('phone-freshness')).toHaveText(/Updated from Pixel 9 Pro Fold (just now|1 minute ago)/)

  // The detail: read-only, with the freshness line; no edit or delete.
  await block.click()
  const detail = page.getByTestId('cal-detail')
  await expect(detail.getByRole('heading', { name: 'Busy' })).toBeVisible()
  await expect(detail.getByTestId('phone-freshness')).toHaveText(/Updated from Pixel 9 Pro Fold/)
  await expect(detail.getByText('Read-only calendar')).toBeVisible()
  await expect(detail.getByRole('button', { name: /^Edit/ })).toHaveCount(0)
  await expect(detail.getByRole('button', { name: /^Delete/ })).toHaveCount(0)
  await page.keyboard.press('Escape')

  // The calendar side panel's list of calendars says the same.
  await page.goto('/mail/inbox')
  await page.locator('.rail').getByRole('button', { name: 'Calendar beside' }).click()
  await page.getByTestId('calendar-panel').getByRole('button', { name: 'Calendars shown' }).click()
  await expect(page.locator('.cp-cal', { hasText: 'Work phone calendar' }).getByTestId('phone-freshness')).toHaveText(/Updated from Pixel 9 Pro Fold/)
  await page.keyboard.press('Escape')

  // Settings → Calendar lists it; Remove (confirmed) deletes it from Mailr.
  await page.goto('/settings/calendar')
  const card = page.getByTestId('phone-calendar').filter({ hasText: 'Work phone calendar' })
  await expect(card).toContainText('Updated from Pixel 9 Pro Fold')
  await expect(card).toContainText('1 busy time')
  await expect(card.getByTestId('phone-calendar-sends')).toHaveText('Busy times only')
  await card.getByRole('button', { name: 'Remove Work phone calendar' }).click()
  const dialog = page.getByRole('dialog', { name: 'Remove “Work phone calendar”?' })
  await dialog.getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByTestId('phone-calendar')).toHaveCount(0)
  await expect(page.getByTestId('phone-calendars-none')).toBeVisible()
  const left = await (await page.request.get('/api/v1/calendar/phone-calendars')).json()
  expect(left).toEqual([])

  await page.goto(`/calendar/day/${day}`)
  await expect(page.locator('.cal-block', { hasText: 'Busy' })).toHaveCount(0)
})

test('a phone calendar that sends details shows titles and places, read-only, and Settings says so', async ({ page, seeded }) => {
  void seeded
  await page.setViewportSize({ width: 1400, height: 900 })
  const key = KEY()
  const day = isoDay(4)
  const put = await page.request.put(`/api/v1/calendar/phone-calendars/${key}`, {
    data: {
      name: 'Team phone calendar', color: '#2e7d32', device: 'Pixel 9 Pro Fold', details: true,
      window_start: `${isoDay(-30)}T00:00:00Z`, window_end: `${isoDay(180)}T00:00:00Z`,
      busy: [{ id: 'b2'.repeat(12), start: `${day}T12:00:00Z`, end: `${day}T13:00:00Z`, all_day: false,
        title: 'Planning review', location: 'Hall B' }],
    },
  })
  expect(put.ok(), await put.text()).toBeTruthy()
  // Without details a title is refused.
  const refused = await page.request.put(`/api/v1/calendar/phone-calendars/${KEY()}`, {
    data: { name: 'Other', window_start: `${isoDay(-1)}T00:00:00Z`, window_end: `${isoDay(10)}T00:00:00Z`,
      busy: [{ id: 'c3'.repeat(12), start: `${day}T12:00:00Z`, end: `${day}T13:00:00Z`, title: 'Planning review' }] },
  })
  expect(refused.status()).toBe(422)

  await page.goto(`/calendar/day/${day}`)
  const block = page.locator('.cal-block', { hasText: 'Planning review' })
  await expect(block).toHaveCount(1)
  await expect(block).toHaveClass(/\bro\b/)

  await block.click()
  const detail = page.getByTestId('cal-detail')
  await expect(detail.getByRole('heading', { name: 'Planning review' })).toBeVisible()
  await expect(detail.getByText('Hall B')).toBeVisible()
  await expect(detail.getByTestId('phone-freshness')).toHaveText(/Updated from Pixel 9 Pro Fold/)
  await expect(detail.getByText('An event from a calendar on your phone. It changes only on the phone.')).toBeVisible()
  await expect(detail.getByText('Read-only calendar')).toBeVisible()
  await expect(detail.getByRole('button', { name: /^Edit/ })).toHaveCount(0)
  await page.keyboard.press('Escape')

  await page.goto('/settings/calendar')
  const card = page.getByTestId('phone-calendar').filter({ hasText: 'Team phone calendar' })
  await expect(card.getByTestId('phone-calendar-sends')).toHaveText('Busy times with titles and places')
  await expect(card).toContainText('1 busy time')
  // Back to busy times only: the title and place are gone, and Settings says so.
  const back = await page.request.put(`/api/v1/calendar/phone-calendars/${key}`, {
    data: { name: 'Team phone calendar', color: '#2e7d32', device: 'Pixel 9 Pro Fold',
      window_start: `${isoDay(-30)}T00:00:00Z`, window_end: `${isoDay(180)}T00:00:00Z`,
      busy: [{ id: 'b2'.repeat(12), start: `${day}T12:00:00Z`, end: `${day}T13:00:00Z`, all_day: false }] },
  })
  expect(back.ok(), await back.text()).toBeTruthy()
  await page.reload()
  await expect(page.getByTestId('phone-calendar').filter({ hasText: 'Team phone calendar' }).getByTestId('phone-calendar-sends'))
    .toHaveText('Busy times only')
  await page.goto(`/calendar/day/${day}`)
  await expect(page.locator('.cal-block', { hasText: 'Planning review' })).toHaveCount(0)
  await expect(page.locator('.cal-block', { hasText: 'Busy' })).toHaveCount(1)
  expect((await page.request.delete(`/api/v1/calendar/phone-calendars/${key}`)).ok()).toBeTruthy()
})
