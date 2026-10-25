// ACT-SURFACES: web.editor web.reader web.panel
// Live editing: an agent changes a draft that is open in a compose
// window, and the window follows in place: one window, the change shown with Undo, a merge with
// what the user is typing, the conflict bar when both changed the same sentence, "Open draft"
// bringing the open window forward, and two browser tabs on one draft. Classic and Cards, desktop
// and phone. The agent is an API token calling the update_draft tool (the same server path as the
// built-in assistant; backend tests cover the assistant's own turn).
// Covers: draft.live_update, draft.resolve_conflict, draft.open, agent.wait_for_typing (the panel toggle).
import { request as pwRequest, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { apiSettled, backend, expect, test } from './fixtures'
import { pick } from './matrix'

const P = (...xs: string[]) => xs.map((x) => `<p>${x}</p>`).join('')
const BASE = process.env.E2E_BASE ?? 'http://localhost:5175'

interface Made { id: number; version: number; subject: string }

async function makeDraft(page: Page, subject: string, html: string): Promise<Made> {
  const r = await page.request.post('/api/v1/drafts', { data: { payload: { to: [], cc: [], bcc: [], subject, html, attachments: [] } } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const j = await r.json()
  return { id: j.draft.id, version: j.version, subject }
}

async function stored(page: Page, id: number): Promise<{ html: string; version: number }> {
  const j = await (await page.request.get(`/api/v1/drafts/${id}`)).json()
  return { html: j.payload.html as string, version: j.version as number }
}

/** An agent with an API token (by "agent:E2E agent" in the live events). */
async function agent(page: Page): Promise<APIRequestContext> {
  const r = await page.request.post('/api/v1/tokens', { data: { name: 'E2E agent', scopes: ['read', 'draft'] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const tok = (await r.json()).token as string
  return pwRequest.newContext({ baseURL: BASE, extraHTTPHeaders: { Authorization: `Bearer ${tok}` } })
}

async function agentEdit(a: APIRequestContext, args: Record<string, unknown>) {
  const r = await a.post('/api/v1/agent/tools/update_draft', { data: args })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()).result as { version: number }
}

/** Open the draft from Drafts: the conversation shows it, and Edit draft opens its editor. */
async function openDraft(page: Page, subject: string): Promise<Locator> {
  await page.goto('/mail/drafts')
  await page.locator('[data-mail-list]').getByText(subject).first().click()
  const form = page.locator('.compose-form').filter({ has: page.locator('[contenteditable="true"]') })
  const edit = page.getByRole('button', { name: 'Edit draft' }).first()
  await expect(form.or(edit).first()).toBeVisible()
  if (!(await form.count())) await edit.click()
  await expect(form).toHaveCount(1)
  // The editor knows its version (GET /drafts/{id} carries it) before the test changes anything:
  // when that answer comes late (6 s under load) it replaces what was typed before it. Then a moment
  // more for the live stream to be connected.
  await apiSettled(page, /\/api\/v1\/(drafts|live\/draft)\//)
  await page.waitForTimeout(300)
  return form
}

/** Ambrose's panel: the top bar's button where it shows, else its shortcut (Ctrl+.), which every
 *  theme has (a full-screen compose window on a phone covers the tab bar). */
async function openPanel(page: Page) {
  const button = page.locator('[data-ambrose]:visible')
  if (await button.count()) {
    await button.click()
    return
  }
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('Control+.')
}

const editorOf = (form: Locator) => form.locator('[contenteditable="true"]').first()

/** Holds the page's draft saves until `release()`, so the agent's write lands first, as when the
 *  agent is quicker than the 1.5 s autosave pause (on a busy machine it is not). Release right after
 *  the agent's write: the page then saves over a newer version and gets the conflict. */
async function holdSaves(page: Page): Promise<() => Promise<void>> {
  let release!: () => void
  const held = new Promise<void>((r) => { release = r })
  const route = async (r: import('@playwright/test').Route) => {
    if (r.request().method() === 'PUT') await held
    await r.fallback()
  }
  await page.route('**/api/v1/drafts/*', route)
  return async () => {
    release()
    await page.unroute('**/api/v1/drafts/*', route)
  }
}

async function typeAtEndOf(page: Page, form: Locator, paragraph: string, text: string) {
  const p = editorOf(form).locator('p', { hasText: paragraph }).first()
  await p.click()
  await page.keyboard.press('End')
  await page.keyboard.type(text)
}

// The merge logic is the server's and the compose window's, the same in both themes: nightly every
// pair, otherwise Classic on the desktop and Cards on a phone (e2e/matrix.ts).
const PAIRS = [['classic', 'desktop'], ['classic', 'phone'], ['cards', 'desktop'], ['cards', 'phone']] as const
for (const [theme, device] of pick(PAIRS, [PAIRS[0], PAIRS[3]])) {
  test.describe(`${theme} · ${device}`, () => {
    if (device === 'phone') test.use({ viewport: { width: 393, height: 851 }, isMobile: true, hasTouch: true })
    test.beforeEach(async ({ page }) => {
      if (theme !== 'classic') await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    })

    test('draft.live_update: the agent revises the open draft in place, with Undo', async ({ page }) => {
      const d = await makeDraft(page, `Live ${theme} ${device}`, P('Hi Dana,', 'See you Thursday.', 'Best,'))
      const form = await openDraft(page, d.subject)
      const a = await agent(page)
      const res = await agentEdit(a, { draft_id: d.id, base_version: d.version, edits: [{ find: 'Thursday', replace: 'Friday' }] })
      expect(res.version).toBe(d.version + 1)
      // The same window shows the new text; no second window opened.
      await expect(editorOf(form)).toContainText('See you Friday.')
      await expect(page.locator('.compose-form')).toHaveCount(1)
      const toast = page.getByText('Updated by the agent E2E agent')
      await expect(toast).toBeVisible()
      await page.getByRole('button', { name: 'Undo' }).last().click()
      await expect(editorOf(form)).toContainText('See you Thursday.')
      // Undo is a new version, saved like any change.
      await expect.poll(async () => (await stored(page, d.id)).html).toContain('See you Thursday.')
      expect((await stored(page, d.id)).version).toBeGreaterThan(res.version)
      await a.dispose()
    })

    test('draft.live_update: typing while the agent edits another paragraph merges both', async ({ page }) => {
      const d = await makeDraft(page, `Merge ${theme} ${device}`, P('Hi Dana,', 'See you Thursday.', 'Best,'))
      const form = await openDraft(page, d.subject)
      const a = await agent(page)
      await typeAtEndOf(page, form, 'Best,', ' Alex')
      await agentEdit(a, { draft_id: d.id, base_version: d.version, edits: [{ find: 'Thursday', replace: 'Friday' }] })
      await expect(editorOf(form)).toContainText('See you Friday.')
      await expect(editorOf(form)).toContainText('Best, Alex')
      await expect(page.getByTestId('live-conflict')).toHaveCount(0)
      await expect.poll(async () => (await stored(page, d.id)).html).toMatch(/Friday[\s\S]*Best, Alex/)
      await a.dispose()
    })

    test('draft.resolve_conflict: the same sentence changed on both sides shows the bar; Compare, then Use mine', async ({ page }) => {
      const d = await makeDraft(page, 'Conflict E2E', P('Hi Dana,', 'See you Thursday.', 'Best,'))
      const form = await openDraft(page, d.subject)
      const a = await agent(page)
      const release = await holdSaves(page)
      await typeAtEndOf(page, form, 'See you Thursday.', ' Bring the deck.')
      await agentEdit(a, { draft_id: d.id, base_version: d.version, body: 'Hi Dana,\n\nSee you on Friday instead.\n\nBest,' })
      await release()
      const bar = page.getByTestId('live-conflict')
      await expect(bar).toBeVisible()
      await expect(bar).toContainText('The agent E2E agent changed this draft')
      await bar.getByRole('button', { name: 'Compare' }).click()
      await expect(page.getByTestId('live-compare')).toContainText('Bring the deck.')
      await expect(page.getByTestId('live-compare')).toContainText('See you on Friday instead.')
      await bar.getByRole('button', { name: 'Use mine' }).click()
      await expect(bar).toHaveCount(0)
      await expect.poll(async () => (await stored(page, d.id)).html).toContain('Bring the deck.')
      expect((await stored(page, d.id)).html).not.toContain('Friday')
      await a.dispose()
    })

    test('draft.resolve_conflict: "Use theirs" takes the agent\'s text', async ({ page }) => {
      const d = await makeDraft(page, 'Theirs E2E', P('Hi Dana,', 'See you Thursday.', 'Best,'))
      const form = await openDraft(page, d.subject)
      const a = await agent(page)
      const release = await holdSaves(page)
      await typeAtEndOf(page, form, 'See you Thursday.', ' Mine.')
      await agentEdit(a, { draft_id: d.id, base_version: d.version, body: 'Hi Dana,\n\nSee you on Friday.\n\nBest,' })
      await release()
      const bar = page.getByTestId('live-conflict')
      await expect(bar).toBeVisible()
      await bar.getByRole('button', { name: 'Use theirs' }).click()
      await expect(bar).toHaveCount(0)
      await expect(editorOf(form)).toContainText('See you on Friday.')
      await expect(editorOf(form)).not.toContainText('Mine.')
      await a.dispose()
    })

    test('an assistant write shows "Ambrose is editing this draft…", and the banner clears when it is done', async ({ page, seeded }) => {
      const d = await makeDraft(page, 'Banner E2E', P('Hello.'))
      const form = await openDraft(page, d.subject)
      serverEditing(seeded.email, d.id, 'start')
      await expect(page.getByTestId('live-editing')).toHaveText('Ambrose is editing this draft…')
      // The user can keep typing meanwhile.
      await typeAtEndOf(page, form, 'Hello.', ' Still typing.')
      const a = await agent(page)
      await agentEdit(a, { draft_id: d.id, base_version: d.version, edits: [{ find: 'Hello.', replace: 'Hello there.' }] })
      serverEditing(seeded.email, d.id, 'end')
      await expect(page.getByTestId('live-editing')).toHaveCount(0)
      await a.dispose()
    })

    test('flush-before-ask, and "Open draft" brings the open window forward instead of a second one', async ({ page }) => {
      await controlledChat(page)
      const d = await makeDraft(page, 'Panel E2E', P('Hi Dana,', 'See you Thursday.', 'Best,'))
      await openDraft(page, d.subject)
      // In a compose window this time: the inline editor pops out.
      await page.getByRole('button', { name: 'Pop out reply' }).click()
      await expect(page.locator('.compose-win')).toHaveCount(1)
      const form = page.locator('.compose-win .compose-form')
      await expect(page.locator('.compose-form')).toHaveCount(1)
      await page.waitForTimeout(300)
      // Popping out saves the draft. Typing while that save is still on its way leaves the window
      // marked unsaved after the flush below (its answer, for the older text, lands after the
      // typing): on a busy server that save takes seconds, so the test lets it land first.
      await apiSettled(page, /\/api\/v1\/drafts\//)
      await typeAtEndOf(page, form, 'Best,', ' Alex')
      // A phone's compose window fills the screen: minimise it to reach the assistant.
      if (device === 'phone') await page.locator('.compose-win').getByRole('button', { name: 'Minimise' }).click()
      await openPanel(page)
      const input = page.getByLabel('Message Ambrose')
      await expect(input).toBeVisible()
      // ACT agent.wait_for_typing @web.panel
      await page.getByTestId('wait-for-typing').click()
      await expect(page.getByTestId('wait-for-typing')).toHaveAttribute('aria-checked', 'true')
      await input.fill('make it shorter')
      await input.press('Enter')
      await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBe(1)
      // The window was saved before the question went out, and the question says what it shows.
      const saved = await stored(page, d.id)
      expect(saved.html).toContain('Best, Alex')
      const body = (await page.evaluate(() => window.__chat.bodies[0])) as Record<string, unknown>
      const ui = (body.context as { ui: { compose: Record<string, unknown>[]; wait_for_idle?: boolean } }).ui
      expect(ui.wait_for_idle).toBe(true)
      expect(ui.compose).toHaveLength(1)
      expect(ui.compose[0]).toMatchObject({ draft_id: d.id, base_version: saved.version, dirty: false })
      expect(String(ui.compose[0].body)).toContain('Best, Alex')
      // The turn changed that draft: its card opens the window that is open already.
      await page.evaluate(([id, v]) => {
        window.__chat.push('session', { session_id: 's-live', user_message_id: 1 })
        window.__chat.push('draft', { draft_id: id, payload: { subject: 'Panel E2E', to: [], cc: [], bcc: [], html: '<p>x</p>', attachments: [] }, ref: { kind: 'draft', id, version: v } })
        window.__chat.push('final', { content: 'Shortened.', citations: [], segment: 0 })
        window.__chat.push('done', {})
        window.__chat.end()
      }, [d.id, saved.version] as const)
      const open = page.getByTestId('open-draft')
      await expect(open).toBeVisible()
      // A newer version arrives (another change): the old card says so.
      const a = await agent(page)
      await agentEdit(a, { draft_id: d.id, base_version: saved.version, edits: [{ find: 'Thursday', replace: 'Friday' }] })
      await expect(open.getByTestId('updated-since')).toBeVisible()
      await open.click()
      await expect(page.locator('.compose-form')).toHaveCount(1)
      await expect(page.locator('.compose-win')).toHaveCount(1)
      await expect(page.locator('.compose-win')).not.toHaveClass(/minimized/)
      await expect(editorOf(form)).toBeFocused()
      await expect(editorOf(form)).toContainText('See you Friday.')
      await a.dispose()
    })

    test('two browser tabs on one draft: a save in one shows in the other', async ({ page, context }) => {
      const d = await makeDraft(page, 'Tabs E2E', P('Hi Dana,', 'See you Thursday.', 'Best,'))
      const formA = await openDraft(page, d.subject)
      const other = await context.newPage()
      const formB = await openDraft(other, d.subject)
      await typeAtEndOf(other, formB, 'See you Thursday.', ' At three.')
      await expect(formB.getByText('Draft saved')).toBeVisible()
      await expect(editorOf(formA)).toContainText('See you Thursday. At three.')
      await expect(page.getByText('Updated by another tab')).toBeVisible()
      await expect(page.locator('.compose-form')).toHaveCount(1)
      await other.close()
    })
  })
}

// Save sequencing: a save answered for older text never undoes what came after it, and "Draft saved"
// shows only when what is on screen is what the server holds.
test('a save answered after newer typing: the merged answer is kept by the save queued behind it, and "Saved" waits for the newest text', async ({ page }) => {
  const d = await makeDraft(page, 'Order E2E', P('Hi Dana,', 'See you Thursday.', 'Best,'))
  const form = await openDraft(page, d.subject)
  const status = form.locator('.status')
  // The first save reaches the server only after the agent's write, and its answer comes late.
  let release!: () => void
  const held = new Promise<void>((r) => { release = r })
  let puts = 0
  await page.route('**/api/v1/drafts/*', async (r) => {
    if (r.request().method() === 'PUT' && puts++ === 0) await held
    await r.fallback()
  })
  const bodies: string[] = []
  page.on('request', (r) => {
    if (r.method() === 'PUT' && /\/drafts\/\d+$/.test(r.url())) bodies.push(String(JSON.parse(r.postData() ?? '{}').payload?.html))
  })
  const sent = page.waitForRequest((r) => r.method() === 'PUT' && /\/drafts\/\d+$/.test(r.url()))
  await typeAtEndOf(page, form, 'Best,', ' Alex')
  await sent
  // Newer typing while the first save is on its way: the autosave behind it is queued.
  await page.keyboard.type(' and Sam')
  await page.waitForTimeout(1800)
  await expect(status).toHaveText('Saving…')
  const a = await agent(page)
  await agentEdit(a, { draft_id: d.id, base_version: d.version, edits: [{ find: 'Thursday', replace: 'Friday' }] })
  release()
  // The first answer is merged (Friday + Alex); the queued save carries the merge, not its older copy.
  await expect.poll(async () => (await stored(page, d.id)).html).toMatch(/Friday[\s\S]*Best, Alex and Sam/)
  await expect(status).toHaveText('Draft saved')
  await apiSettled(page, /\/api\/v1\/(drafts|live\/draft)\//)
  expect((await stored(page, d.id)).html).toMatch(/Friday[\s\S]*Best, Alex and Sam/)
  await expect(editorOf(form)).toContainText('See you Friday.')
  await expect(editorOf(form)).toContainText('Best, Alex and Sam')
  await expect(page.getByTestId('live-conflict')).toHaveCount(0)
  // No save after the merged answer sent the older copy back (before: the queued one did, and the
  // server held "Thursday" again until the next autosave).
  expect(bodies.slice(1).filter((h) => h.includes('Thursday'))).toEqual([])
  await a.dispose()
})

test('a save answered after newer typing does not say "Draft saved" while the newer text waits', async ({ page }) => {
  const d = await makeDraft(page, 'Honest E2E', P('Hi Dana,', 'Best,'))
  const form = await openDraft(page, d.subject)
  const status = form.locator('.status')
  let release!: () => void
  const held = new Promise<void>((r) => { release = r })
  let puts = 0
  await page.route('**/api/v1/drafts/*', async (r) => {
    if (r.request().method() === 'PUT' && puts++ === 0) await held
    await r.fallback()
  })
  const sent = page.waitForRequest((r) => r.method() === 'PUT' && /\/drafts\/\d+$/.test(r.url()))
  await typeAtEndOf(page, form, 'Best,', ' Alex')
  await sent
  await page.keyboard.type(' and Sam')
  // The older text's answer lands before the autosave pause ends: the newer text is not saved yet.
  release()
  await expect.poll(async () => (await stored(page, d.id)).html).toContain('Best, Alex')
  await expect(status).not.toHaveText('Draft saved')
  await expect.poll(async () => (await stored(page, d.id)).html).toContain('Best, Alex and Sam')
  await expect(status).toHaveText('Draft saved')
})

// A save the server answered "merged" (another writer saved meanwhile) leaves the form showing what
// the server holds: the status says "Draft saved". The live stream is held, so the merged answer is
// the only way the agent's write reaches the form (no race with the live event). Before the fix the
// form compared its own serialisation with the server's payload, whose keys come in another order,
// and the status stayed empty with nothing left to autosave.
for (const kind of ['another paragraph', 'a change and its undo'] as const) {
  test(`a merged save answer with nothing typed since says "Draft saved" (${kind})`, async ({ page }) => {
    await page.route('**/api/v1/events', () => { /* held: no live events */ })
    const d = await makeDraft(page, `Merged ${kind}`, P('Hi Dana,', 'See you Thursday.', 'Best,'))
    const form = await openDraft(page, d.subject)
    const status = form.locator('.status')
    let release!: () => void
    const held = new Promise<void>((r) => { release = r })
    let puts = 0
    await page.route('**/api/v1/drafts/*', async (r) => {
      if (r.request().method() === 'PUT' && puts++ === 0) await held
      await r.fallback()
    })
    const sent = page.waitForRequest((r) => r.method() === 'PUT' && /\/drafts\/\d+$/.test(r.url()))
    const answered = page.waitForResponse((r) => r.request().method() === 'PUT' && /\/drafts\/\d+$/.test(r.url()))
    await typeAtEndOf(page, form, 'Best,', ' Alex')
    await sent
    const a = await agent(page)
    const first = await agentEdit(a, { draft_id: d.id, base_version: d.version, edits: [{ find: 'Thursday', replace: 'Friday' }] })
    if (kind === 'a change and its undo') await agentEdit(a, { draft_id: d.id, base_version: first.version, edits: [{ find: 'Friday', replace: 'Thursday' }] })
    release()
    expect((await (await answered).json()).merged).toBe(true)
    const day = kind === 'another paragraph' ? 'Friday' : 'Thursday'
    await expect(editorOf(form)).toContainText(`See you ${day}.`)
    await expect(editorOf(form)).toContainText('Best, Alex')
    await expect(status).toHaveText('Draft saved')
    await apiSettled(page, /\/api\/v1\/drafts\//)
    expect((await stored(page, d.id)).html).toMatch(new RegExp(`${day}[\\s\\S]*Best, Alex`))
    await a.dispose()
  })
}



/** The server's "editing" notice (core/live.py editing()), sent the way an assistant write sends it. */
function serverEditing(email: string, draftId: number, state: 'start' | 'end') {
  const script = [
    'import asyncio, sys',
    'from sqlalchemy import select',
    'from mailr.core import live',
    'from mailr.db import sessionmaker',
    'from mailr.models import User',
    'async def main(email, did, state):',
    '    async with sessionmaker()() as db:',
    '        uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()',
    "    await live.editing(uid, 'draft', int(did), 'assistant', state)",
    'asyncio.run(main(*sys.argv[1:]))',
  ].join('\n')
  backend(['-c', script, email, String(draftId), state], { python: true })
}


// ---------------------------------------------------------------- the assistant panel

declare global {
  interface Window {
    __chat: { push: (event: string, data: unknown) => void; end: () => void; bodies: unknown[]; streams: number }
  }
}

/** POST /agent/chat answers with a stream the test feeds through window.__chat (as agent-present.spec.ts). */
async function controlledChat(page: Page) {
  await page.addInitScript(() => {
    const real = window.fetch.bind(window)
    let ctrl: ReadableStreamDefaultController<Uint8Array> | null = null
    let seq = 0
    const enc = new TextEncoder()
    window.__chat = {
      bodies: [],
      streams: 0,
      push(event, data) {
        seq += 1
        ctrl?.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify({ ...(data as object), seq })}\n\n`))
      },
      end() {
        ctrl?.close()
        ctrl = null
      },
    }
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.includes('/api/v1/agent/chat') && init?.method === 'POST' && !url.includes('/stop')) {
        window.__chat.bodies.push(JSON.parse(String(init.body ?? '{}')))
        window.__chat.streams += 1
        const body = new ReadableStream<Uint8Array>({ start: (c) => { ctrl = c } })
        return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
      }
      return real(input, init)
    }
  })
}


