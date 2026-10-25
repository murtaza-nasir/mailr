// ACT-SURFACES: web.panel web.calendar web.palette web.recordings
// The Recordings app (web.recordings). Speakr is
// never contacted: mailr's own /api/v1/speakr routes are Playwright route mocks, and the browser's
// microphone and MediaRecorder are fakes installed with addInitScript (a tone from an oscillator and
// a recorder that hands out a small chunk every timeslice). With RECORDINGS_SHOTS=<dir> the app is
// also saved as screenshots, light and dark, in every theme.
// Covers: recording.view, recording.search and recording.record (web.recordings); the nav entries
// in all five themes; recording.link and recording.record from the event detail (web.calendar);
// event.last_recording in the calendar panel (web.panel).
import type { Page, Route } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { createEvent } from './calendar-data'
import { connectCalendar, expect, test } from './fixtures'

const CID = 3
const SHOTS = process.env.RECORDINGS_SHOTS
type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'

interface Call { method: string; path: string; query: URLSearchParams; body: string | null }

const at = (daysAgo: number, h: number, m = 0) => {
  const d = new Date()
  d.setDate(d.getDate() - daysAgo)
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}

function rec(rid: number, title: string, meeting: string, extra: Record<string, unknown> = {}) {
  return {
    connector_id: CID, recording_id: rid, title, meeting_date: meeting, created_at: meeting, duration_s: 2700,
    participants: ['Dana Smith', 'Evan Lee'], tags: ['Work'], folder: 'Meetings', status: 'completed',
    has_transcript: true, has_summary: true, audio_available: true, is_archived: false, local_only: true,
    url: `https://speakr.example/recordings/${rid}`, event_link: null,
    people: [{ name: 'Dana Smith', email: 'dana@guest.example' }, { name: 'Evan Lee', email: null }],
    ...extra,
  }
}

const evLink = (rid: number, state: 'confirmed' | 'suggested', title: string) =>
  ({ id: 100 + rid, connector_id: CID, recording_id: rid, kind: 'event', target: `ev-${rid}@x`, occurrence: '', state, score: 0.9, source: 'auto', title, start: at(0, 10) })

const RECS = [
  rec(21, 'Weekly sync', at(0, 10), { event_link: evLink(21, 'confirmed', 'Weekly sync'), tags: ['Work', 'Planning'] }),
  rec(22, 'Advising: Mara', at(0, 14, 30), { participants: ['Mara Lopez'], people: [{ name: 'Mara Lopez', email: 'mara@student.example' }], folder: 'Advising', tags: ['Students'], duration_s: 1500, status: 'processing' }),
  rec(23, 'Budget review with finance', at(1, 15), { participants: ['Dana Smith', 'Priya Shah', 'Evan Lee', 'Omar Haddad', 'Lena Kim'], event_link: evLink(23, 'suggested', 'Budget review'), duration_s: 3900 }),
  rec(24, 'Grant brainstorm', at(3, 9, 15), { participants: ['Evan Lee'], people: [{ name: 'Evan Lee', email: null }], tags: ['Research'], folder: 'Research', duration_s: 1980, local_only: false }),
  rec(25, 'Thesis committee', at(5, 13), { participants: ['Mara Lopez', 'Dana Smith'], folder: 'Advising', tags: ['Students', 'Research'] }),
  rec(26, 'Phone call with the dean', at(9, 11), { participants: [], people: [], tags: [], folder: null, duration_s: 600, status: 'failed' }),
  rec(27, 'Course redesign workshop', at(16, 10), { participants: ['Priya Shah'], people: [{ name: 'Priya Shah', email: 'priya@guest.example' }], tags: ['Teaching'], folder: 'Teaching', duration_s: 5400 }),
]

const SUMMARY = '## Decisions\n- Ship the budget on Friday\n- Evan drafts the plan by Monday\n\n## Follow-ups\n- Dana sends the numbers to finance'
const sheetOf = (r: ReturnType<typeof rec>) => ({ ...r, summary: SUMMARY, notes: 'Bring the numbers.', links: r.event_link ? [r.event_link] : [], candidates: [] })

