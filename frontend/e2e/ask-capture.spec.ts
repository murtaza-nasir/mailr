// ACT-SURFACES: web.panel web.sheet web.reader web.calendar web.pane web.notification web.triage web.palette web.ask
// The ask capture on the web (web.ask): agent.ask_about from the reader's toolbar, the
// bell tray's rows, the reminder sheet's header, the in-app reminder toast, the command palette and
// Shift+A in every theme; the item travels as structured context in the assistant's request and
// never handles the reminder; the words survive closing the capture. agent.dictate: the
// microphone records with MediaRecorder (Chrome's fake microphone plays a generated clip), the clip
// goes to /assistant/transcribe (answered by the test), the words come back editable, "Send when I
// stop talking" sends after the pause, a refused microphone says so, and the chat composer's
// microphone fills the message box. Screenshots of the capture in light and dark.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page, Request, Route } from '@playwright/test'
import { connectCalendar, conv, expect, test } from './fixtures'
import { dayOf, seedWeek } from './calendar-data'
import { looks, THEMES, themes, type Theme } from './matrix'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-ask-shots')

/** 1.2 s of a voiced, syllable-like signal, then 2.6 s of near silence (Chrome loops it). */
function speechWav(file: string) {
  const rate = 48_000
  const n = Math.round(rate * 3.8)
  const data = Buffer.alloc(n * 2)
  for (let i = 0; i < n; i++) {
    const t = i / rate
    const syllable = 0.55 + 0.45 * Math.sin(2 * Math.PI * 4 * t)
    const v = t < 1.2
      ? syllable * (0.35 * Math.sin(2 * Math.PI * 180 * t) + 0.2 * Math.sin(2 * Math.PI * 360 * t + 1) + 0.12 * Math.sin(2 * Math.PI * 720 * t + 2) + 0.05 * (Math.random() - 0.5))
      : 0.0005 * (Math.random() - 0.5)
    data.writeInt16LE(Math.max(-32767, Math.min(32767, Math.round(v * 32767))), i * 2)
  }
  const h = Buffer.alloc(44)
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8); h.write('fmt ', 12)
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24)
  h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write('data', 36); h.writeUInt32LE(data.length, 40)
  fs.writeFileSync(file, Buffer.concat([h, data]))
}
const WAV = path.join(os.tmpdir(), 'mailr-e2e-speech.wav')
speechWav(WAV)

