// ACT-SURFACES: web.sheet web.editor web.list web.reader web.settings web.calendar web.palette web.recording
// Speakr recordings on the web. Speakr itself is
// never contacted: mailr's own /api/v1/speakr routes (and the connector routes in the wizard) are
// Playwright route mocks, as the design's test plan says; calendars, drafts and the compose window
// are real.
// Covers: speakr.connect and speakr.sync (web.settings); recording.view, recording.open_speakr,
// recording.link, recording.unlink, recording.follow_up and recording.to_tasks (web.recording,
// web.calendar); recording.insert (web.editor). Phases 3 and 4: recording.view, recording.follow_up
// and recording.to_tasks in the reader card, mail.to_speakr (web.reader), recording.search (web.list
// chip and section, web.palette), recording.event_to_calendar, speaker.map, recording.edit and
// recording.share_link (web.recording), event.last_recording (web.calendar, web.sheet), note.from_recording
// (web.recording).
import type { Page, Route } from '@playwright/test'
import { createEvent, dayOf } from './calendar-data'
import { connectCalendar, conv, expect, test } from './fixtures'

const CID = 3
const RID = 12

interface Call { method: string; path: string; body: unknown }

function recording(extra: Record<string, unknown> = {}) {
  return {
    connector_id: CID, recording_id: RID, title: 'Weekly sync', meeting_date: new Date(Date.now() - 86_400_000).toISOString(),
    duration_s: 2700, participants: ['Dana', 'Evan'], tags: ['Work'], folder: 'Meetings', status: 'completed',
    has_transcript: true, has_summary: true, audio_available: true, is_archived: false, local_only: true,
    url: 'https://speakr.example/recordings/12',
    summary: '## Decisions\n- Ship the budget on Friday\n- Evan drafts the plan', notes: 'Bring the numbers.',
    links: [], candidates: [], ...extra,
  }
}

const TRANSCRIPT = {
  segments: [
    { t: 0, end: 12, speaker: 'Dana', text: 'Welcome everyone to the weekly sync.' },
    { t: 12, end: 754, speaker: 'Evan', text: 'The budget numbers are almost done.' },
    { t: 754, end: 790, speaker: 'Dana', text: 'We agreed to ship the budget on Friday.' },
    { t: 790, end: 810, speaker: 'Evan', text: 'I will draft the plan by Monday.' },
  ],
  truncated: false, next_start: null, total_segments: 4, timed: true, url: 'https://speakr.example/recordings/12',
}

const ITEMS = [
  { text: 'Send the budget to finance', owner: 'me', mine: true, due: '2026-10-09', t: 754, checked: true, already_added: false, url: 'https://speakr.example/recordings/12?t=754' },
  { text: 'Draft the plan', owner: 'Evan', mine: false, due: null, t: 790, checked: false, already_added: false, url: 'https://speakr.example/recordings/12?t=790' },
]

/** Mocks every /api/v1/speakr route; `over` answers chosen paths; returns the calls made. */
async function mockSpeakr(page: Page, over: Record<string, (route: Route, call: Call) => unknown> = {}): Promise<Call[]> {
  const calls: Call[] = []
  await page.route('**/api/v1/speakr/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1/speakr', '')
    const call = { method: req.method(), path, body: req.postData() ? req.postDataJSON() : null }
    calls.push(call)
    const key = `${req.method()} ${path}`
    if (over[key]) return over[key](route, call)
    if (path === '/status') return route.fulfill({ json: { connected: true, connectors: [{ id: CID, name: 'Speakr', base_url: 'https://speakr.example' }] } })
    if (key === `GET /recordings/${CID}/${RID}`) return route.fulfill({ json: recording() })
    if (key === `GET /recordings/${CID}/${RID}/transcript`) return route.fulfill({ json: TRANSCRIPT })
    if (key === `GET /recordings/${CID}/${RID}/summary`) return route.fulfill({ json: { summary: recording().summary, has_summary: true } })
    if (key === 'GET /recordings') return route.fulfill({ json: { items: [recording()] } })
    if (key === 'GET /events/recordings') return route.fulfill({ json: { connected: true, items: [] } })
    if (key === 'POST /links') return route.fulfill({ json: { id: 41, state: 'confirmed', ...(call.body as object) } })
    if (req.method() === 'DELETE' && path.startsWith('/links/')) return route.fulfill({ json: { id: Number(path.split('/').pop()), state: 'dismissed' } })
    if (key === `POST /recordings/${CID}/${RID}/action-items`) return route.fulfill({ json: { items: ITEMS } })
    if (key === `POST /recordings/${CID}/${RID}/tasks`) return route.fulfill({ json: { created: [{ uid: 't-1', summary: 'Send the budget to finance' }], pending: 0 } })
    return route.fulfill({ status: 404, json: { detail: `unmocked ${key}` } })
  })
  return calls
}

