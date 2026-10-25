// ACT-SURFACES: web.settings web.pane web.recording web.people
// Speakr follow-ups on the web: the person card's Recordings list and the task pane's
// "From:" line (recording.view on web.people and web.pane), Ask assistant from the recording sheet
// (recording.ask: the panel opens with the recording attached and each turn sends
// context.focused_recording), and recording orders in Settings → Assistant (order.create with the
// "When a new recording arrives" trigger, order.test on the newest recordings, order.run on the
// newest matching recording). Speakr is never contacted: mailr's /api/v1/speakr routes, the
// recording-order preview and Run now are Playwright route mocks; people, tasks and saved orders
// are real.
import type { Page, Request, Route } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'

const CID = 3
const RID = 12

function recording(extra: Record<string, unknown> = {}) {
  return {
    connector_id: CID, recording_id: RID, title: 'Weekly sync', meeting_date: '2026-09-28T15:00:00+00:00',
    duration_s: 2700, participants: ['Priya Natarajan', 'Evan'], tags: ['Work'], folder: 'Meetings', status: 'completed',
    has_transcript: true, has_summary: true, audio_available: true, is_archived: false, local_only: true,
    url: 'https://speakr.example/recordings/12', summary: '## Decisions\n- Ship the budget on Friday', notes: '',
    links: [], candidates: [], ...extra,
  }
}

const TRANSCRIPT = {
  segments: [
    { t: 0, end: 754, speaker: 'Dana', text: 'Welcome everyone to the weekly sync.' },
    { t: 754, end: 790, speaker: 'Dana', text: 'We agreed to ship the budget on Friday.' },
  ],
  truncated: false, next_start: null, total_segments: 2, timed: true, url: 'https://speakr.example/recordings/12',
}

/** Mocks every /api/v1/speakr route; `over` answers chosen "METHOD /path" keys; returns the paths asked. */
async function mockSpeakr(page: Page, connected: boolean, over: Record<string, (route: Route) => unknown> = {}): Promise<string[]> {
  const seen: string[] = []
  await page.route('**/api/v1/speakr/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1/speakr', '')
    const key = `${req.method()} ${path}`
    seen.push(`${key}${url.search}`)
    if (over[key]) return over[key](route)
    if (path === '/status') return route.fulfill({ json: { connected, connectors: connected ? [{ id: CID, name: 'Speakr', base_url: 'https://speakr.example' }] : [] } })
    if (key === `GET /recordings/${CID}/${RID}`) return route.fulfill({ json: recording() })
    if (key === `GET /recordings/${CID}/${RID}/transcript`) return route.fulfill({ json: TRANSCRIPT })
    if (key === 'GET /events/recordings') return route.fulfill({ json: { connected, items: [] } })
    return route.fulfill({ status: 404, json: { detail: `unmocked ${key}` } })
  })
  return seen
}

/** Answers every chat turn with a short canned reply and keeps the request bodies. */
async function fakeChat(page: Page): Promise<Record<string, unknown>[]> {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    bodies.push((route.request() as Request).postDataJSON() as Record<string, unknown>)
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: 'event: session\ndata: {"session_id":"e2e-rec"}\n\nevent: final\ndata: {"content":"Noted."}\n\nevent: done\ndata: {}\n\n',
    })
  })
  await page.route('**/api/v1/agent/sessions/e2e-rec', (route) => route.fulfill({ json: { id: 'e2e-rec', turns: [] } }))
  return bodies
}

