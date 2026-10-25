// ACT-SURFACES: web.reader
// A bulk review in the chat (agent.review_mail; backend agent/review.py): the review_mail step leaves a
// progress card that follows the review (with Stop), then the review card appears below it, grouped by
// move and sender. The move chip of a sender, of one of its conversations or of a kept row changes what
// happens to it, and Apply sends the changes (agent.review_move); "Always …" on a sender makes a filter
// for their future mail (agent.review_filter). "This view" on an inbox tab is the whole tab, or the one
// section shown alone (the list query the chat sends). The chat stream and the review endpoints are
// stand-ins driven by the test.
import type { Page } from '@playwright/test'
import { expect, shotsDir, test } from './fixtures'

const SHOTS = shotsDir('review')

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

const push = (page: Page, event: string, data: unknown) => page.evaluate(([e, d]) => window.__chat.push(e as string, d), [event, data] as const)
const end = (page: Page) => page.evaluate(() => window.__chat.end())

const review = {
  review_id: 7, scope: 'Inbox: Promotions', total: 9,
  groups: [
    { move: 'trash', count: 5, senders: [
      { from: 'Stridewell', count: 3, email: 'news@stridewell.example', suggest_filter: true, conversation_ids: [101, 102, 103], subjects: ['Final hours: 30% off', 'New colours', 'Last chance'] },
      { from: 'Harbor Denim', count: 2, conversation_ids: [104, 105], subjects: ['Jeans week'] },
    ] },
    { move: 'archive', count: 1, senders: [{ from: 'Amazon', count: 1, conversation_ids: [106], subjects: ['Your order has shipped'] }] },
  ],
  set_aside: { starred: 1, known: 1 }, kept: [{ conversation_id: 107, from: 'REI', subject: 'Member dividend', reason: 'money to claim' }],
  kept_count: 1, unsure: [], unsure_count: 0,
}
const card = {
  id: 52, action: 'mail.trash', status: 'pending', target: '6 conversation(s)', expires_at: null,
  preview: { title: 'Make 6 changes from the review of Inbox: Promotions', kind: 'review', lines: ['Move to Trash: 5 (Stridewell 3, Harbor Denim 2)'], review },
  display: { icon: 'action', title: 'Make 6 changes from the review of Inbox: Promotions', fields: [] },
}