/** Mocks /api/v1/speakr; `connected` switches the account; `over` answers chosen requests. Returns the calls made. */
async function mockSpeakr(page: Page, { connected = true, over = {} as Record<string, (route: Route, call: Call) => unknown> } = {}): Promise<Call[]> {
  const calls: Call[] = []
  await page.route('**/api/v1/speakr/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1/speakr', '')
    const call = { method: req.method(), path, query: url.searchParams, body: req.postData() }
    calls.push(call)
    const key = `${req.method()} ${path}`
    if (over[key]) return over[key](route, call)
    if (path === '/status') return route.fulfill({ json: connected ? { connected: true, connectors: [{ id: CID, name: 'Speakr', base_url: 'https://speakr.example' }] } : { connected: false, connectors: [] } })
    if (key === 'GET /recordings') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase()
      if (!q) return route.fulfill({ json: { items: RECS, more: false } })
      const items = RECS.filter((r) => r.title.toLowerCase().includes(q) || q.startsWith('budg')).map((r) => ({ ...r, summary_match: q.startsWith('budg') ? 'Ship the budget on Friday' : null }))
      return route.fulfill({ json: { items, more: false } })
    }
    if (key === 'GET /folders') return route.fulfill({ json: { connector_id: CID, folders: [{ id: 1, name: 'Meetings' }, { id: 2, name: 'Advising' }, { id: 3, name: 'Research' }, { id: 4, name: 'Teaching' }, { id: 5, name: 'Archive' }] } })
    if (key === 'GET /tags') return route.fulfill({ json: { connector_id: CID, tags: [{ id: 1, name: 'Work', color: '#1a73e8' }, { id: 2, name: 'Students', color: '#188038' }, { id: 3, name: 'Research', color: '#a142f4' }, { id: 4, name: 'Planning' }, { id: 5, name: 'Teaching' }] } })
    const m = /^\/recordings\/(\d+)\/(\d+)$/.exec(path)
    if (req.method() === 'GET' && m) {
      const r = RECS.find((x) => x.recording_id === Number(m[2])) ?? rec(Number(m[2]), 'New meeting', new Date().toISOString(), { status: 'pending' })
      return route.fulfill({ json: sheetOf(r) })
    }
    if (path.endsWith('/transcript')) return route.fulfill({ json: { segments: [{ t: 0, end: 10, speaker: 'Dana Smith', text: 'Welcome everyone.' }], truncated: false, next_start: null, total_segments: 1, timed: true, url: '' } })
    if (key === 'GET /events/recordings') return route.fulfill({ json: { connected: true, items: [] } })
    if (key === 'GET /events/last-time') return route.fulfill({ json: { connected: true, item: null } })
    if (key === 'POST /links') return route.fulfill({ json: { id: 77, state: 'confirmed', ...JSON.parse(call.body ?? '{}') } })
    if (req.method() === 'DELETE' && path.startsWith('/links/')) return route.fulfill({ json: { id: 77, state: 'dismissed' } })
    return route.fulfill({ status: 404, json: { detail: `unmocked ${key}` } })
  })
  return calls
}

/** A fake microphone (an oscillator) and a fake MediaRecorder that emits a 2 kB chunk each timeslice. */
async function fakeMic(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>
    class FakeRecorder extends EventTarget {
      static isTypeSupported(t: string) { return t.startsWith('audio/webm') }
      state: 'inactive' | 'recording' | 'paused' = 'inactive'
      mimeType = 'audio/webm;codecs=opus'
      ondataavailable: ((e: { data: Blob }) => void) | null = null
      onstop: (() => void) | null = null
      onerror: ((e: unknown) => void) | null = null
      private timer: number | null = null
      stream: MediaStream
      constructor(stream: MediaStream) { super(); this.stream = stream }
      private emit() { this.ondataavailable?.({ data: new Blob([new Uint8Array(2048)], { type: this.mimeType }) }) }
      start(slice = 1000) {
        this.state = 'recording'
        ;(w.__recStarted as number) = ((w.__recStarted as number) ?? 0) + 1
        this.timer = window.setInterval(() => this.state === 'recording' && this.emit(), Math.min(slice, 400))
      }
      pause() { this.state = 'paused' }
      resume() { this.state = 'recording' }
      stop() {
        if (this.timer) window.clearInterval(this.timer)
        this.emit()
        this.state = 'inactive'
        setTimeout(() => this.onstop?.(), 10)
      }
    }
    w.MediaRecorder = FakeRecorder
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          ;(w.__micAsked as number) = ((w.__micAsked as number) ?? 0) + 1
          const ctx = new AudioContext()
          const osc = ctx.createOscillator()
          const dest = ctx.createMediaStreamDestination()
          osc.connect(dest)
          osc.start()
          return dest.stream
        },
      },
    })
  })
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(150)
  await page.screenshot({ path: `${SHOTS}/${name}.png` })
}

