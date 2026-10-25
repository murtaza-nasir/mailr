// The assistant's presentation layer on the web: a reply is an
// ordered timeline (text, steps, more text), an approval card changes in place when decided and the
// turn continues after it, and the log follows the stream until the reader scrolls up ("Jump to
// latest" brings it back). The chat stream is driven from the test through a fetch stand-in, so each
// event arrives when the test says. ACT approval.approve
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { expect, shotsDir, test } from './fixtures'

const SHOTS = shotsDir('present')
const here = path.dirname(fileURLToPath(import.meta.url))
const fixtures = JSON.parse(fs.readFileSync(path.join(here, '../../spec/fixtures/agent-blocks-fixtures.json'), 'utf8')) as {
  approvals: { name: string; action: string; display: Record<string, unknown>; outcomes: { state: string; text: string }[] }[]
}
const filter = fixtures.approvals.find((a) => a.name === 'filter-create')!

declare global {
  interface Window {
    __chat: { push: (event: string, data: unknown) => void; end: () => void; bodies: unknown[]; streams: number }
  }
}

/** POST /agent/chat answers with a stream the test feeds through window.__chat. */
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

async function openAssistant(page: Page) {
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await expect(page.locator('.chat')).toBeVisible()
}

async function ask(page: Page, text: string) {
  await page.getByLabel('Message Ambrose').fill(text)
  await page.getByLabel('Message Ambrose').press('Enter')
  await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBeGreaterThan(0)
}

test.beforeAll(() => fs.mkdirSync(SHOTS, { recursive: true }))

test('the reply is one timeline: text, steps, more text, in the order they happened', async ({ page }) => {
  await controlledChat(page)
  await openAssistant(page)
  await ask(page, 'Sort my receipts')
  await push(page, 'session', { session_id: 'e2e-present', user_message_id: 1 })
  await expect(page.locator('.chat-turn .thinking')).toBeVisible() // Thinking… where the turn is
  await push(page, 'token', { text: 'First I look for receipts.', segment: 0 })
  await push(page, 'segment', { segment: 0, content: 'First I look for receipts.', citations: [] })
  await push(page, 'tool_start', { id: 'a', name: 'search_conversations', label: 'Searching your mail for “receipt”', icon: 'search', segment: 0 })
  await push(page, 'tool', { id: 'a', name: 'search_conversations', label: 'Searching your mail for “receipt”', ok: true, summary: '12 conversations', count: 12, segment: 0 })
  await push(page, 'token', { text: 'Twelve found. Now the labels.', segment: 1 })
  await push(page, 'segment', { segment: 1, content: 'Twelve found. Now the labels.', citations: [] })
  await push(page, 'tool_start', { id: 'b', name: 'list_labels', label: 'Checking your labels', icon: 'label', segment: 1 })
  await push(page, 'tool', { id: 'b', name: 'list_labels', label: 'Checking your labels', ok: true, summary: '9 labels', count: 9, segment: 1 })
  await push(page, 'final', { segment: 2, content: '### Receipts\n\n| Store | Count |\n|---|---|\n| Levi’s | 4 |\n| Gap | 8 |', citations: [] })
  await push(page, 'done', { steps: 2, conversations: 0, seconds: 3.1 })
  await end(page)
  const turn = page.locator('.chat-turn').last()
  const kinds = await turn.locator(':scope > *').evaluateAll((els) => els.map((e) => (e.classList.contains('chat-steps-group') ? 'steps' : e.classList.contains('chat-msg') ? 'text' : '')).filter(Boolean))
  expect(kinds).toEqual(['text', 'steps', 'text', 'steps', 'text'])
  await expect(turn.locator('.chat-msg').first()).toHaveText('First I look for receipts.')
  await expect(turn.locator('h3')).toHaveText('Receipts')
  await expect(turn.locator('.md-table table')).toBeVisible()
  await expect(turn.locator('.thinking')).toHaveCount(0)
  await page.locator('.side-panel').screenshot({ path: `${SHOTS}/web-timeline.png` })
})

test('a reply that claims a change no step saved carries a note; the step line shows what was saved', async ({ page }) => {
  // The server's claim check (agent/claims.py): the step failed, the model insisted, `unconfirmed` is set.
  await controlledChat(page)
  await openAssistant(page)
  await ask(page, 'Remind me when the limit resets')
  await push(page, 'session', { session_id: 'e2e-claim', user_message_id: 1 })
  await push(page, 'tool_start', { id: 'a', name: 'create_task', label: 'Adding a task', icon: 'task', segment: 0 })
  await push(page, 'tool', { id: 'a', name: 'create_task', label: 'Adding a task', ok: true, summary: 'saved · reminds 2026-10-14 06:00',
    summary_msg: { key: 'approvals.result.saved_reminds', params: { when: '2026-10-14 06:00' } }, segment: 0 })
  await push(page, 'tool_start', { id: 'b', name: 'update_task', label: 'Changing a task', icon: 'task', segment: 0 })
  await push(page, 'tool', { id: 'b', name: 'update_task', label: 'Changing a task', ok: false, summary: '', error: "update_task has no field 'remind_at'", segment: 0 })
  await push(page, 'final', { segment: 1, content: 'Done, both are set.', citations: [], unconfirmed: true })
  await push(page, 'done', { steps: 2, conversations: 0, seconds: 2 })
  await end(page)
  const turn = page.locator('.chat-turn').last()
  await expect(turn.getByTestId('unconfirmed-claim')).toHaveText('This reply says something was changed, but no change was saved in this turn. Check the steps above.')
  await turn.getByRole('button', { name: '2 steps · 1 failed' }).click()
  await expect(turn.getByText('saved · reminds 2026-10-14 06:00')).toBeVisible()
})