test('a review shows its scope and progress, then a card whose moves can be changed before Apply', async ({ page }) => {
  await controlledChat(page)
  let state = { review_id: 7, status: 'running', scope: 'Inbox: Promotions', total: 9, done: 4, set_aside: 2, decided: { trash: 2 }, approval_id: null as number | null, error: null }
  await page.route('**/api/v1/agent/reviews/7', (route) => route.fulfill({ json: state }))
  await page.route('**/api/v1/agent/reviews/7/stop', (route) => {
    state = { ...state, status: 'stopping' }
    return route.fulfill({ json: state })
  })
  await page.route('**/api/v1/approvals/52', (route) => route.fulfill({ json: card }))
  let decided: Record<string, unknown> | null = null
  await page.route('**/api/v1/approvals/52/decide', (route) => {
    decided = route.request().postDataJSON() as Record<string, unknown>
    return route.fulfill({ json: { ...card, status: 'executed' } })
  })

  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await page.getByLabel('Message Ambrose').fill('Clean up this view')
  await page.getByLabel('Message Ambrose').press('Enter')
  await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBeGreaterThan(0)
  await push(page, 'session', { session_id: 'e2e-review', user_message_id: 1 })
  await push(page, 'tool_start', { id: 'a', name: 'review_mail', label: 'Starting a review of the list', icon: 'label', segment: 0 })
  await push(page, 'tool', { id: 'a', name: 'review_mail', ok: true, summary: 'ok', segment: 0 })
  await push(page, 'review', { review_id: 7, segment: 0 })
  await push(page, 'final', { segment: 1, content: 'I am reviewing the 9 conversations; the card will appear here.', citations: [] })
  await push(page, 'done', {})
  await end(page)

  const progress = page.locator('.review-progress')
  await expect(progress).toContainText('Reviewing Inbox: Promotions: 9 conversations')
  await expect(progress).toContainText('4 of 9 checked')
  await expect(progress).toContainText('2 kept without asking')
  await progress.getByRole('button', { name: 'Stop' }).click()
  await expect(progress).toContainText('Stopping after this batch')

  // The job resumes and finishes: the card appears under the progress line.
  state = { ...state, status: 'done', done: 9, approval_id: 52 }
  await page.route('**/api/v1/agent/reviews/7', (route) => route.fulfill({ json: state }))
  await expect(progress).toContainText('Review of 9 conversations is ready', { timeout: 10_000 })
  const ap = page.locator('.approval')
  await expect(ap.locator('.rv-group.trash h4')).toHaveText('Move to Trash 5')
  // The summary names what a rule set aside; what the model kept is counted once, in its rows' heading.
  await expect(ap.locator('.rv-kept')).toHaveText('Kept: 1 starred, 1 from people you write to')
  await expect(ap.locator('.rv-rows').filter({ hasText: '1 kept: still useful' })).toHaveCount(1)
  await page.locator('.side-panel').screenshot({ path: `${SHOTS}/web-review-card.png` })

  // "Always move to Trash" on a sender with several: a filter for their future mail, with Undo.
  let rule: Record<string, unknown> | null = null
  await page.route('**/api/v1/rules', (route) => {
    rule = route.request().postDataJSON() as Record<string, unknown>
    return route.fulfill({ json: { id: 31, ...rule } })
  })
  await ap.getByRole('button', { name: 'Always move to Trash' }).click()
  await expect.poll(() => rule).not.toBeNull()
  expect(rule).toMatchObject({ query: 'from:news@stridewell.example', actions: { trash: true }, enabled: true })
  await expect(ap.getByText('Filter made')).toBeVisible()
  await expect(page.getByText('Filter made for future mail from Stridewell')).toBeVisible()

  // A sender from Archive to Trash: the row moves to the Trash group, the counts follow.
  await ap.getByRole('combobox', { name: 'What happens to Amazon' }).selectOption('trash')
  await expect(ap.locator('.rv-group.trash h4')).toHaveText('Move to Trash 6')
  await expect(ap.locator('.rv-group.archive')).toHaveCount(0)
  // A kept row to Archive.
  await ap.getByRole('button', { name: '1 kept: still useful' }).click()
  await ap.getByRole('combobox', { name: 'What happens to REI: Member dividend' }).selectOption('archive')
  await expect(ap.locator('.rv-group.archive h4')).toHaveText('Archive 1')
  // One conversation of a sender with several: Keep.
  await ap.getByRole('button', { name: 'Conversations from Stridewell' }).click()
  await ap.getByRole('combobox', { name: 'What happens to New colours' }).selectOption('keep')
  await expect(ap.locator('.rv-group.trash h4')).toHaveText('Move to Trash 5')
  await expect(ap.getByRole('button', { name: '1 kept: you chose Keep' })).toBeVisible()
  await expect(ap.locator('.rv-changed')).toHaveText('3 changed by you')
  await page.locator('.side-panel').screenshot({ path: `${SHOTS}/web-review-card-changed.png` })
  await ap.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect.poll(() => decided).not.toBeNull()
  expect(decided).toMatchObject({ decision: 'approve', scope: 'once' })
  expect(decided!.changes).toEqual({ 102: 'keep', 106: 'trash', 107: 'archive' })
  expect(decided!.except_ids).toBeUndefined()
  // The chat resumes with the card's outcome (the card waits in its chat, backend agent/review.py).
  await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBe(2)
  expect(await page.evaluate(() => window.__chat.bodies[1])).toMatchObject({ session_id: 'e2e-review', resume_approval_id: 52 })
  await push(page, 'session', { session_id: 'e2e-review', user_message_id: 2, continuation: true, segment: 2 })
  await push(page, 'final', { segment: 2, content: 'Applied: 4 conversations changed.', citations: [] })
  await push(page, 'done', {})
  await end(page)
  await expect(page.getByText('Applied: 4 conversations changed.')).toBeVisible()
  await expect(ap.locator('.ap-error')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)
})