test('web.recordings nav: shown only with a Speakr account, in Classic and Office; g r goes there', async ({ page }) => {
  await mockSpeakr(page, { connected: false })
  await page.goto('/mail/inbox')
  await expect(page.locator('.sidebar').getByRole('link', { name: 'Calendar' }).first()).toBeVisible()
  await expect(page.locator('[data-app-nav="recordings"], .sidebar a[href^="/recordings"]')).toHaveCount(0)
  // Without an account the app explains how to connect one.
  await page.goto('/recordings')
  await expect(page.getByTestId('recordings-no-account')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Connect Speakr' })).toHaveAttribute('href', '/settings/mail/accounts#speakr')
})

test('web.recordings nav with an account: the Classic sidebar\'s Apps; g r goes there', async ({ page }) => {
  await mockSpeakr(page)
  await page.goto('/mail/inbox')
  const side = page.locator('.sidebar a[href^="/recordings"]')
  await expect(side).toBeVisible()
  await side.click()
  await expect(page).toHaveURL(/\/recordings$/)
  await expect(page.getByTestId('recordings-app')).toBeVisible()
  // g r from mail.
  await page.goto('/mail/inbox')
  // The g r binding exists once the app knows there is a Speakr account (the sidebar link shows it)
  // and the list has loaded and taken the keys.
  await expect(side).toBeVisible()
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  await page.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => {})
  await page.keyboard.press('g')
  await page.keyboard.press('r')
  await expect(page).toHaveURL(/\/recordings/)

})

for (const [theme, sel] of [['office', '.o-appbar [data-app-nav="recordings"]'], ['desk', '.d-spaces [data-space="recordings"]'], ['cards', '.c-rail [data-app-nav="recordings"]'], ['focus', '']] as const) {
  test(`web.recordings nav in ${theme}`, async ({ page }) => {
    await mockSpeakr(page)
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/mail/inbox')
    if (sel) {
      await expect(page.locator(sel)).toBeVisible()
      await page.locator(sel).click()
    } else {
      // Focus has no nav bar: the palette goes there.
      await expect(page.getByTestId('waiting-bell')).toBeVisible()
      await page.keyboard.press('Control+k')
      await page.getByRole('combobox', { name: 'Command' }).fill('Go to Recordings')
      await page.getByRole('option', { name: /Go to Recordings/ }).first().click()
    }
    await expect(page.getByTestId('recordings-app')).toHaveClass(new RegExp(`rx-theme-${theme}`))
    await expect(page.getByTestId('recording-row').first()).toBeVisible()
    if (!sel) {
      // ACT recording.record from the palette: "New recording" opens the recorder.
      await page.keyboard.press('Control+k')
      await page.getByRole('combobox', { name: 'Command' }).fill('New recording')
      await page.getByRole('option', { name: /New recording/ }).first().click()
      await expect(page.getByRole('dialog', { name: 'New recording' })).toBeVisible()
    }
  })
}