/** A real draft, so the compose window the follow-up opens can save it. */
async function realDraft(page: Page, subject: string) {
  const me = await (await page.request.get('/api/v1/me')).json()
  const r = await page.request.post('/api/v1/drafts', { data: { payload: { identity_id: me.identities[0].id, subject, to: [{ email: 'evan@guest.example', name: 'Evan' }], html: '<p>Here are the notes from Weekly sync.</p><ul><li>Ship the budget on Friday</li></ul>' } } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const d = (await r.json()).draft
  return { draft_id: d.id as number, conversation_id: d.conversation_id as number, payload: { identity_id: me.identities[0].id, to: [{ email: 'evan@guest.example', name: 'Evan' }], cc: [], bcc: [], subject, html: '<p>Here are the notes from Weekly sync.</p><ul><li>Ship the budget on Friday</li></ul>', attachments: [] }, to: ['evan@guest.example'], reply: false }
}

test('speakr.connect and speakr.sync: the wizard tests and connects, the card syncs and changes the data class', async ({ page }) => {
  const sent: Call[] = []
  let connected = false
  const card = { id: 77, kind: 'speakr', name: 'Speakr (dana)', preset: null, mode: 'mirror', data_class: 'local_only', retention_days: 0,
    delete_upstream: false, enabled: true, status: 'idle', status_detail: '312 recordings', last_sync_at: new Date().toISOString(),
    config: { base_url: 'https://speakr.example', speakr_username: 'dana', speakr_models_local: false }, speakr: { count: 312, processing: 0, auth_failed: false } }
  await page.route('**/api/v1/connectors**', async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    if (path.endsWith('/presets')) return route.fallback()
    const call = { method: req.method(), path, body: req.postData() ? req.postDataJSON() : null }
    sent.push(call)
    if (req.method() === 'GET' && path === '/api/v1/connectors') return route.fulfill({ json: connected ? [card] : [] })
    if (path === '/api/v1/connectors/test') return route.fulfill({ json: { ok: true, detail: 'Signed in to Speakr as Dana Speakr, 312 recordings.', folders: [], identities: [], extensions: [] } })
    if (req.method() === 'POST' && path === '/api/v1/connectors') { connected = true; return route.fulfill({ json: card }) }
    if (req.method() === 'PATCH') {
      card.data_class = (call.body as { data_class: string }).data_class
      return route.fulfill({ json: card })
    }
    if (path.endsWith('/sync')) return route.fulfill({ json: { ok: true } })
    return route.fulfill({ json: card })
  })
  await mockSpeakr(page)
  await page.goto('/settings/mail/accounts')
  await page.getByRole('button', { name: 'Add Speakr' }).click()
  const wiz = page.getByRole('dialog', { name: 'Connect Speakr' })
  await expect(wiz.getByText('Account Settings → API Tokens → Create Token')).toBeVisible()
  await wiz.getByLabel('Speakr address').fill('https://speakr.example')
  await wiz.getByLabel('API token').fill('spk_secret_token_value')
  await expect(wiz.getByRole('button', { name: 'Connect' })).toBeDisabled()
  await wiz.getByRole('button', { name: 'Test' }).click()
  await expect(wiz.getByText('Signed in to Speakr as Dana Speakr')).toBeVisible()
  await expect(wiz.getByLabel('Keep recordings away from hosted models')).toBeChecked()
  await wiz.getByRole('button', { name: 'Connect' }).click()
  await expect(wiz).toHaveCount(0)
  const create = sent.find((c) => c.method === 'POST' && c.path === '/api/v1/connectors')!
  expect(create.body).toEqual({ kind: 'speakr', name: '', config: { base_url: 'https://speakr.example' }, secrets: { token: 'spk_secret_token_value' }, data_class: 'local_only' })

  const c = page.getByTestId('speakr-card')
  await expect(c).toContainText('312 recordings')
  await expect(c).toContainText('local only')
  await expect(c).not.toContainText('spk_secret_token_value')
  await c.getByRole('button', { name: 'Sync now' }).click()
  await expect.poll(() => sent.some((x) => x.path === '/api/v1/connectors/77/sync')).toBe(true)
  // The switch follows the saved connector, so it changes once the server answered.
  await c.getByLabel('Keep recordings away from hosted models').click()
  await expect.poll(() => sent.find((x) => x.method === 'PATCH')?.body).toEqual({ data_class: 'normal' })
  await expect(c.getByLabel('Keep recordings away from hosted models')).not.toBeChecked()
  await expect(c).not.toContainText('local only')
  // Speakr is not a mail account.
  await expect(page.locator('section').filter({ hasText: 'Mail accounts' }).getByText('Speakr (dana)')).toHaveCount(0)
})

test('speakr.connect webhook: Get updates instantly sets it up in Speakr, Send test reports, Turn off deletes it', async ({ page }) => {
  const sent: Call[] = []
  const card = { id: 78, kind: 'speakr', name: 'Speakr (dana)', preset: null, mode: 'mirror', data_class: 'local_only', retention_days: 0,
    delete_upstream: false, enabled: true, status: 'idle', status_detail: '312 recordings', last_sync_at: new Date().toISOString(),
    config: { base_url: 'https://speakr.example', speakr_username: 'dana' } as Record<string, unknown>,
    speakr: { count: 312, processing: 0, auth_failed: false, webhook_path: null as string | null } }
  await page.route('**/api/v1/connectors**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/presets')) return route.fallback()
    return route.fulfill({ json: path === '/api/v1/connectors' ? [card] : card })
  })
  await mockSpeakr(page, {
    'POST /connectors/78/webhook': (route, call) => {
      sent.push(call)
      card.config = { ...card.config, webhook_id: 'hook1', speakr_webhook_id: 5 }
      card.speakr.webhook_path = '/api/v1/integrations/speakr/hook1/webhook'
      return route.fulfill({ json: { on: true, managed: true, path: card.speakr.webhook_path, url: 'https://mail.example/x', speakr_webhook_id: 5, events: [] } })
    },
    'POST /connectors/78/webhook/test': (route, call) => {
      sent.push(call)
      return route.fulfill({ json: { received: false, speakr_status: 'failed', response_status: null, error: 'blocked at delivery: private address',
        hint: 'On the Speakr server, set WEBHOOK_INTRANET_HOST_ALLOWLIST=^mail\\.example$ and restart Speakr, or keep the ten-minute checks.' } })
    },
    'DELETE /connectors/78/webhook': (route, call) => {
      sent.push(call)
      card.config = { base_url: 'https://speakr.example', speakr_username: 'dana' }
      card.speakr.webhook_path = null
      return route.fulfill({ json: { on: false, managed: false, path: null, url: null, speakr_webhook_id: null, events: [], deleted_in_speakr: true } })
    },
  })
  await page.goto('/settings/mail/accounts')
  const c = page.getByTestId('speakr-card')
  await expect(c.getByTestId('speakr-webhook')).toContainText('every ten minutes')
  await c.getByRole('button', { name: 'Get updates instantly…' }).click()
  const dlg = page.getByRole('dialog', { name: 'Instant updates from Speakr' })
  await expect(dlg).toContainText('ten-minute check')
  await dlg.getByRole('button', { name: 'Set up in Speakr' }).click()
  await expect(dlg).toHaveCount(0)
  await expect.poll(() => sent.map((x) => `${x.method} ${x.path}`)).toEqual(['POST /connectors/78/webhook'])
  await expect(c.getByTestId('speakr-webhook')).toContainText('Instant updates on.')
  await c.getByRole('button', { name: 'Send test' }).click()
  await expect(page.getByText('WEBHOOK_INTRANET_HOST_ALLOWLIST')).toBeVisible()
  // The notice sits over the bottom of the page, where the Speakr card now is (Mail → Mail accounts).
  await page.locator('.toast').filter({ hasText: 'WEBHOOK_INTRANET_HOST_ALLOWLIST' }).getByRole('button', { name: 'Dismiss' }).click()
  await c.getByRole('button', { name: 'Turn off' }).click()
  await expect.poll(() => sent.map((x) => `${x.method} ${x.path}`).at(-1)).toBe('DELETE /connectors/78/webhook')
  await expect(c.getByTestId('speakr-webhook')).toContainText('every ten minutes')
})