test('an approval card changes in place and the turn continues after it', async ({ page }) => {
  await controlledChat(page)
  const card = { id: 41, action: 'rules.write', status: 'pending', target: 'Create filter', preview: { title: 'Create a filter', display: filter.display }, display: filter.display, outcome: { state: 'pending', text: 'Waiting for your approval' }, expires_at: null }
  const done = { ...card, status: 'executed', outcome: filter.outcomes.find((o) => o.state === 'executed') }
  await page.route('**/api/v1/approvals/41/decide', (route) => route.fulfill({ json: done }))
  await page.route('**/api/v1/approvals/41', (route) => route.fulfill({ json: done }))
  await openAssistant(page)
  await ask(page, 'File my finance mail')
  await push(page, 'session', { session_id: 'e2e-present-2', user_message_id: 1 })
  await push(page, 'segment', { segment: 0, content: 'I will set up a filter.', citations: [] })
  await push(page, 'tool_start', { id: 'a', name: 'manage_filter', label: 'Creating a filter “Finance”', icon: 'label', segment: 0 })
  await push(page, 'tool', { id: 'a', name: 'manage_filter', ok: true, summary: 'waiting for your approval', segment: 0 })
  await push(page, 'approval_required', { approval_id: 41, action: 'rules.write', approval: card, preview: card.preview, status: 'pending', segment: 0 })
  await push(page, 'final', { segment: 1, content: 'Approve the card above and the filter goes live.', citations: [] })
  await push(page, 'done', {})
  await end(page)

  const ap = page.locator('.approval')
  await expect(ap).toContainText('Create a filter')
  await expect(ap).toContainText('From any of: paypal.com, amex.com, chase.com, … (6)')
  await expect(ap.locator('.label-chip', { hasText: 'Finance' })).toBeVisible()
  await expect(ap).not.toContainText('add_labels')
  await page.locator('.side-panel').screenshot({ path: `${SHOTS}/web-approval-pending.png` })

  await ap.getByRole('button', { name: 'Allow once' }).click()
  await expect(ap.getByRole('status')).toHaveText('Approved · created the filter')
  // The chat continues in the same turn.
  await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBe(2)
  expect(await page.evaluate(() => window.__chat.bodies[1])).toMatchObject({ resume_approval_id: 41 })
  await push(page, 'session', { session_id: 'e2e-present-2', continuation: true, segment: 2 })
  await push(page, 'approval_update', { approval: done })
  await push(page, 'final', { segment: 2, content: 'Done. Mail from those six senders now gets the Finance label.', citations: [] })
  await push(page, 'done', {})
  await end(page)

  await expect(page.locator('.chat-turn')).toHaveCount(1)
  await expect(page.locator('.chat-user')).toHaveCount(1)
  const turn = page.locator('.chat-turn')
  const order = await turn.locator(':scope > *').evaluateAll((els) => els.map((e) =>
    e.classList.contains('approval') ? 'approval' : e.classList.contains('chat-resume') ? 'resume' : e.classList.contains('chat-steps-group') ? 'steps' : e.classList.contains('chat-msg') ? 'text' : '').filter(Boolean))
  expect(order).toEqual(['text', 'steps', 'approval', 'text', 'resume', 'text'])
  await expect(turn.locator('.chat-msg').last()).toContainText('Mail from those six senders')
  await page.locator('.side-panel').screenshot({ path: `${SHOTS}/web-approval-resolved.png` })
})