test('recording.view and recording.search: the list by day, the search with its summary line, the pane', async ({ page }) => {
  const calls = await mockSpeakr(page)
  await page.goto('/recordings')
  const app = page.getByTestId('recordings-app')
  const rows = page.getByTestId('recording-row')
  await expect(rows).toHaveCount(RECS.length)
  // Annotated pages of the whole mirror.
  expect(calls.find((c) => c.path === '/recordings')?.query.get('annotate')).toBe('true')
  await expect(app.locator('.rx-day').first()).toContainText('Today')
  await expect(app.locator('.rx-day').nth(1)).toContainText('Yesterday')
  const sync = rows.filter({ hasText: 'Weekly sync' })
  await expect(sync.locator('.rx-event')).toContainText('Weekly sync')
  await expect(rows.filter({ hasText: 'Budget review with finance' }).locator('.rx-event.suggested')).toContainText('Suggested')
  await expect(rows.filter({ hasText: 'Advising: Mara' }).locator('.rx-status')).toHaveText('Processing')
  await expect(rows.filter({ hasText: 'Phone call with the dean' }).locator('.rx-status')).toHaveText('Failed')
  await expect(sync.getByLabel('Local only')).toBeVisible()
  await expect(rows.filter({ hasText: 'Grant brainstorm' }).getByLabel('Local only')).toHaveCount(0)
  await shot(page, 'list-classic-light')

  // ACT recording.search: the summary index, the matching line with the words marked.
  await page.getByRole('searchbox', { name: 'Search recordings' }).fill('budget')
  await expect(page).toHaveURL(/q=budget/)
  await expect.poll(() => calls.some((c) => c.path === '/recordings' && c.query.get('q') === 'budget')).toBe(true)
  await expect(page.getByTestId('summary-match').first().locator('mark')).toHaveText('budget')
  await shot(page, 'search-classic-light')

  // ACT recording.view: the pane (Classic puts it on the right); Escape closes it.
  await rows.first().locator('.rx-row-hit').click()
  await expect(page).toHaveURL(new RegExp(`/recordings/${CID}/\\d+`))
  const pane = page.getByTestId('recording-pane')
  await expect(pane.getByTestId('recording-sheet')).toBeVisible()
  await expect(pane.getByRole('link', { name: 'Open in Speakr' })).toBeVisible()
  await expect(pane.getByRole('button', { name: 'Add tasks' })).toBeVisible()
  await expect(pane.getByRole('button', { name: 'Ask Ambrose' })).toBeVisible()
  await expect(pane.getByText('Ship the budget on Friday')).toBeVisible()
  await shot(page, 'pane-classic-light')
  await page.locator('.rx-day').first().click()
  await page.keyboard.press('Escape')
  await expect(pane).toHaveCount(0)
  // The search survives opening and closing a recording.
  await expect(page.getByRole('searchbox', { name: 'Search recordings' })).toHaveValue('budget')

  // Deep link straight to a recording.
  await page.goto(`/recordings/${CID}/25`)
  await expect(page.getByTestId('recording-pane').getByRole('heading', { name: 'Thesis committee' })).toBeVisible()
  await expect(rows.filter({ hasText: 'Thesis committee' })).toHaveClass(/active/)
})

test('recording.search filters: recent, linked, not linked, folders and tags with counts, participants', async ({ page }) => {
  await mockSpeakr(page)
  await page.goto('/recordings')
  const side = page.getByTestId('recordings-sidebar')
  const rows = page.getByTestId('recording-row')
  await expect(rows).toHaveCount(7)
  await expect(side.getByRole('button', { name: /^All/ })).toContainText('7')
  await side.getByRole('button', { name: /^Recent/ }).click()
  await expect(page).toHaveURL(/\/recordings\/recent$/)
  await expect(rows).toHaveCount(5)
  await side.getByRole('button', { name: /^Linked to events/ }).click()
  await expect(rows).toHaveCount(2)
  await side.getByRole('button', { name: /^Not linked/ }).click()
  await expect(rows).toHaveCount(5)
  const folders = side.getByRole('group', { name: 'Folders' })
  await expect(folders.getByRole('button', { name: /^Advising/ })).toContainText('2')
  // A Speakr folder with no recordings is listed, with 0.
  await expect(folders.getByRole('button', { name: /^Archive/ })).toContainText('0')
  await folders.getByRole('button', { name: /^Advising/ }).click()
  await expect(page).toHaveURL(/\/recordings\/f\/Advising$/)
  await expect(rows).toHaveCount(2)
  await side.getByRole('group', { name: 'Tags' }).getByRole('button', { name: /^Research/ }).click()
  await expect(rows).toHaveCount(2)
  await side.getByRole('button', { name: /^All/ }).click()
  // Participants: choose two; only recordings with both stay.
  const ppl = side.getByRole('group', { name: 'Participants' })
  await ppl.getByRole('button', { name: /Dana Smith/ }).click()
  await expect(rows).toHaveCount(3)
  await ppl.getByRole('button', { name: /Mara Lopez/ }).click()
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText('Thesis committee')
  await shot(page, 'filters-classic-light')
  await page.getByRole('button', { name: /^Dana Smith/ }).filter({ has: page.getByLabel('Remove Dana Smith') }).click()
  await expect(rows).toHaveCount(2)
  // Empty state for a filter with nothing in it.
  await folders.getByRole('button', { name: /^Archive/ }).click()
  await expect(page.getByTestId('recordings-empty')).toContainText('No recordings here')
})