// ACT recording.view @web.panel: the chip is in the assistant panel's answer.
test('recording.view from a citation chip: the sheet opens at the moment; links, follow-up and tasks', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mailr.panel', 'agent'))
  await page.route('**/api/v1/agent/chat', (route) => route.fulfill({
    status: 200, headers: { 'Content-Type': 'text/event-stream' },
    body: 'event: session\ndata: {"session_id":"e2e-rec"}\n\n' +
      `event: final\ndata: ${JSON.stringify({ content: 'You agreed to ship on Friday [1](mailr-cite:1).', citations: [{ n: 1, kind: 'recording', recording_id: RID, connector_id: CID, title: 'Weekly sync', subject: 'Weekly sync', from: 'Dana', speaker: 'Dana', conversation_id: null, message_id: null, date: null, quote: 'agreed to ship the budget on Friday', verified: true, start: 754, t: 754, end: null, url: 'https://speakr.example/recordings/12?t=754' }] })}\n\n` +
      'event: done\ndata: {}\n\n',
  }))
  await page.route('**/api/v1/agent/sessions/e2e-rec', (route) => route.fulfill({ json: { id: 'e2e-rec', turns: [] } }))
  const draft = await realDraft(page, 'Follow-up: Weekly sync')
  const calls = await mockSpeakr(page, {
    [`GET /recordings/${CID}/${RID}`]: (route) => route.fulfill({ json: recording({
      links: [{ id: 9, connector_id: CID, recording_id: RID, kind: 'event', target: 'ev-1@x', occurrence: '2026-09-30T14:00:00+00:00', state: 'suggested', score: 0.9, source: 'auto', title: 'Weekly sync' }],
      candidates: [{ uid: 'ev-2@x', occurrence: '2026-09-30T14:00:00+00:00', summary: 'Budget huddle', start: '2026-09-30T14:00:00+00:00', end: null, score: 0.4 }],
    }) }),
    [`POST /recordings/${CID}/${RID}/follow-up`]: (route) => route.fulfill({ json: draft }),
  })
  await page.goto('/mail/inbox')
  await page.getByRole('textbox', { name: 'Message Ambrose' }).fill('What did we decide about the budget?')
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Recording 1: Weekly sync at 12:34' }).click()

  const sheet = page.getByTestId('recording-sheet')
  await expect(sheet.getByRole('heading', { name: 'Weekly sync' })).toBeVisible()
  await expect(sheet.getByRole('tab', { name: 'Transcript' })).toHaveAttribute('aria-selected', 'true')
  await expect(sheet.locator('li[data-at="true"]')).toContainText('We agreed to ship the budget on Friday.')
  await expect(sheet.getByRole('link', { name: 'Play in Speakr at 12:34' })).toHaveAttribute('href', 'https://speakr.example/recordings/12?t=754')
  await sheet.getByLabel('Find in transcript').fill('plan')
  await expect(sheet.locator('.rec-turns li')).toHaveCount(1)
  await sheet.getByRole('tab', { name: 'Summary' }).click()
  await expect(sheet.getByText('Ship the budget on Friday')).toBeVisible()
  await sheet.getByRole('tab', { name: 'Notes' }).click()
  await expect(sheet.getByText('Bring the numbers.')).toBeVisible()

  // Links: confirm the suggestion, link another event, dismiss.
  await sheet.getByRole('tab', { name: /Links/ }).click()
  await sheet.getByRole('button', { name: 'Confirm' }).click()
  await expect.poll(() => calls.find((c) => c.method === 'POST' && c.path === '/links')?.body).toEqual(
    { connector_id: CID, recording_id: RID, kind: 'event', target: 'ev-1@x', occurrence: '2026-09-30T14:00:00+00:00' })
  await sheet.getByRole('button', { name: 'Link', exact: true }).click()
  await expect.poll(() => calls.filter((c) => c.method === 'POST' && c.path === '/links').map((c) => (c.body as { target: string }).target)).toEqual(['ev-1@x', 'ev-2@x'])
  await sheet.getByRole('button', { name: 'Dismiss' }).click()
  await expect.poll(() => calls.some((c) => c.method === 'DELETE' && c.path === '/links/9')).toBe(true)

  // Follow-up email: a draft opens in compose, addressed to the guests, not sent.
  await sheet.getByRole('button', { name: 'Follow-up email' }).click()
  await expect.poll(() => calls.find((c) => c.path.endsWith('/follow-up'))?.body).toEqual({ mode: 'summary', event_uid: 'ev-1@x', occurrence: '2026-09-30T14:00:00+00:00' })
  await expect(page.getByLabel('Subject')).toHaveValue('Follow-up: Weekly sync')

  // Add tasks: the review list; Evan's item starts unticked.
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Recording 1: Weekly sync at 12:34' }).click()
  await page.getByTestId('recording-sheet').getByRole('button', { name: 'Add tasks' }).click()
  const dlg = page.getByRole('dialog', { name: 'Tasks from “Weekly sync”' })
  await expect(dlg.getByRole('checkbox', { name: /Send the budget to finance/ })).toBeChecked()
  await expect(dlg.getByRole('checkbox', { name: /Draft the plan/ })).not.toBeChecked()
  await dlg.getByRole('button', { name: 'Add 1 task' }).click()
  await expect.poll(() => calls.find((c) => c.path.endsWith('/tasks'))?.body).toEqual({
    items: [{ text: 'Send the budget to finance', due: '2026-10-09', t: 754 }], event_uid: 'ev-1@x', occurrence: '2026-09-30T14:00:00+00:00' })
  await expect(page.getByText('1 task added.')).toBeVisible()
})