test('the log follows the stream, detaches when scrolled up, and Jump to latest returns', async ({ page }) => {
  await controlledChat(page)
  await page.setViewportSize({ width: 1280, height: 700 })
  await openAssistant(page)
  await ask(page, 'Write a long answer')
  await push(page, 'session', { session_id: 'e2e-present-3', user_message_id: 1 })
  const log = page.locator('.chat-log')
  const gap = () => log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)
  for (let i = 0; i < 40; i++) await push(page, 'token', { text: `Paragraph ${i} of a long streamed answer about receipts and filters.\n\n`, segment: 0 })
  await expect.poll(gap).toBeLessThan(48) // attached: at the bottom

  await log.hover()
  await page.mouse.wheel(0, -600)
  await expect(page.getByTestId('chat-jump')).toBeVisible()
  const top = await log.evaluate((el) => el.scrollTop)
  for (let i = 40; i < 60; i++) await push(page, 'token', { text: `Paragraph ${i} keeps arriving.\n\n`, segment: 0 })
  await page.waitForTimeout(200)
  expect(await log.evaluate((el) => el.scrollTop)).toBe(top) // no snapping back
  expect(await gap()).toBeGreaterThan(200)
  await page.locator('.side-panel').screenshot({ path: `${SHOTS}/web-jump-pill.png` })

  await page.getByTestId('chat-jump').click()
  await expect.poll(gap).toBeLessThan(48)
  await expect(page.getByTestId('chat-jump')).toHaveCount(0)
  for (let i = 60; i < 70; i++) await push(page, 'token', { text: `Paragraph ${i} after the jump.\n\n`, segment: 0 })
  await expect.poll(gap).toBeLessThan(48) // following again

  // Scrolling back down by hand attaches too.
  await page.mouse.wheel(0, -500)
  await expect(page.getByTestId('chat-jump')).toBeVisible()
  await log.evaluate((el) => { el.scrollTop = el.scrollHeight })
  await expect(page.getByTestId('chat-jump')).toHaveCount(0)

  // Scrolled up when the turn ends: the pill stays, so the reader can get back to the end.
  await page.mouse.wheel(0, -500)
  await expect(page.getByTestId('chat-jump')).toBeVisible()
  const whole = Array.from({ length: 70 }, (_, i) => `Paragraph ${i} of the answer.`).join('\n\n')
  await push(page, 'final', { segment: 0, content: `${whole}\n\nThe end.`, citations: [] })
  await push(page, 'done', {})
  await end(page)
  await expect(page.getByTestId('chat-jump')).toBeVisible()
  await page.getByTestId('chat-jump').click()
  await expect.poll(gap).toBeLessThan(48)
})

test('a wide table in a narrow panel becomes cards, one per row', async ({ page }) => {
  const md = JSON.parse(fs.readFileSync(path.join(here, '../../spec/fixtures/agent-markdown-fixtures.json'), 'utf8')) as {
    samples: { name: string; normalized: string }[]
  }
  const categories = md.samples.find((s) => s.name === 'categories-table')!
  await controlledChat(page)
  await openAssistant(page)
  await ask(page, 'Set up categories')
  await push(page, 'session', { session_id: 'e2e-present-4', user_message_id: 1 })
  await push(page, 'final', { segment: 0, content: categories.normalized, citations: [] })
  await push(page, 'done', {})
  await end(page)
  const table = page.locator('.chat-turn .md-table')
  await expect(table).toHaveAttribute('data-layout', 'cards')
  await expect(page.locator('.chat-turn h3')).toHaveText('Categories & matching rules')
  await expect(table.locator('tbody tr')).toHaveCount(3)
  await expect(table.locator('td[data-label="Match rule"]').first()).toContainText('from:levi.com OR from:gap.com OR from:nike.com')
  await page.locator('.side-panel').screenshot({ path: `${SHOTS}/web-table-cards.png` })
})

test('an empty or short chat never offers Jump to latest', async ({ page }) => {
  await controlledChat(page)
  await openAssistant(page)
  const log = page.locator('.chat-log').first()
  await log.hover()
  await page.mouse.wheel(0, -600) // nothing to scroll: stays attached
  await page.waitForTimeout(200)
  await expect(page.getByTestId('chat-jump')).toHaveCount(0)
  await ask(page, 'Short question')
  await push(page, 'session', { session_id: 'e2e-short', user_message_id: 1 })
  await push(page, 'final', { segment: 0, content: 'A short answer.', citations: [] })
  await push(page, 'done', {})
  await end(page)
  await log.hover()
  await page.mouse.wheel(0, -300)
  await page.waitForTimeout(200)
  await expect(page.getByTestId('chat-jump')).toHaveCount(0)
})

test('a citation card is drawn over the page and stays inside the window', async ({ page }) => {
  await controlledChat(page)
  await openAssistant(page)
  await ask(page, 'Find the invoice')
  await push(page, 'session', { session_id: 'e2e-cite', user_message_id: 1 })
  await push(page, 'final', {
    segment: 0,
    content: 'Your balance was charged to your card [1](mailr-cite:1).',
    citations: [{ n: 1, kind: 'mail', conversation_id: 1, message_id: 1, subject: '[DigitalOcean] Your 2026-07 invoice is available',
      from: 'DigitalOcean Support', date: '2026-08-01T10:00:00Z', quote: 'Your balance due was automatically charged to your Visa ending in 4960', verified: true }],
  })
  await push(page, 'done', {})
  await end(page)
  const chip = page.locator('.cite-chip').first()
  await chip.hover()
  const card = page.getByRole('tooltip').filter({ hasText: 'DigitalOcean Support' })
  await expect(card).toBeVisible()
  // Not inside the chat (which clips), but in the page's own layer.
  expect(await card.evaluate((el) => el.parentElement === document.body)).toBe(true)
  const box = (await card.boundingBox())!
  const vp = page.viewportSize()!
  expect(box.x).toBeGreaterThanOrEqual(8)
  expect(box.y).toBeGreaterThanOrEqual(8)
  expect(box.x + box.width).toBeLessThanOrEqual(vp.width - 8 + 0.5)
  expect(box.y + box.height).toBeLessThanOrEqual(vp.height - 8 + 0.5)
})