test('recording.record: record in the browser, pause, notes, stop and upload with the fields; the sheet opens', async ({ page }) => {
  await fakeMic(page)
  let uploaded: string | null = null
  const calls = await mockSpeakr(page, {
    over: {
      'POST /recordings': async (route, call) => {
        uploaded = route.request().postDataBuffer()?.toString('latin1') ?? call.body
        await new Promise((r) => setTimeout(r, 300))
        return route.fulfill({ json: { connector_id: CID, recording_id: 99, title: 'Lab meeting', status: 'pending', url: 'https://speakr.example/recordings/99', unsupported_fields: [], kept_in_mailr: [] } })
      },
    },
  })
  await page.goto('/recordings')
  await page.getByRole('button', { name: 'New recording' }).first().click()
  const dlg = page.getByRole('dialog', { name: 'New recording' })
  await expect(dlg).toBeVisible()
  // The microphone is asked for only on Record.
  expect(await page.evaluate(() => (window as unknown as { __micAsked?: number }).__micAsked ?? 0)).toBe(0)
  await dlg.getByLabel('Title').fill('Lab meeting')
  await dlg.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Recording…' })).toBeVisible()
  const rd = page.getByRole('dialog', { name: /Recording/ })
  await expect(rd.getByRole('meter', { name: 'Microphone level' })).toBeVisible()
  await expect(rd.getByTestId('rx-clock')).toHaveText(/0:0[1-9]/, { timeout: 5000 })
  await rd.getByRole('button', { name: 'Pause' }).click()
  await expect(page.getByRole('dialog', { name: 'Recording paused' })).toBeVisible()
  await rd.getByRole('button', { name: 'Resume' }).click()
  // Notes while recording, in Markdown, with a preview.
  await rd.getByRole('textbox', { name: 'Notes' }).fill('- Decided to **order** the new scope')
  await rd.getByRole('tab', { name: 'Preview' }).click()
  await expect(rd.locator('.rx-notes-preview strong')).toHaveText('order')
  // Participants with People suggestions (typed names also work).
  await rd.getByLabel('Participants').fill('Dana Smith')
  await rd.getByLabel('Participants').press('Enter')
  await expect(rd.getByRole('button', { name: 'Remove Dana Smith' })).toBeVisible()
  await rd.getByLabel('Folder').selectOption({ label: 'Research' })
  await rd.getByRole('button', { name: 'Students' }).click()
  await shot(page, 'recorder-recording-light')
  // Minimise: the recording goes on in the bar.
  await rd.getByRole('button', { name: 'Minimise' }).click()
  const bar = page.getByTestId('recorder-bar')
  await expect(bar).toBeVisible()
  await expect(bar).toContainText('Lab meeting')
  await shot(page, 'recorder-bar-light')
  await bar.getByRole('button', { name: 'Stop' }).click()
  const done = page.getByRole('dialog', { name: 'New recording' })
  await expect(done.getByText('Stopped. Ready to upload.')).toBeVisible()
  await expect(done.locator('audio')).toBeVisible()
  await shot(page, 'recorder-stopped-light')
  await done.getByTestId('rx-upload').click()
  await expect(page).toHaveURL(new RegExp(`/recordings/${CID}/99$`))
  await expect(page.getByTestId('recording-pane')).toBeVisible()
  expect(calls.some((c) => c.method === 'POST' && c.path === '/recordings')).toBe(true)
  const body = uploaded ?? ''
  expect(body).toContain('name="file"; filename="recording-')
  expect(body).toContain('Content-Type: audio/webm')
  expect(body).toMatch(/name="title"\r\n\r\nLab meeting/)
  expect(body).toMatch(/name="participants"\r\n\r\n\["Dana Smith"\]/)
  expect(body).toMatch(/name="folder"\r\n\r\n3/)
  expect(body).toMatch(/name="tags"\r\n\r\n\["2"\]/)
  expect(body).toContain('- Decided to **order** the new scope')
  expect(body).toMatch(/name="meeting_date"\r\n\r\n\d{4}-\d{2}-\d{2}T/)
  // The kept copy is gone after the upload: nothing is offered next time.
  await page.goto('/recordings')
  await expect(page.getByTestId('recording-row').first()).toBeVisible()
  await expect(page.getByTestId('pending-recording')).toHaveCount(0)
})