test('recording.view at /recordings/<cid>/<rid> as a page', async ({ page }) => {
  await mockSpeakr(page)
  await page.goto(`/recordings/${CID}/${RID}`)
  const view = page.getByTestId('recording-sheet')
  await expect(view.getByRole('heading', { name: 'Weekly sync' })).toBeVisible()
  await expect(view.getByRole('tab', { name: 'Summary' })).toHaveAttribute('aria-selected', 'true')
  await expect(view.getByRole('link', { name: 'Open in Speakr' })).toHaveAttribute('href', 'https://speakr.example/recordings/12')
})

test('note.from_recording: Create note from recording in the Links tab (web.recording)', async ({ page }) => {
  await mockSpeakr(page)
  // The server writes the note from its mirror; this recording exists only in the mocks, so the
  // write is mocked too and answers with a real note, which the toast then opens.
  const made = (await (await page.request.post('/api/v1/notes', { data: { kind: 'text', title: 'Weekly sync', body: '**Summary**' } })).json()).note
  const sent: unknown[] = []
  await page.route('**/api/v1/notes/from-recording', (route) => {
    sent.push(route.request().postDataJSON())
    return route.fulfill({ json: { note: made, undo: null } })
  })
  await page.goto(`/recordings/${CID}/${RID}`)
  const view = page.getByTestId('recording-sheet')
  await view.getByRole('tab', { name: /Links/ }).click()
  await view.getByRole('button', { name: 'Create note from recording' }).click()
  await expect.poll(() => sent).toEqual([{ connector_id: CID, recording_id: RID }])
  await expect(page.getByText('Note created and linked')).toBeVisible()
  await page.locator('.toast-act', { hasText: 'Open' }).click()
  await expect(page).toHaveURL(new RegExp(`/notes/n/${made.id}$`))
})