test('Discard on a review card: a clean "Discarded" card, and the chat continues without an error', async ({ page }) => {
  await controlledChat(page)
  const state = { review_id: 7, status: 'done', scope: 'Inbox: Promotions', total: 9, done: 9, set_aside: 2, decided: { trash: 5, archive: 1 }, approval_id: 52, error: null }
  await page.route('**/api/v1/agent/reviews/7', (route) => route.fulfill({ json: state }))
  await page.route('**/api/v1/approvals/52', (route) => route.fulfill({ json: card }))
  const outcome = { state: 'denied', text: 'Discarded · nothing changed', msg: { key: 'approvals.outcome.discarded' } }
  const denied = { ...card, status: 'denied', outcome }
  let decided: Record<string, unknown> | null = null
  await page.route('**/api/v1/approvals/52/decide', (route) => {
    decided = route.request().postDataJSON() as Record<string, unknown>
    return route.fulfill({ json: denied })
  })

  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await page.getByLabel('Message Ambrose').fill('Clean up this view')
  await page.getByLabel('Message Ambrose').press('Enter')
  await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBe(1)
  await push(page, 'session', { session_id: 'e2e-review', user_message_id: 1 })
  await push(page, 'review', { review_id: 7, segment: 0 })
  await push(page, 'final', { segment: 0, content: 'The review card is below.', citations: [] })
  await push(page, 'done', {})
  await end(page)

  const ap = page.locator('.approval')
  await expect(ap.locator('.rv-group.trash h4')).toHaveText('Move to Trash 5')
  await ap.getByRole('button', { name: 'Discard' }).click()
  await expect.poll(() => decided).toMatchObject({ decision: 'deny' })
  await expect(ap).toHaveAttribute('data-status', 'denied')
  await expect(ap.getByRole('status')).toHaveText('Discarded · nothing changed')
  await expect(ap.locator('.ap-error')).toHaveCount(0)

  // The chat resumes with the card's outcome (backend: the card waits in its chat, agent/review.py).
  await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBe(2)
  expect(await page.evaluate(() => window.__chat.bodies[1])).toMatchObject({ session_id: 'e2e-review', resume_approval_id: 52 })
  await push(page, 'session', { session_id: 'e2e-review', user_message_id: 2, continuation: true, segment: 1 })
  await push(page, 'approval_update', { approval: denied })
  await push(page, 'final', { segment: 1, content: 'Discarded: nothing in Inbox: Promotions changed.', citations: [] })
  await push(page, 'done', {})
  await end(page)
  await expect(page.getByText('Discarded: nothing in Inbox: Promotions changed.')).toBeVisible()
  await expect(ap.getByRole('status')).toHaveText('Discarded · nothing changed')
  await expect(page.getByText('That approval is still waiting.')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)
})

test('"this view" on an inbox tab is the whole tab, or the section shown alone', async ({ page }) => {
  await controlledChat(page)
  const lay = { id: 'two', name: 'Two', tabs: [
    { id: 'main', name: 'Main', icon: 'inbox', sections: [
      { id: 'first', title: 'First few', base: 'inbox', filter: 'unread', limit: 3 },
      { id: 'rest', title: 'Everything else', base: 'inbox', exclude_above: true }] },
    { id: 'later', name: 'Later', icon: 'star', sections: [{ id: 'later', title: 'Later', base: 'starred' }] }] }
  const put = await page.request.put('/api/v1/layouts', { data: { layouts: [lay], active: 'two' } })
  expect(put.ok(), await put.text()).toBeTruthy()
  const total = (await (await page.request.get('/api/v1/conversations', { params: { layout: 'two', tab: 'main', limit: '1' } })).json()).total as number
  expect(total).toBeGreaterThan(0)
  const tabTotal = page.waitForResponse((r) => r.url().includes('/api/v1/conversations?') && r.url().includes('tab=main') && !r.url().includes('sec='))

  const ask = async (text: string) => {
    const before = await page.evaluate(() => window.__chat.streams)
    await page.getByLabel('Message Ambrose').fill(text)
    await page.getByLabel('Message Ambrose').press('Enter')
    await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBe(before + 1)
    await push(page, 'session', { session_id: 'e2e-scope', user_message_id: before + 1 })
    await push(page, 'final', { segment: 0, content: 'Noted.', citations: [] })
    await push(page, 'done', {})
    await end(page)
    return page.evaluate((i) => (window.__chat.bodies[i] as { context: { ui: { list?: Record<string, unknown> } } }).context.ui.list, before)
  }

  await page.goto('/mail/inbox')
  await tabTotal
  await page.locator('[data-ambrose]:visible').click()
  // The whole tab: layout and tab, no section, with the tab's total.
  expect(await ask('What is in this view?')).toEqual({ view: 'inbox', q: '', total, layout: 'two', tab: 'main', name: 'Main tab (all sections)' })

  // "Show only this section": that section alone.
  const rest = page.locator('[data-mail-list] .section-head').filter({ has: page.locator('.title', { hasText: 'Everything else' }) })
  await rest.getByTestId('section-focus').click()
  await expect(page.getByTestId('list-range')).toContainText(/Everything else/)
  const alone = await ask('And now?')
  expect(alone).toMatchObject({ view: 'inbox', layout: 'two', tab: 'main', sec: 'rest', name: 'Main: Everything else' })
  expect(typeof alone!.total).toBe('number')
})