test('recording.record: a tab closed in the middle keeps the audio, offered for upload next time', async ({ page }) => {
  await fakeMic(page)
  const calls = await mockSpeakr(page, {
    over: { 'POST /recordings': (route) => route.fulfill({ json: { connector_id: CID, recording_id: 98, title: 'Kept', status: 'pending', url: '' } }) },
  })
  await page.goto('/recordings')
  await page.getByRole('button', { name: 'New recording' }).first().click()
  const dlg = page.getByRole('dialog', { name: 'New recording' })
  await dlg.getByLabel('Title').fill('Hallway chat')
  await dlg.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(page.getByRole('dialog', { name: /Recording/ }).getByTestId('rx-clock')).toHaveText(/0:0[2-9]/, { timeout: 6000 })
  // The tab goes away mid-recording (no beforeunload prompt in a test: the page is closed).
  const context = page.context()
  await page.close({ runBeforeUnload: false })
  const again = await context.newPage()
  await mockSpeakr(again, { over: { 'POST /recordings': (route) => route.fulfill({ json: { connector_id: CID, recording_id: 98, title: 'Hallway chat', status: 'pending', url: '' } }) } })
  // The left session counts as abandoned once its heartbeat is old; the test ages it.
  await again.goto('/recordings')
  await again.evaluate(async () => {
    const db: IDBDatabase = await new Promise((res) => { const r = indexedDB.open('mailr-recorder', 1); r.onsuccess = () => res(r.result) })
    const tx = db.transaction('sessions', 'readwrite')
    const st = tx.objectStore('sessions')
    const all: { heartbeat: number }[] = await new Promise((res) => { const r = st.getAll(); r.onsuccess = () => res(r.result) })
    for (const s of all) st.put({ ...s, heartbeat: Date.now() - 60_000 })
    await new Promise((res) => (tx.oncomplete = res))
  })
  await again.reload()
  const banner = again.getByTestId('pending-recording')
  await expect(banner).toContainText('was not uploaded')
  await expect(banner).toContainText('Hallway chat')
  await shot(again, 'pending-light')
  await banner.getByRole('button', { name: 'Upload…' }).click()
  const dlg2 = again.getByRole('dialog', { name: 'Upload a kept recording' })
  await expect(dlg2.getByLabel('Title')).toHaveValue('Hallway chat')
  await dlg2.getByTestId('rx-upload').click()
  await expect(again).toHaveURL(new RegExp(`/recordings/${CID}/98$`))
  expect(calls.length).toBeGreaterThan(0)
})

test('Find recording… and Record this meeting in the event detail (recording.link, recording.record; web.calendar)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const start = new Date(Date.now() - 20 * 60_000)
  const end = new Date(Date.now() + 40 * 60_000)
  const ev = await createEvent(page.request, { summary: 'Weekly sync', start: start.toISOString(), end: end.toISOString(), attendees: [{ email: 'dana@guest.example', name: 'Dana Smith' }] })
  const calls = await mockSpeakr(page)
  await page.goto('/calendar/day')
  await page.getByTestId('cal-event').filter({ hasText: 'Weekly sync' }).first().click()
  const sec = page.getByTestId('cal-recording-find')
  await expect(sec.getByRole('button', { name: 'Record this meeting' })).toBeVisible()
  await sec.getByRole('button', { name: 'Find recording…' }).click()
  const finder = page.getByTestId('find-recording')
  await expect(finder.getByText('Weekly sync').first()).toBeVisible()
  // The search is by day around the meeting.
  expect(calls.find((c) => c.path === '/recordings' && c.query.get('from'))).toBeTruthy()
  await finder.locator('li').filter({ hasText: 'Weekly sync' }).getByRole('button', { name: 'Link' }).click()
  await expect.poll(() => JSON.parse(calls.find((c) => c.method === 'POST' && c.path === '/links')?.body ?? '{}')).toMatchObject({ kind: 'event', target: ev.uid, recording_id: 21 })
  await expect(page.getByText('Linked “Weekly sync” to this meeting.')).toBeVisible()

  // Record this meeting: the recorder opens with the event, title and guests.
  await page.getByTestId('cal-event').filter({ hasText: 'Weekly sync' }).first().click()
  await page.getByTestId('cal-recording-find').getByRole('button', { name: 'Record this meeting' }).click()
  const dlg = page.getByRole('dialog', { name: 'New recording' })
  await expect(dlg.getByLabel('Title')).toHaveValue('Weekly sync')
  await expect(dlg.getByRole('button', { name: 'Remove Dana Smith' })).toBeVisible()
  await expect(dlg.getByLabel('Link to event')).toHaveValue(new RegExp(`^${ev.uid.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\|`))
})