test('Find recording… searches around the meeting and links the pick (recording.search, recording.link; web.calendar)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const y = new Date(Date.now() - 86_400_000)
  const day = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, '0')}-${String(y.getDate()).padStart(2, '0')}`
  const ev = await createEvent(page.request, { summary: 'Planning call', start: `${day}T10:00:00`, end: `${day}T11:00:00`, tz })
  const queries: URLSearchParams[] = []
  const calls = await mockSpeakr(page, {
    'GET /recordings': (route) => {
      queries.push(new URL(route.request().url()).searchParams)
      return route.fulfill({ json: { items: [recording()] } })
    },
  })
  await page.goto(`/calendar/week/${day}`)
  await page.getByTestId('cal-event').filter({ hasText: 'Planning call' }).first().click()
  await page.getByTestId('cal-recording-find').getByRole('button', { name: 'Find recording…' }).click()
  const finder = page.getByTestId('find-recording')
  // ACT recording.search: first the recordings from around the meeting's day, then a typed search.
  await expect(finder).toContainText('Weekly sync')
  expect(queries[0].get('from')).toBeTruthy()
  await finder.getByLabel('Search recordings').fill('budget')
  await expect.poll(() => queries.some((q) => q.get('q') === 'budget')).toBe(true)
  await finder.getByRole('button', { name: 'Link' }).click()
  await expect.poll(() => (calls.find((c) => c.method === 'POST' && c.path === '/links')?.body as { target: string } | undefined)?.target).toBe(ev.uid)
  await expect(page.locator('.toast').filter({ hasText: 'Linked “Weekly sync” to this meeting.' })).toBeVisible()
})

test('The event detail Recording section: confirm the suggestion, follow-up and tasks (web.calendar)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const ev = await createEvent(page.request, { summary: 'Budget review', start: `${dayOf(1)}T10:00:00`, end: `${dayOf(1)}T11:00:00`, tz })
  const draft = await realDraft(page, 'Follow-up: Budget review')
  let state = 'suggested'
  const calls = await mockSpeakr(page, {
    'GET /events/recordings': (route) => route.fulfill({ json: { connected: true, items: [{ ...recording({ title: 'Budget review' }), summary_excerpt: '## Decisions\n- Ship the budget on Friday',
      link: { id: 5, connector_id: CID, recording_id: RID, kind: 'event', target: ev.uid, occurrence: null, state, score: 0.9, source: 'auto' } }] } }),
    'POST /links': (route, call) => { state = 'confirmed'; return route.fulfill({ json: { id: 5, state: 'confirmed', ...(call.body as object) } }) },
    [`POST /recordings/${CID}/${RID}/follow-up`]: (route) => route.fulfill({ json: draft }),
  })
  await page.goto(`/calendar/week/${dayOf(1)}`)
  await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).first().click()
  const sec = page.getByTestId('cal-recording')
  await expect(sec).toContainText('Ship the budget on Friday')
  await sec.getByRole('group', { name: 'Is this the recording of this meeting?' }).getByRole('button', { name: 'Yes' }).click()
  await expect.poll(() => (calls.find((c) => c.method === 'POST' && c.path === '/links')?.body as { target: string } | undefined)?.target).toBe(ev.uid)
  await expect(sec.getByRole('group', { name: 'Is this the recording of this meeting?' })).toHaveCount(0)

  await sec.getByRole('button', { name: 'Add tasks' }).click()
  const dlg = page.getByRole('dialog', { name: 'Tasks from “Budget review”' })
  await dlg.getByRole('checkbox', { name: /Draft the plan/ }).check()
  await dlg.getByRole('button', { name: 'Add 2 tasks' }).click()
  await expect.poll(() => ((calls.find((c) => c.path.endsWith('/tasks'))?.body as { items: unknown[]; event_uid: string } | undefined)?.items ?? []).length).toBe(2)
  expect((calls.find((c) => c.path.endsWith('/tasks'))!.body as { event_uid: string }).event_uid).toBe(ev.uid)

  // "Open recording" opens the sheet over the calendar.
  await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).first().click()
  await page.getByTestId('cal-recording').getByRole('button', { name: 'Open recording' }).click()
  await expect(page.getByTestId('recording-sheet').getByRole('heading', { name: 'Weekly sync' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('recording-sheet')).toHaveCount(0)

  // Follow-up email: the draft opens in compose; nothing is sent.
  await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).first().click()
  await page.getByTestId('cal-recording').getByRole('button', { name: 'Follow-up email' }).click()
  await expect.poll(() => (calls.find((c) => c.path.endsWith('/follow-up'))?.body as { event_uid: string } | undefined)?.event_uid).toBe(ev.uid)
  await expect(page.getByLabel('Subject')).toHaveValue('Follow-up: Budget review')
})

test('recording.insert: Insert from a recording puts the summary or chosen turns in the draft', async ({ page }) => {
  await mockSpeakr(page)
  await page.goto('/mail/inbox?compose=1')
  const editor = page.locator('.compose-win [contenteditable="true"]').first()
  await expect(editor).toBeVisible()
  await page.getByRole('button', { name: 'Insert from a recording' }).click()
  const pop = page.getByRole('dialog', { name: 'Insert from a recording' })
  await pop.getByRole('button', { name: /Weekly sync/ }).click()
  await pop.getByRole('button', { name: 'Insert the summary' }).click()
  await expect(editor).toContainText('Ship the budget on Friday')
  await expect(editor).toContainText('From the recording “Weekly sync”')

  await page.getByRole('button', { name: 'Insert from a recording' }).click()
  await pop.getByRole('button', { name: /Weekly sync/ }).click()
  await pop.getByRole('button', { name: 'Choose transcript turns…' }).click()
  await pop.getByRole('checkbox', { name: /We agreed to ship/ }).check()
  await pop.getByRole('button', { name: 'Insert 1 turn' }).click()
  await expect(editor).toContainText('Dana (12:34): We agreed to ship the budget on Friday.')
})

// ---------------------------------------------------------------- writes

test('Recordings in mail: the reader card, the list chip, Transcribe in Speakr (web.reader, web.list)', async ({ page }) => {
  const c = await conv(page.request, 'E2E Read me')
  const draft = await realDraft(page, 'Follow-up: Weekly sync')
  // The message gets an audio attachment (the seed has none); everything else is the real message.
  await page.route(`**/api/v1/conversations/${c.id}`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    for (const m of body.messages) {
      m.has_attachments = true
      m.attachments = [...(m.attachments ?? []), { part: 7, filename: 'interview.mp3', type: 'audio/mpeg', size: 4096, inline: false }]
    }
    return route.fulfill({ response: res, json: body })
  })
  const calls = await mockSpeakr(page, {
    'GET /linked-conversations': (route) => route.fulfill({ json: { conversation_ids: [c.id] } }),
    'GET /mail-card': (route) => route.fulfill({ json: { connected: true, items: [{ ...recording(), summary_excerpt: 'We ship the budget on Friday',
      links: [{ id: 9, connector_id: CID, recording_id: RID, kind: 'event', target: 'ev-1@x', occurrence: '2026-09-30T14:00:00+00:00', state: 'confirmed', score: 1, source: 'user', title: 'Budget review', start: '2026-09-30T14:00:00+00:00' }] }] } }),
    'POST /upload': (route) => route.fulfill({ json: { connector_id: CID, recording_id: 99, title: 'interview', status: 'pending', url: 'https://speakr.example/recordings/99' } }),
    [`POST /recordings/${CID}/${RID}/follow-up`]: (route) => route.fulfill({ json: draft }),
  })
  await page.goto('/mail/inbox')
  const row = page.locator(`.conv-row[data-id="${c.id}"]`)
  await expect(row.getByTestId('recording-chip')).toBeVisible()
  await expect(page.getByTestId('recording-chip')).toHaveCount(1)

  await row.click()
  const card = page.getByTestId('recording-mail-card').first()
  await expect(card).toContainText('Weekly sync')
  await expect(card).toContainText('We ship the budget on Friday')
  await expect(card).toContainText('Linked to Budget review')
  await expect(card.getByRole('link', { name: 'Open in Speakr' })).toHaveAttribute('href', 'https://speakr.example/recordings/12')
  expect(calls.find((x) => x.path === '/mail-card')).toBeTruthy()

  // Transcribe in Speakr on the audio attachment (mail.to_speakr).
  await page.getByRole('button', { name: 'Transcribe interview.mp3 in Speakr' }).first().click({ force: true })
  await expect.poll(() => (calls.find((x) => x.path === '/upload')?.body as { part: number } | undefined)?.part).toBe(7)
  await expect(page.getByText('Sent to Speakr: “interview”')).toBeVisible()

  // Follow-up from the card: to the linked event's guests, in compose.
  await card.getByRole('button', { name: 'Follow-up email' }).click()
  await expect.poll(() => calls.find((x) => x.path.endsWith('/follow-up'))?.body).toEqual({ mode: 'summary', event_uid: 'ev-1@x', occurrence: '2026-09-30T14:00:00+00:00' })
  await expect(page.getByLabel('Subject')).toHaveValue('Follow-up: Weekly sync')
})

test('Recordings in search and the palette: the Recordings section, Open recording, Search recordings, the latest follow-up (web.list, web.palette)', async ({ page }) => {
  const draft = await realDraft(page, 'Follow-up: Weekly sync')
  const calls = await mockSpeakr(page, {
    'GET /linked-conversations': (route) => route.fulfill({ json: { conversation_ids: [] } }),
    [`POST /recordings/${CID}/${RID}/follow-up`]: (route) => route.fulfill({ json: draft }),
  })
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('waiting-bell')).toBeVisible()
  const input = page.getByRole('combobox', { name: 'Command' })
  await page.keyboard.press('Control+k')
  await input.fill('Open recording')
  await page.getByRole('option', { name: /Open recording…/ }).click()
  await expect(input).toHaveValue('Recording: ')
  await input.pressSequentially('week')
  await expect.poll(() => calls.some((x) => x.path === '/recordings')).toBe(true)
  await page.getByRole('option', { name: /Open recording: Weekly sync/ }).click()
  await expect(page.getByTestId('recording-sheet').getByRole('heading', { name: 'Weekly sync' })).toBeVisible()
  // With the sheet open, the palette offers its follow-up.
  await page.keyboard.press('Control+k')
  await input.fill('Follow-up email for')
  await expect(page.getByRole('option', { name: 'Follow-up email for “Weekly sync”' })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('recording-sheet')).toHaveCount(0)

  await page.keyboard.press('Control+k')
  await input.fill('budget')
  await page.getByRole('option', { name: 'Search recordings for “budget”' }).click()
  await expect(page).toHaveURL(/recording(%3A|:)budget/)
  const sec = page.getByRole('region', { name: 'Recordings' })
  await expect(sec.getByRole('button', { name: /Weekly sync/ })).toBeVisible()
  expect(calls.filter((x) => x.path === '/recordings').length).toBeGreaterThan(1)
  await sec.getByRole('button', { name: /Weekly sync/ }).click()
  await expect(page.getByTestId('recording-sheet')).toBeVisible()
  await page.keyboard.press('Escape')

  await page.keyboard.press('Control+k')
  await input.fill('latest meeting')
  await page.getByRole('option', { name: 'Follow-up from the latest meeting' }).click()
  await expect.poll(() => calls.find((x) => x.path.endsWith('/follow-up'))?.body).toEqual({ mode: 'summary' })
  await expect(page.getByLabel('Subject')).toHaveValue('Follow-up: Weekly sync')
})

test('The sheet: Events into the calendar, People, Edit and Share link (web.recording)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const calls = await mockSpeakr(page, {
    [`GET /recordings/${CID}/${RID}`]: (route) => route.fulfill({ json: recording({ write_back: true, share_links: true }) }),
    [`GET /recordings/${CID}/${RID}/events`]: (route) => route.fulfill({ json: { events: [{ title: 'Budget review', start: `${new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10)}T15:00:00Z`, end: null, location: 'Room 4', description: 'Bring numbers' }] } }),
    [`GET /recordings/${CID}/${RID}/people`]: (route) => route.fulfill({ json: { speakers: [
      { label: 'SPEAKER_00', name: 'Dana Smith', key: 'id:11', speaker_id: 11, person: null, suggestions: [{ email: 'dana@guest.example', name: 'Dana Smith', why: 'a guest of the meeting with this name' }] },
      { label: 'SPEAKER_01', name: 'Evan', key: 'id:12', speaker_id: 12, person: { email: 'evan@guest.example', name: 'Evan Lee' }, suggestions: [] },
    ], guests: [{ email: 'dana@guest.example', name: 'Dana Smith' }] } }),
    [`POST /speakers/${CID}/person`]: (route, call) => route.fulfill({ json: { state: 'confirmed', ...(call.body as object) } }),
    [`PATCH /recordings/${CID}/${RID}`]: (route) => route.fulfill({ json: { recording: recording({ title: 'Budget sync' }), previous: { title: 'Weekly sync', participants: ['Dana', 'Evan'] }, notes_appended: false } }),
    [`POST /recordings/${CID}/${RID}/share`]: (route) => route.fulfill({ json: { share_url: 'https://speakr.example/share/12-abc', existing: false } }),
  })
  await page.goto(`/recordings/${CID}/${RID}`)
  const view = page.getByTestId('recording-sheet')

  await view.getByRole('tab', { name: 'People' }).click()
  await expect(view.getByTestId('rec-speaker').filter({ hasText: 'Evan' })).toContainText('Evan Lee')
  await view.getByRole('button', { name: 'This is Dana Smith' }).click()
  await expect.poll(() => calls.find((x) => x.path === `/speakers/${CID}/person`)?.body).toEqual(
    // speaker_id and recording_id let the mapping follow a rename in Speakr.
    { key: 'id:11', speaker_name: 'Dana Smith', email: 'dana@guest.example', name: 'Dana Smith', speaker_id: 11, recording_id: RID })

  await view.getByRole('button', { name: 'Edit' }).click()
  const dlg = page.getByRole('dialog', { name: 'Edit in Speakr' })
  await dlg.getByLabel('Title').fill('Budget sync')
  await dlg.getByRole('button', { name: 'Save in Speakr' }).click()
  await expect.poll(() => calls.find((x) => x.method === 'PATCH')?.body).toEqual({ title: 'Budget sync' })
  await expect(page.getByText('Changed in Speakr.')).toBeVisible()
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect.poll(() => calls.filter((x) => x.method === 'PATCH').map((x) => x.body)).toEqual([{ title: 'Budget sync' }, { title: 'Weekly sync', participants: ['Dana', 'Evan'] }])

  await view.getByRole('button', { name: 'Share link' }).click()
  const ask = page.getByRole('dialog', { name: 'Create a public link?' })
  await expect(ask).toContainText('Anyone with the link can open')
  expect(calls.some((x) => x.path.endsWith('/share'))).toBe(false)
  await ask.getByRole('button', { name: 'Create public link' }).click()
  await expect(page.getByRole('dialog', { name: 'Public link' }).getByLabel('Share link')).toHaveValue('https://speakr.example/share/12-abc')
  await page.getByRole('button', { name: 'Done' }).click()

  // Events: Add to calendar opens the event editor, prefilled; nothing is created before Save.
  await view.getByRole('tab', { name: 'Events' }).click()
  await expect(view).toContainText('Room 4')
  await view.getByRole('button', { name: 'Add to calendar' }).click()
  await expect(page).toHaveURL(/\/calendar/)
  await expect(page.getByRole('textbox', { name: /Title/ }).first()).toHaveValue('Budget review')
})

test('Last time on an upcoming event and its reminder (event.last_recording)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const ev = await createEvent(page.request, { summary: 'Lab meeting', start: `${dayOf(1)}T10:00:00`, end: `${dayOf(1)}T11:00:00`, tz })
  const last = { ...recording({ title: 'Lab meeting' }), why: 'series', summary_line: 'Pilot results are in' }
  const calls = await mockSpeakr(page, {
    'GET /events/last-time': (route) => route.fulfill({ json: { connected: true, item: last, upcoming: true } }),
  })
  await page.goto(`/calendar/week/${dayOf(1)}`)
  await page.getByTestId('cal-event').filter({ hasText: 'Lab meeting' }).first().click()
  const lt = page.getByTestId('last-time')
  await expect(lt).toContainText('Last time:')
  await expect(lt).toContainText('Pilot results are in')
  expect((calls.find((x) => x.path === '/events/last-time'))).toBeTruthy()
  await lt.getByRole('button', { name: 'Lab meeting' }).click()
  await expect(page.getByTestId('recording-sheet').getByRole('heading', { name: 'Weekly sync' })).toBeVisible()
  await page.keyboard.press('Escape')

  // The event's reminder shows the same line.
  const start = new Date(Date.now() + 20 * 60_000).toISOString()
  await page.route('**/api/v1/reminders/active*', (r) => r.fulfill({ json: { items: [{ key: `e|${ev.uid}|${start}|10`, kind: 'event', uid: ev.uid, occurrence: start, minutes: 10,
    title: 'Lab meeting', detail: '', start, fire_at: start, fired_at: new Date().toISOString(), missed: false, state: 'fired', actions: ['snooze', 'dismiss'] }] } }))
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  await page.getByRole('dialog', { name: 'Waiting for you' }).getByTestId('reminder-row').first().click()
  await expect(page.getByTestId('reminder-sheet').getByTestId('last-time')).toContainText('Pilot results are in')
})