test.describe('the person card', () => {
  test.use({ extras: true })

  test('recording.view on web.people: recent recordings with the person open the sheet', async ({ page }) => {
    const seen = await mockSpeakr(page, true, {
      'GET /people/recordings': (route) => route.fulfill({ json: {
        count: 7, local_only: true,
        items: [recording(), recording({ recording_id: 9, title: 'Budget review', meeting_date: '2026-09-14T15:00:00+00:00', duration_s: 1800 })],
      } }),
    })
    await page.goto('/settings/people')
    await page.getByLabel('Search people').fill('priya')
    await page.getByRole('button', { name: /Priya Natarajan/ }).click()
    const list = page.getByTestId('person-card').getByTestId('person-recordings')
    await expect(list.getByRole('heading', { name: 'Recordings (7)' })).toBeVisible()
    await expect(list.getByRole('button')).toHaveCount(2)
    await expect(list.getByRole('button').first()).toContainText('Weekly sync')
    await expect(list.getByRole('button').first()).toContainText('45 min')
    await expect(list).toContainText('The 2 most recent of 7.')
    expect(seen).toContain('GET /people/recordings?email=priya%40example.org&limit=5')
    await list.getByRole('button', { name: /Weekly sync/ }).click()
    const sheet = page.getByTestId('recording-sheet')
    await expect(sheet.getByRole('heading', { name: 'Weekly sync' })).toBeVisible()
    await expect(sheet).toContainText('Ship the budget on Friday')
  })

  test('recording.view on web.people: hidden without a Speakr account', async ({ page }) => {
    const seen = await mockSpeakr(page, false)
    await page.goto('/settings/people')
    await page.getByLabel('Search people').fill('priya')
    await page.getByRole('button', { name: /Priya Natarajan/ }).click()
    await expect(page.getByTestId('person-card')).toContainText('priya@example.org')
    await expect(page.getByTestId('person-recordings')).toHaveCount(0)
    expect(seen.some((s) => s.startsWith('GET /people/recordings'))).toBe(false)
  })
})