test('event.last_recording in the calendar panel: the next meeting today shows Last time (web.panel)', async ({ page, seeded }) => {
  // The meeting runs from 10 min ago to 50 min ahead; across midnight, either side, it is not "today's next".
  const minsToday = new Date().getHours() * 60 + new Date().getMinutes()
  test.skip(minsToday < 15 || (24 * 60) - minsToday <= 60, 'the test meeting would cross midnight')
  await connectCalendar(page.request, seeded)
  const start = new Date(Date.now() - 10 * 60_000)
  const end = new Date(Date.now() + 50 * 60_000)
  const ev = await createEvent(page.request, { summary: 'Weekly sync', start: start.toISOString(), end: end.toISOString() })
  const calls = await mockSpeakr(page, {
    over: { 'GET /events/last-time': (route) => route.fulfill({ json: { connected: true, upcoming: true, item: { ...RECS[0], recording_id: 20, title: 'Weekly sync', meeting_date: at(7, 10), why: 'the same series', summary_line: 'Ship the budget on Friday' } } }) },
  })
  await page.goto('/mail/inbox')
  await page.locator('.rail').getByRole('button', { name: 'Calendar beside' }).click()
  const next = page.getByTestId('calendar-panel').getByTestId('next-last-time')
  await expect(next).toContainText('Next:')
  await expect(next).toContainText('Weekly sync')
  await expect(next).toContainText('Ship the budget on Friday')
  expect(calls.find((c) => c.path === '/events/last-time')?.query.get('uid')).toBe(ev.uid)
  await shot(page, 'panel-last-time-light')
  await next.getByRole('button', { name: 'Weekly sync' }).click()
  await expect(page.getByTestId('recording-sheet')).toBeVisible()
})

test.describe('recordings screenshots', () => {
  test.skip(!SHOTS, 'set RECORDINGS_SHOTS to a directory to take screenshots')
  for (const theme of ['classic', 'office', 'focus', 'desk', 'cards'] as Theme[]) {
    for (const dark of [false, true]) {
      test(`${theme} ${dark ? 'dark' : 'light'}`, async ({ page }) => {
        const tag = `${theme}-${dark ? 'dark' : 'light'}`
        await fakeMic(page)
        await mockSpeakr(page)
        await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
        await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light', reducedMotion: 'reduce' })
        await page.goto('/recordings')
        await expect(page.getByTestId('recording-row').first()).toBeVisible()
        await shot(page, `app-${tag}`)
        await page.getByRole('searchbox', { name: 'Search recordings' }).fill('budget')
        await expect(page.getByTestId('summary-match').first()).toBeVisible()
        await shot(page, `search-${tag}`)
        await page.goto(`/recordings/${CID}/21`)
        await expect(page.getByTestId('recording-pane')).toBeVisible()
        await shot(page, `pane-${tag}`)
        await page.goto('/recordings')
        await page.getByRole('button', { name: 'New recording' }).first().click()
        await page.getByRole('dialog', { name: 'New recording' }).getByRole('button', { name: 'Record', exact: true }).click()
        await expect(page.getByRole('dialog', { name: /Recording/ }).getByTestId('rx-clock')).toHaveText(/0:0[1-9]/, { timeout: 5000 })
        await shot(page, `recorder-${tag}`)
        await page.setViewportSize({ width: 390, height: 844 })
        await page.getByRole('button', { name: 'Minimise' }).click()
        await shot(page, `phone-${tag}`)
      })
    }
  }
})

test('web.recordings at 600 px: the menu drawer is a modal dialog', async ({ page }) => {
  await mockSpeakr(page)
  await page.setViewportSize({ width: 600, height: 800 })
  await page.goto('/recordings')
  const menu = page.getByRole('button', { name: 'Recordings menu' })
  await menu.click()
  const drawer = page.getByRole('dialog', { name: 'Recordings menu' })
  await expect(drawer).toBeVisible()
  for (let i = 0; i < 15; i++) {
    await page.keyboard.press('Tab')
    expect(await drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(drawer).toHaveCount(0)
  await expect(menu).toBeFocused()
})