test.use({
  launchOptions: {
    ...(process.env.E2E_CHROME ? { executablePath: process.env.E2E_CHROME } : {}),
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${WAV}`],
  },
})

interface Item {
  key: string; kind: 'event' | 'task'; uid: string; occurrence: string; minutes: number; title: string; detail: string
  start: string | null; fire_at: string; fired_at: string | null; missed: boolean; state: 'fired' | 'snoozed'; actions: string[]
}
const iso = (ms: number) => new Date(Date.now() + ms).toISOString()
const MIN = 60_000
function reminder(kind: 'event' | 'task', title: string, inMin: number): Item {
  const at = iso(inMin * MIN)
  const uid = `${kind === 'event' ? 'ev' : 'tk'}-${title.replace(/\W/g, '')}`
  return {
    key: `${kind === 'event' ? 'e' : 't'}|${uid}|${at}|10`, kind, uid, occurrence: at, minutes: 10, title, detail: '', start: at,
    fire_at: iso(-MIN), fired_at: iso(-MIN), missed: false, state: 'fired', actions: kind === 'task' ? ['done', 'snooze', 'dismiss'] : ['snooze', 'dismiss'],
  }
}

/** The tray's list, answered by the test; any reminder action is recorded (Ask must make none). */
async function serveReminders(page: Page, items: Item[]) {
  const acted: string[] = []
  await page.route('**/api/v1/reminders/active*', (r) => r.fulfill({ json: { items } }))
  for (const p of ['handle', 'snooze', 'add', 'act']) await page.route(`**/api/v1/reminders/${p}`, (r) => {
    acted.push(p)
    return r.fulfill({ json: { ok: true } })
  })
  return acted
}

/** Answers every chat turn with a short reply and keeps the request bodies. */
async function fakeChat(page: Page): Promise<Record<string, unknown>[]> {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    bodies.push((route.request() as Request).postDataJSON() as Record<string, unknown>)
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: 'event: session\ndata: {"session_id":"e2e-ask"}\n\nevent: final\ndata: {"content":"Noted."}\n\nevent: done\ndata: {}\n\n',
    })
  })
  await page.route('**/api/v1/agent/sessions/e2e-ask', (route) => route.fulfill({ json: { id: 'e2e-ask', turns: [] } }))
  return bodies
}

/** features.transcribe as the test wants it, whatever the dev server says. */
async function transcribeFeature(page: Page, on: boolean) {
  await page.route('**/api/v1/auth/session', async (route: Route) => {
    const r = await route.fetch()
    const j = (await r.json()) as Record<string, unknown>
    if (j.authenticated) j.features = { ...((j.features as object) ?? {}), transcribe: on }
    await route.fulfill({ response: r, json: j })
  })
}

/** The speech server, answered by the test: the clip's form fields and type are kept. */
async function fakeTranscribe(page: Page, text: string, delayMs = 400) {
  const clips: { type: string; name: string; size: number; duration: string }[] = []
  await page.route('**/api/v1/assistant/transcribe', async (route) => {
    const req = route.request()
    const body = req.postDataBuffer() ?? Buffer.alloc(0)
    const s = body.toString('latin1')
    clips.push({
      type: /Content-Type: (audio\/[^\r\n]+)/.exec(s)?.[1] ?? '',
      name: /filename="([^"]+)"/.exec(s)?.[1] ?? '',
      size: body.length,
      duration: /name="duration_ms"\r\n\r\n(\d+)/.exec(s)?.[1] ?? '',
    })
    await new Promise((r) => setTimeout(r, delayMs))
    await route.fulfill({ json: { text } })
  })
  return clips
}

async function openConversation(page: Page, theme: Theme, subject: string, dark = false) {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light', reducedMotion: 'reduce' })
  const c = await conv(page.request, subject)
  await page.goto(`/mail/inbox/${c.id}`)
  await expect(page.getByRole('heading', { name: subject }).first()).toBeVisible()
  return c
}

/** Keys go to the page, not to a field. */
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

const capture = (page: Page) => page.getByTestId('ask-capture')
const field = (page: Page) => capture(page).getByRole('textbox', { name: 'Your words for Ambrose' })

for (const theme of THEMES) {
  test(`agent.ask_about in ${theme}: reader toolbar, Shift+A, palette, tray row and sheet header`, async ({ page }) => {
    const bodies = await fakeChat(page)
    const acted = await serveReminders(page, [reminder('event', 'Budget review', 25), reminder('task', 'Pay the rent', -120)])
    const c = await openConversation(page, theme, 'E2E Unread me')

    // The reader's toolbar: the conversation as a chip; Escape closes and the words stay.
    await page.getByTestId('reader-ask').first().click()
    await expect(capture(page).getByTestId('ask-chip')).toContainText('E2E Unread me')
    await expect(field(page)).toBeFocused()
    await field(page).fill('Summarize this and list open questions')
    await page.keyboard.press('Escape')
    await expect(capture(page)).toHaveCount(0)

    // Shift+A opens it again about what is open, with the words still there.
    await blur(page)
    await page.keyboard.press('Shift+A')
    await expect(capture(page).getByTestId('ask-chip')).toContainText('E2E Unread me')
    await expect(field(page)).toHaveValue('Summarize this and list open questions')
    await capture(page).getByRole('button', { name: 'Send' }).click()
    await expect(capture(page)).toHaveCount(0)
    await expect(page.getByText('Noted.').first()).toBeVisible()
    expect(bodies[0].message).toBe('Summarize this and list open questions')
    expect((bodies[0].context as Record<string, unknown>).conversation_id).toBe(c.id)

    // The palette: "Ask about this…" names the open conversation.
    await blur(page)
    await page.keyboard.press('Control+k')
    await page.getByRole('combobox', { name: 'Command' }).fill('Ask about')
    await expect(page.locator('.palette-item, [role="option"]').filter({ hasText: 'Ask about this…' }).first()).toContainText('E2E Unread me')
    await page.keyboard.press('Enter')
    await expect(capture(page).getByTestId('ask-chip')).toContainText('E2E Unread me')
    await expect(field(page)).toHaveValue('')
    await page.keyboard.press('Escape')

    // The bell tray's row: Ask about a task reminder; the reminder is not handled.
    await page.getByRole('button', { name: 'Close panel' }).click()
    await page.getByTestId('waiting-bell').first().click()
    const tray = page.getByRole('dialog', { name: 'Waiting for you' })
    await tray.getByTestId('reminder-row-ask').first().focus()
    await tray.getByRole('button', { name: 'Ask Ambrose about Pay the rent' }).click()
    await expect(tray).toHaveCount(0)
    await expect(capture(page).getByTestId('ask-chip')).toContainText('Pay the rent')
    await field(page).fill('Move it to Friday')
    await page.keyboard.press('Enter')
    await expect.poll(() => bodies.length).toBe(2)
    expect(bodies[1].message).toBe('Move it to Friday')
    expect((bodies[1].context as Record<string, unknown>).focused_task).toBe('tk-Paytherent')
    expect(bodies[1].session_id ?? null).toBeNull() // a new chat

    // The sheet's header: Ask about an event; Open chat puts the words in the message box unsent.
    await page.getByRole('button', { name: 'Close panel' }).click()
    await page.getByTestId('waiting-bell').first().click()
    await page.getByRole('dialog', { name: 'Waiting for you' }).getByTestId('reminder-row').filter({ hasText: 'Budget review' }).click()
    await page.getByTestId('reminder-ask').click()
    await expect(capture(page).getByTestId('ask-chip')).toContainText('Budget review')
    await field(page).fill('Prepare an agenda')
    await capture(page).getByRole('button', { name: 'Open chat' }).click()
    await expect(page.getByTestId('chat-about')).toContainText('Budget review')
    await expect(page.getByRole('textbox', { name: 'Message Ambrose' })).toHaveValue('Prepare an agenda')
    await page.waitForTimeout(300)
    expect(bodies.length).toBe(2)
    expect(acted).toEqual([])
  })
}

// Ask is the same button in every theme's event detail and task pane (where the theme puts them is
// calendar-views' and tasks-app-themes' subject): nightly every theme, otherwise Classic and Cards.
for (const theme of themes({ release: ['classic', 'cards'] })) {
  test(`agent.ask_about in ${theme}: the event detail and the task pane`, async ({ page, seeded }) => {
    const ev = await seedWeek(page.request, seeded)
    await connectCalendar(page.request, seeded)
    const task = (await (await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Ask pane', due: dayOf(0) } })).json()) as { uid: string }
    const bodies = await fakeChat(page)
    await serveReminders(page, [])
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.emulateMedia({ reducedMotion: 'reduce' })

    // The event detail (a popover, pane, card or sheet, as the theme shows it).
    await page.goto(`/calendar/week/${dayOf(3)}`)
    await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).first().click()
    await page.getByTestId('cal-detail').getByRole('button', { name: 'Ask Ambrose' }).click()
    await expect(capture(page).getByTestId('ask-chip')).toContainText('Budget review')
    await field(page).fill('What should I read before this?')
    await capture(page).getByRole('button', { name: 'Send' }).click()
    await expect.poll(() => bodies.length).toBe(1)
    expect((bodies[0].context as Record<string, unknown>).focused_event).toEqual({ uid: ev.budget.uid, occurrence: expect.any(String) })

    // Shift+A with the detail open attaches the same occurrence.
    await page.getByRole('button', { name: 'Close panel' }).click()
    await page.getByTestId('cal-event').filter({ hasText: 'Budget review' }).first().click()
    await expect(page.getByTestId('cal-detail')).toBeVisible()
    await page.keyboard.press('Shift+A')
    await expect(capture(page).getByTestId('ask-chip')).toContainText('Budget review')
    await page.keyboard.press('Escape')

    // The task pane.
    await page.goto(`/tasks/all/task/${encodeURIComponent(task.uid)}`)
    await page.getByTestId('task-pane').getByRole('button', { name: 'Ask Ambrose' }).click()
    await expect(capture(page).getByTestId('ask-chip')).toContainText('E2E Ask pane')
    await field(page).fill('Split this into steps')
    await page.keyboard.press('Enter')
    await expect.poll(() => bodies.length).toBe(2)
    expect((bodies[1].context as Record<string, unknown>).focused_task).toBe(task.uid)
  })
}

test('agent.ask_about: the chip can be removed, then nothing is attached', async ({ page }) => {
  const bodies = await fakeChat(page)
  await serveReminders(page, [])
  const c = await openConversation(page, 'classic', 'E2E Unread me')
  await page.getByTestId('reader-ask').first().click()
  await capture(page).getByRole('button', { name: 'Remove E2E Unread me' }).click()
  await expect(capture(page)).toContainText('No item attached')
  await field(page).fill('What needs a reply this week?')
  await capture(page).getByRole('button', { name: 'Send' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  const ctx = bodies[0].context as Record<string, unknown>
  // The open conversation is still on screen (ui), but no item is attached.
  expect(ctx.focused_task).toBeUndefined()
  expect(ctx.focused_event).toBeUndefined()
  expect(ctx.conversation_id).toBe(c.id)
  await expect(page.getByTestId('chat-about')).toHaveCount(0)
})

test('agent.ask_about: the in-app reminder toast offers Ask when desktop notifications are off', async ({ page }) => {
  const bodies = await fakeChat(page)
  const r = reminder('event', 'Design review', 10)
  const acted = await serveReminders(page, [r])
  await page.addInitScript(() => {
    class FakeNotification {
      static permission: NotificationPermission = 'default'
      static async requestPermission() { return 'default' as NotificationPermission }
    }
    ;(window as unknown as { Notification: unknown }).Notification = FakeNotification
    const w = window as unknown as { __es: unknown }
    class FakeES {
      static OPEN = 1
      readyState = 1
      onmessage: ((m: { data: string }) => void) | null = null
      onopen: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor() {
        w.__es = this
        setTimeout(() => this.onopen?.(), 0)
      }
      close() {}
    }
    ;(window as unknown as { EventSource: unknown }).EventSource = FakeES
  })
  await page.goto('/mail/inbox')
  await page.waitForFunction(() => !!(window as unknown as { __es: unknown }).__es)
  await page.evaluate((d) => (window as unknown as { __es: { onmessage: (m: { data: string }) => void } }).__es.onmessage({ data: JSON.stringify(d) }), {
    type: 'notification', kind: 'reminder', key: r.key, title: r.title, body: '', start: r.start, item: { kind: r.kind, uid: r.uid, occurrence: r.occurrence }, missed: false, actions: r.actions,
  })
  const t = page.locator('.toast').filter({ hasText: 'Design review' })
  await expect(t).toBeVisible()
  await t.getByRole('button', { name: 'Ask' }).click()
  await expect(capture(page).getByTestId('ask-chip')).toContainText('Design review')
  await field(page).fill('Who is coming?')
  await capture(page).getByRole('button', { name: 'Send' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect((bodies[0].context as Record<string, unknown>).focused_event).toEqual({ uid: r.uid, occurrence: r.occurrence })
  expect(acted).toEqual([])
})

test('agent.dictate: tap to speak, tap to stop; the words come back to edit before Send', async ({ page }) => {
  const bodies = await fakeChat(page)
  await serveReminders(page, [])
  await transcribeFeature(page, true)
  const clips = await fakeTranscribe(page, 'move this to Friday and email Dana')
  await openConversation(page, 'classic', 'E2E Unread me')
  await page.getByTestId('reader-ask').first().click()
  await field(page).fill('Please')
  const mic = capture(page).getByTestId('ask-mic')
  await mic.click()
  await expect(capture(page).getByRole('status')).toContainText('Listening')
  await page.waitForTimeout(1500)
  await capture(page).getByRole('button', { name: 'Stop dictation' }).click()
  await expect(capture(page).getByRole('status')).toContainText('Transcribing')
  await expect(field(page)).toHaveValue('Please move this to Friday and email Dana')
  // Nothing was sent: the words wait to be read.
  expect(bodies.length).toBe(0)
  expect(clips).toHaveLength(1)
  expect(clips[0].type).toMatch(/^audio\/webm/)
  expect(clips[0].name).toBe('dictation.webm')
  expect(Number(clips[0].duration)).toBeGreaterThan(1000)
  await field(page).fill('Please move this to Thursday and email Dana')
  await capture(page).getByRole('button', { name: 'Send' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].message).toBe('Please move this to Thursday and email Dana')
})

test('agent.dictate: "Send when I stop talking" sends after the pause', async ({ page }) => {
  const bodies = await fakeChat(page)
  await serveReminders(page, [reminder('task', 'Pay the rent', -30)])
  await transcribeFeature(page, true)
  const clips = await fakeTranscribe(page, 'pay it tomorrow morning', 200)
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').first().click()
  await page.getByRole('button', { name: 'Ask Ambrose about Pay the rent' }).click()
  await capture(page).getByLabel('Send when I stop talking').check()
  await capture(page).getByTestId('ask-mic').click()
  await expect(capture(page).getByRole('status')).toContainText('sent when you stop talking')
  // The clip has 1.2 s of sound, then quiet: the pause ends it and the words go at once.
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].message).toBe('pay it tomorrow morning')
  expect((bodies[0].context as Record<string, unknown>).focused_task).toBe('tk-Paytherent')
  expect(clips).toHaveLength(1)
  await expect(capture(page)).toHaveCount(0)
  // The choice is remembered on this browser.
  await blur(page)
  await page.keyboard.press('Shift+A')
  await expect(capture(page).getByLabel('Send when I stop talking')).toBeChecked()
})

test('agent.dictate: a refused microphone says so; no mic without the server feature', async ({ page }) => {
  await serveReminders(page, [])
  await transcribeFeature(page, true)
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('denied'), { name: 'NotAllowedError' }))
  })
  await openConversation(page, 'classic', 'E2E Unread me')
  await page.getByTestId('reader-ask').first().click()
  await capture(page).getByTestId('ask-mic').click()
  await expect(capture(page).getByRole('alert')).toContainText('Microphone access was refused')
  await expect(capture(page).getByRole('button', { name: 'Send' })).toBeDisabled()
})

test('agent.dictate: a clip with no speech (only a made-up phrase, dropped by the server) says so', async ({ page }) => {
  const bodies = await fakeChat(page)
  await serveReminders(page, [])
  await transcribeFeature(page, true)
  await page.route('**/api/v1/assistant/transcribe', (route) => route.fulfill({ json: { text: '', dropped: 'no speech' } }))
  await openConversation(page, 'classic', 'E2E Unread me')
  await page.getByTestId('reader-ask').first().click()
  await field(page).fill('Please')
  await capture(page).getByTestId('ask-mic').click()
  await expect(capture(page).getByRole('status')).toContainText('Listening')
  await page.waitForTimeout(1200)
  await capture(page).getByRole('button', { name: 'Stop dictation' }).click()
  await expect(capture(page).getByRole('alert')).toContainText('No speech heard')
  await expect(field(page)).toHaveValue('Please')
  expect(bodies.length).toBe(0)
})

test('agent.dictate: no microphone when the server has no speech server', async ({ page }) => {
  await serveReminders(page, [])
  await transcribeFeature(page, false)
  await openConversation(page, 'classic', 'E2E Unread me')
  await page.getByTestId('reader-ask').first().click()
  await expect(capture(page)).toBeVisible()
  await expect(capture(page).getByTestId('ask-mic')).toHaveCount(0)
  await expect(capture(page).getByLabel('Send when I stop talking')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+.')
  await expect(page.getByRole('textbox', { name: 'Message Ambrose' })).toBeVisible()
  await expect(page.getByTestId('chat-mic')).toHaveCount(0)
})

test('agent.dictate: the chat composer microphone fills the message box', async ({ page }) => {
  const bodies = await fakeChat(page)
  await serveReminders(page, [])
  await transcribeFeature(page, true)
  await fakeTranscribe(page, 'what did Dana ask for')
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list]').first()).toBeVisible()
  await blur(page)
  await page.keyboard.press('Control+.')
  const box = page.getByRole('textbox', { name: 'Message Ambrose' })
  await expect(box).toBeVisible()
  await page.getByTestId('chat-mic').click()
  await expect(page.getByRole('button', { name: 'Stop dictation' })).toBeVisible()
  await page.waitForTimeout(1200)
  await page.getByRole('button', { name: 'Stop dictation' }).click()
  await expect(box).toHaveValue('what did Dana ask for')
  expect(bodies.length).toBe(0)
  await page.keyboard.press('Enter')
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].message).toBe('what did Dana ask for')
})

test('web.ask screenshots: the capture in light and dark in Classic, Office, Desk, Focus and Cards', async ({ page }) => {
  fs.mkdirSync(SHOTS, { recursive: true })
  await serveReminders(page, [])
  await transcribeFeature(page, true)
  await fakeTranscribe(page, 'x')
  for (const { theme, dark } of looks({ dark: true })) {
    await openConversation(page, theme, 'E2E Unread me', dark)
    await page.getByTestId('reader-ask').first().click()
    await field(page).fill('Draft a reply that accepts and proposes two times next week')
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/ask-${theme}-${dark ? 'dark' : 'light'}.png` })
    await page.keyboard.press('Escape')
  }
  // The bell tray's rows and the sheet's header (Classic light, Desk dark).
  await serveReminders(page, [reminder('event', 'Budget review', 25), reminder('task', 'Pay the rent', -120)])
  for (const [theme, dark] of [['classic', false], ['desk', true]] as const) {
    await openConversation(page, theme, 'E2E Unread me', dark)
    await page.getByTestId('waiting-bell').first().click()
    const tray = page.getByRole('dialog', { name: 'Waiting for you' })
    await tray.getByTestId('reminder-row').first().hover()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/ask-tray-${theme}-${dark ? 'dark' : 'light'}.png` })
    await tray.getByTestId('reminder-row').filter({ hasText: 'Budget review' }).click()
    await page.waitForTimeout(300)
    await tray.screenshot({ path: `${SHOTS}/ask-sheet-${theme}-${dark ? 'dark' : 'light'}.png` })
    await page.getByTestId('reminder-ask').click()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/ask-from-sheet-${theme}-${dark ? 'dark' : 'light'}.png` })
    await page.keyboard.press('Escape')
  }
  // A phone: the capture is a bottom sheet.
  await page.setViewportSize({ width: 390, height: 844 })
  await openConversation(page, 'classic', 'E2E Unread me')
  // At phone width Ask assistant is in the reader's ⋮ (the toolbar keeps room for ⋮).
  await page.locator('.conv-view-head').getByRole('button', { name: 'More' }).click()
  await page.getByRole('menuitem', { name: 'Ask Ambrose' }).click()
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/ask-phone-light.png` })
  await page.keyboard.press('Escape')
  await page.setViewportSize({ width: 1440, height: 900 })

  // While listening, in Office (light).
  await openConversation(page, 'office', 'E2E Unread me')
  await page.getByTestId('reader-ask').first().click()
  await capture(page).getByTestId('ask-mic').click()
  await expect(capture(page).getByRole('status')).toContainText('Listening')
  await page.waitForTimeout(700)
  await capture(page).screenshot({ path: `${SHOTS}/ask-office-listening.png` })
  await capture(page).getByRole('button', { name: 'Stop dictation' }).click()
})
