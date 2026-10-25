// ACT-SURFACES: web.calendar web.sheet
// Meeting prep (event.prep; backend calendar/meeting_prep.py) in the event detail: in Classic (a
// popover), in Cards (a bottom sheet) and on a phone-sized screen; the pack from the server (a task
// that names a guest), the sections of a fuller pack, Summarise, the ?prep=1 link that opens it,
// and the Prep link of an event reminder's sheet.
import type { Page } from '@playwright/test'
import { dayOf } from './calendar-data'
import { connectCalendar, expect, test } from './fixtures'

const GUEST = { email: 'dana@lumen.example', name: 'Dana Whitfield' }

async function upcoming(page: Page, summary = 'Lumen review') {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  // Next week, so it is upcoming at any hour and on any weekday.
  const r = await page.request.post('/api/v1/calendar/events', {
    data: { summary, start: `${dayOf(9)}T10:00:00`, end: `${dayOf(9)}T11:00:00`, tz, attendees: [GUEST], send_updates: false },
  })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as { uid: string; occurrence?: string; start: string }
}

const PACK = {
  event: { uid: 'x', occurrence: '', title: 'Lumen review', start: null, end: null, guests: [GUEST], url: '', local_only: false },
  upcoming: true, has_guests: true, hidden: 1, local_only: false,
  recording: { connector_id: 1, recording_id: 7, title: 'Lumen review', meeting_date: '2026-09-28T15:00:00Z', created_at: null,
    summary_line: 'Dana wants the pricing page first', why: 'series', local_only: false,
    action_items: { open: [{ uid: 't1', summary: 'Draft the pricing page', due: null }], done: 2 } },
  threads: [{ conversation_id: 1, message_id: 1, subject: 'Pricing page', snippet: 'Can you share the draft?', last_date: '2026-10-01T10:00:00Z',
    messages: 3, people: [GUEST], signals: [], from_me: true }],
  tasks: [{ uid: 't2', summary: 'Send Dana the logo files', due: null, why: 'mentions' }],
  notes: [{ entity: { id: 1, name: 'Dana Whitfield', kind: 'person', summary: "Lumen's CMO", emails: [GUEST.email] },
    notes: [{ id: 1, text: 'Prefers short decks', kind: 'preference', from_mail: false }] }],
  attachments: [{ message_id: 1, conversation_id: 1, filename: 'pricing-v2.pdf', type: 'application/pdf', size: 1, date: '2026-10-01T10:00:00Z', from: GUEST.email, direction: 'received' }],
  open_items: {
    questions: [{ conversation_id: 1, message_id: 2, from: GUEST.email, date: null, question: 'Are we still aiming for the 20th?' }],
    waiting_on: [{ conversation_id: 1, message_id: 1, subject: 'Pricing page', since: '2026-10-01T10:00:00Z', follow_up_at: null }],
    promised: [{ conversation_id: 1, message_id: 3, date: null, to: [GUEST.email], text: "I'll send the deck on Monday." }],
  },
}

async function mockPack(page: Page) {
  await page.route('**/api/v1/calendar/events/*/prep?*', (r) => r.fulfill({ json: PACK }))
  await page.route('**/api/v1/calendar/events/*/prep/summary', (r) => r.fulfill({ json: { status: 'ok', text: 'Dana will ask about the pricing page; bring the deck.' } }))
}

async function openEvent(page: Page, title = 'Lumen review') {
  await page.goto(`/calendar/week/${dayOf(9)}`)
  await page.getByTestId('cal-event').filter({ hasText: title }).first().click()
  const detail = page.getByTestId('cal-detail')
  await expect(detail).toBeVisible()
  return detail
}

test('event.prep in Classic: the pack from the server, opened from the event detail (web.calendar)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await upcoming(page)
  await page.request.post('/api/v1/tasks', { data: { summary: 'Ask Dana Whitfield about the logo files' } })
  // An event without guests offers no prep.
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  await page.request.post('/api/v1/calendar/events', { data: { summary: 'Focus time', start: `${dayOf(9)}T14:00:00`, end: `${dayOf(9)}T15:00:00`, tz } })

  const detail = await openEvent(page)
  const prep = detail.getByTestId('meeting-prep')
  await expect(prep.getByRole('button', { name: /Meeting prep/ })).toHaveAttribute('aria-expanded', 'false')
  const loaded = page.waitForResponse((r) => r.url().includes('/prep?') && r.ok())
  await prep.getByRole('button', { name: /Meeting prep/ }).click()
  await loaded
  await expect(prep.getByTestId('prep-tasks')).toContainText('Ask Dana Whitfield about the logo files')
  await expect(prep).toContainText('1 task')

  // Summarise asks the server (mocked here: the model is the user's own).
  await page.route('**/api/v1/calendar/events/*/prep/summary', (r) => r.fulfill({ json: { status: 'refused', text: null,
    message: 'This pack holds mail from a local-only account, and no local model is available to summarise it.' } }))
  await prep.getByRole('button', { name: 'Summarise' }).click()
  await expect(prep.getByTestId('prep-summary')).toContainText('no local model')

  await page.keyboard.press('Escape')
  const focus = await openEvent(page, 'Focus time')
  await expect(focus.getByTestId('meeting-prep')).toHaveCount(0)
})