test('recording.view on web.pane and recording.ask: From the recording, then Ask assistant from the sheet', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const task = (await (await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Send the budget' } })).json()) as { uid: string }
  const plain = (await (await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Unrelated' } })).json()) as { uid: string }
  await mockSpeakr(page, true, {
    'GET /tasks/recordings': (route) => {
      const uid = new URL(route.request().url()).searchParams.get('uid')
      return route.fulfill({ json: { connected: true, items: uid === task.uid ? [{ ...recording(), t: 754 }] : [] } })
    },
  })
  const bodies = await fakeChat(page)

  // A task without a recording shows no From line.
  await page.goto(`/tasks/all/task/${encodeURIComponent(plain.uid)}`)
  await expect(page.getByTestId('task-pane')).toContainText('E2E Unrelated')
  await expect(page.getByTestId('task-recording')).toHaveCount(0)

  await page.goto(`/tasks/all/task/${encodeURIComponent(task.uid)}`)
  const from = page.getByTestId('task-pane').getByTestId('task-recording')
  await expect(from).toContainText('From: Weekly sync · 12:34')
  await from.getByRole('button', { name: /Weekly sync/ }).click()
  const sheet = page.getByTestId('recording-sheet')
  // Opened at the moment: the transcript, with the turn said then.
  await expect(sheet.getByRole('tab', { name: 'Transcript' })).toHaveAttribute('aria-selected', 'true')
  await expect(sheet).toContainText('We agreed to ship the budget on Friday.')
  await expect(sheet.getByRole('link', { name: 'Play in Speakr at 12:34' })).toBeVisible()

  // recording.ask and agent.ask_about: the sheet closes and the ask capture opens with the
  // recording attached; Open chat opens the panel about it.
  await sheet.getByRole('button', { name: 'Ask Ambrose' }).click()
  await expect(page.getByTestId('recording-sheet')).toHaveCount(0)
  await expect(page.getByTestId('ask-capture').getByTestId('ask-chip')).toContainText('Weekly sync')
  await page.getByTestId('ask-capture').getByRole('button', { name: 'Open chat' }).click()
  const chip = page.getByTestId('chat-about')
  await expect(chip).toContainText('Weekly sync')
  await page.getByRole('button', { name: 'What did we decide in this meeting?' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect((bodies[0].context as Record<string, unknown>).focused_recording).toEqual({ connector_id: CID, recording_id: RID })
  await chip.getByRole('button', { name: 'Detach' }).click()
  await page.getByRole('textbox', { name: 'Message Ambrose' }).fill('And now?')
  await page.keyboard.press('Enter')
  await expect.poll(() => bodies.length).toBe(2)
  expect((bodies[1].context as Record<string, unknown>).focused_recording).toBeUndefined()
})

test('order.create, order.test and order.run for a recording order in Settings', async ({ page }) => {
  await mockSpeakr(page, true)
  const previews: Record<string, unknown>[] = []
  await page.route('**/api/v1/assistant/orders/preview', async (route) => {
    previews.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ json: { scope: 'recordings', searched: 2, matched: 1, items: [
      { connector_id: CID, recording_id: 14, title: 'Advising: Dana', meeting_date: '2026-09-30T15:00:00+00:00', matched: true, local_only: true, answer: true, confidence: 0.9, reason: 'a student meeting' },
      { connector_id: CID, recording_id: RID, title: 'Weekly sync', meeting_date: '2026-09-28T15:00:00+00:00', matched: false, local_only: true },
    ] } })
  })
  let runs = 0
  await page.route('**/api/v1/assistant/orders/*/run', async (route) => {
    runs += 1
    const rec = { connector_id: CID, recording_id: 14, title: 'Advising: Dana', meeting_date: '2026-09-30T15:00:00+00:00' }
    await route.fulfill({ json: runs === 1
      ? { matched: true, already: false, recording: rec, run: { id: 1, status: 'queued', condition: {}, actions: [], error: '' } }
      : { matched: true, already: true, recording: rec, run: null } })
  })
  type O = { name: string; trigger: string; question: string; recordings: Record<string, unknown>; actions: Record<string, unknown>; deliver: string[]; errand: string; query: string }
  const orders = async () => ((await (await page.request.get('/api/v1/assistant/orders')).json()) as { items: O[] }).items

  await page.goto('/settings/assistant?section=orders')
  await expect(page.getByRole('heading', { name: 'When a recording arrives' })).toBeVisible()
  // ACT order.create: the trigger choice in a new standing order.
  await page.getByRole('button', { name: 'New order' }).click()
  const editor = page.getByRole('dialog', { name: /New (standing|recording) order/ })
  await editor.getByLabel('Trigger').selectOption('recording')
  await expect(page.getByRole('dialog', { name: 'New recording order' })).toBeVisible()
  await expect(editor.getByLabel('Mail it applies to (search)')).toHaveCount(0)
  await expect(editor.getByLabel('Archive')).toHaveCount(0)
  await editor.getByLabel('Name').fill('E2E Advising')
  await editor.getByLabel('Title or participants contain').fill('advising')
  await editor.getByLabel('Tag').fill('Work')
  await editor.getByLabel(/Only if .* says yes to/).fill('Is this a meeting with a student?')
  await editor.getByLabel('Notify me').check()
  await editor.getByLabel(/Work for/).fill('Draft the follow-up and propose tasks.')

  // ACT order.test: the newest recordings, which the filter and the question pick.
  await editor.getByRole('button', { name: /Check the newest recordings/ }).click()
  const test1 = editor.getByTestId('recording-test')
  await expect(test1).toContainText('1 of the 2 newest recordings would run')
  await expect(test1.locator('li').filter({ hasText: 'Advising: Dana' }).getByLabel('Would run')).toBeVisible()
  await expect(test1).toContainText('yes 90% — a student meeting')
  expect(previews[0]).toMatchObject({ trigger: 'recording', recordings: { q: 'advising', tag: 'Work' }, question: 'Is this a meeting with a student?' })

  await editor.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await orders()).find((o) => o.name === 'E2E Advising')?.trigger).toBe('recording')
  const saved = (await orders()).find((o) => o.name === 'E2E Advising')!
  expect(saved).toMatchObject({ recordings: { q: 'advising', tag: 'Work' }, question: 'Is this a meeting with a student?',
    actions: { notify: true }, deliver: ['notify'], query: '' })

  // The row says what it filters; Run now runs it on the newest matching recording, once.
  const row = page.locator('li.as-order').filter({ hasText: 'E2E Advising' })
  await expect(row).toContainText('title or participants contain “advising”, tagged Work')
  await expect(row).toContainText('Is this a meeting with a student?')
  // ACT order.run
  await row.getByRole('button', { name: 'Run now on the newest matching recording' }).click()
  await expect(page.getByText(/is working on “Advising: Dana”/)).toBeVisible()
  await row.getByRole('button', { name: 'Run now on the newest matching recording' }).click()
  await expect(page.getByText('Already ran on “Advising: Dana”, the newest matching recording. An order runs once per recording.')).toBeVisible()
})