test('event.prep: the sections, Summarise and a thread link (web.calendar)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const ev = await upcoming(page)
  await mockPack(page)
  // ?prep=1 (the reminder's Prep link and the digest's links) opens the detail with the pack open.
  await page.goto(`/calendar/event/${encodeURIComponent(ev.uid)}/${encodeURIComponent(ev.occurrence ?? ev.start)}?prep=1`)
  const prep = page.getByTestId('meeting-prep')
  await expect(prep.getByTestId('prep-recording')).toContainText('Dana wants the pricing page first')
  await expect(prep.getByTestId('prep-recording')).toContainText('1 open action item, 2 done')
  await expect(prep.getByTestId('prep-open')).toContainText('Are we still aiming for the 20th?')
  await expect(prep.getByTestId('prep-open')).toContainText("I'll send the deck on Monday.")
  await expect(prep.getByTestId('prep-notes')).toContainText('Prefers short decks')
  await expect(prep.getByTestId('prep-files')).toContainText('pricing-v2.pdf')
  await expect(prep).toContainText('Some local-only items are hidden.')
  await prep.getByRole('button', { name: 'Summarise' }).click()
  await expect(prep.getByTestId('prep-summary')).toContainText('bring the deck')
  await prep.getByTestId('prep-threads').getByRole('button', { name: 'Pricing page' }).click()
  await expect(page).toHaveURL(/\/mail\/all\//)
})

test('event.prep in Cards: the bottom sheet (web.calendar)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await upcoming(page)
  await mockPack(page)
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'cards'))
  await openEvent(page)
  const prep = page.locator('.cal-sheet').getByTestId('meeting-prep')
  await prep.getByRole('button', { name: /Meeting prep/ }).click()
  await expect(prep.getByTestId('prep-threads')).toContainText('Pricing page')
  await expect(prep).toContainText('Last recording · 1 thread · 1 open question')
})

test.describe('on a phone-sized screen', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('event.prep in the event sheet (web.calendar)', async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    const ev = await upcoming(page)
    await mockPack(page)
    await page.goto(`/calendar/event/${encodeURIComponent(ev.uid)}/${encodeURIComponent(ev.occurrence ?? ev.start)}?prep=1`)
    const prep = page.getByTestId('meeting-prep')
    await expect(prep.getByTestId('prep-tasks')).toContainText('Send Dana the logo files')
    // Nothing overflows the narrow sheet.
    const box = await prep.boundingBox()
    expect(box!.width).toBeLessThanOrEqual(390)
    const overflow = await prep.evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })
})

test('event.prep from a reminder: the sheet links to the prep (web.sheet)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const ev = await upcoming(page)
  await mockPack(page)
  const start = ev.start
  const key = `e|${ev.uid}|${start}|10`
  const prepPath = `/calendar/event/${encodeURIComponent(ev.uid)}/${encodeURIComponent(start)}?prep=1`
  await page.route('**/api/v1/reminders/active*', (r) => r.fulfill({ json: { items: [{ key, kind: 'event', uid: ev.uid, occurrence: start, minutes: 10,
    title: 'Lumen review', detail: '', start, fire_at: start, fired_at: new Date().toISOString(), missed: false, state: 'fired',
    actions: ['snooze', 'dismiss'], prep: prepPath }] } }))
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  await page.getByRole('dialog', { name: 'Waiting for you' }).getByTestId('reminder-row').first().click()
  await page.getByTestId('reminder-sheet').getByTestId('reminder-prep').click()
  await expect(page).toHaveURL(/prep=1/)
  await expect(page.getByTestId('meeting-prep').getByTestId('prep-notes')).toContainText('Prefers short decks')
})
