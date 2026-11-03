// ACT-SURFACES: web.reader web.editor
// A saved draft shown in its thread follows every change to it. The draft keeps its message id
// through each save, and the reader used to keep the body it had loaded first: after an agent's
// edit (applied directly, or after the user approved it) the thread's draft card still showed the
// old text, and Edit draft then showed the new text in the editor under the stale card, which
// looked like two drafts. Now the card shows the new text without a reload and says who changed
// it; a draft open in the inline editor has no card above it; the card comes back with the current
// text when the editor leaves; an editor with unsaved typing gets the conflict bar, never a silent
// overwrite. The agent is an API token calling update_draft, the same server path as Ambrose's.
// Covers: draft.live_update, draft.resume, draft.resolve_conflict.
import { request as pwRequest, type APIRequestContext, type Page } from '@playwright/test'
import { apiSettled, backend, conv, expect, test } from './fixtures'

const P = (...xs: string[]) => xs.map((x) => `<p>${x}</p>`).join('')
const BASE = process.env.E2E_BASE ?? 'http://localhost:5175'
const SUBJECT = 'E2E Reply to me'

interface Draft { id: number; version: number }

/** A reply-all draft in the seeded "E2E Reply to me" conversation, made through the API. */
async function replyDraft(page: Page, ...paragraphs: string[]): Promise<Draft> {
  const c = await conv(page.request, SUBJECT)
  const det = await (await page.request.get(`/api/v1/conversations/${c.id}`)).json()
  const m = det.messages.filter((x: { draft: boolean }) => !x.draft).pop()
  const got = await (await page.request.get('/api/v1/compose/reply', { params: { message_id: String(m.id), mode: 'reply_all' } })).json()
  const payload = 'payload' in got ? got.payload : got
  const r = await page.request.post('/api/v1/drafts', { data: { payload: { ...payload, html: P(...paragraphs) } } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const j = await r.json()
  return { id: j.draft.id, version: j.version }
}

async function agent(page: Page): Promise<APIRequestContext> {
  const r = await page.request.post('/api/v1/tokens', { data: { name: 'E2E agent', scopes: ['read', 'draft'] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const tok = (await r.json()).token as string
  return pwRequest.newContext({ baseURL: BASE, extraHTTPHeaders: { Authorization: `Bearer ${tok}` } })
}

async function updateDraft(a: APIRequestContext, args: Record<string, unknown>) {
  const r = await a.post('/api/v1/agent/tools/update_draft', { data: args })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()).result as { version?: number; status?: string; approval_id?: number | string }
}

/** The worker's approval.execute job for this user, run here (the e2e servers run no worker). */
function runApprovals(email: string) {
  const script = [
    'import asyncio, sys',
    'from sqlalchemy import select',
    'from mailr.core.jobs import _HANDLERS',
    'import mailr.main  # noqa: F401  (the executors need the app\'s import order)',
    'import mailr.core.permissions',
    'from mailr.db import sessionmaker',
    'from mailr.models import Job, User',
    'async def main(email):',
    '    async with sessionmaker()() as db:',
    '        uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()',
    "        jobs = list((await db.execute(select(Job).where(Job.user_id == uid, Job.kind == 'approval.execute',",
    "                                                        Job.status == 'queued'))).scalars())",
    '        payloads = [j.payload for j in jobs]',
    '        for j in jobs:',
    "            j.status = 'done'",
    '        await db.commit()',
    '    for pl in payloads:',
    "        await _HANDLERS['approval.execute'](pl, None)",
    '    print(len(payloads))',
    'asyncio.run(main(*sys.argv[1:]))',
  ].join('\n')
  return backend(['-c', script, email], { python: true }).trim()
}

const card = (page: Page, id: number) => page.locator(`.msg[data-message-id="${id}"]`)
const cardBody = (page: Page, id: number) => card(page, id).locator('.msg-body iframe').first().contentFrame().locator('body')
const editor = (page: Page) => page.locator('[data-inline-reply] [contenteditable="true"]').first()

/** The thread, with the draft card opened (a draft shows folded until clicked). */
async function openThread(page: Page, d: Draft, text: string) {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: SUBJECT }).first().click()
  const folded = page.locator(`.msg.collapsed[data-message-id="${d.id}"]`)
  await expect(card(page, d.id)).toBeVisible()
  if (await folded.count()) await folded.click()
  await expect(cardBody(page, d.id)).toContainText(text)
  // The live stream is connected before the test changes anything.
  await apiSettled(page, /\/api\/v1\//)
  await page.waitForTimeout(300)
}

test('an agent edit of a draft updates its card in the open thread, without a reload', async ({ page }) => {
  const d = await replyDraft(page, 'Thanks for the list.', 'See you Thursday.')
  await openThread(page, d, 'See you Thursday.')
  const a = await agent(page)
  await updateDraft(a, { draft_id: d.id, base_version: d.version, edits: [{ find: 'Thursday', replace: 'Friday' }] })
  await expect(cardBody(page, d.id)).toContainText('See you Friday.')
  await expect(cardBody(page, d.id)).not.toContainText('Thursday')
  await expect(card(page, d.id).getByTestId('draft-updated')).toHaveText('Updated by the agent E2E agent · just now')
  await a.dispose()
})

test('an agent edit applied after the user approves it updates the card too', async ({ page, seeded }) => {
  const d = await replyDraft(page, 'Thanks for the list.', 'See you Thursday.')
  await openThread(page, d, 'See you Thursday.')
  const a = await agent(page)
  // A change of recipients on a saved draft always waits for approval (agent/draft_tools.py _draft_gate).
  const res = await updateDraft(a, { draft_id: d.id, base_version: d.version, cc: ['ops@e2e.example.org'],
    body: 'Thanks for the list.\n\nSee you on Friday, with the warmer wording.' })
  expect(res.status).toBe('pending_approval')
  // Nothing changed yet.
  await expect(cardBody(page, d.id)).toContainText('See you Thursday.')
  const decided = await page.request.post(`/api/v1/approvals/${res.approval_id}/decide`, { data: { decision: 'approve' } })
  expect(decided.ok(), await decided.text()).toBeTruthy()
  expect(runApprovals(seeded.email)).toBe('1')
  await expect(cardBody(page, d.id)).toContainText('See you on Friday, with the warmer wording.')
  await expect(cardBody(page, d.id)).not.toContainText('Thursday')
  await expect(card(page, d.id).getByTestId('draft-updated')).toBeVisible()
  // Edit draft: the editor shows the new text, and no stale card stands above it.
  await card(page, d.id).getByRole('button', { name: 'Edit draft' }).click()
  await expect(editor(page)).toContainText('See you on Friday, with the warmer wording.')
  await expect(card(page, d.id)).toHaveCount(0)
  await expect(page.locator('.msg-draft-word')).toHaveCount(0)
  await a.dispose()
})

test('a draft open in the inline editor has no card; the card comes back with the current text', async ({ page }) => {
  const d = await replyDraft(page, 'Thanks for the list.', 'See you Thursday.')
  await openThread(page, d, 'See you Thursday.')
  await card(page, d.id).getByRole('button', { name: 'Edit draft' }).click()
  await expect(editor(page)).toContainText('See you Thursday.')
  await expect(card(page, d.id)).toHaveCount(0)
  // A change made while the editor is open shows in the editor (it was clean), still with no card.
  const a = await agent(page)
  await updateDraft(a, { draft_id: d.id, base_version: d.version, edits: [{ find: 'Thursday', replace: 'Friday' }] })
  await expect(editor(page)).toContainText('See you Friday.')
  await expect(card(page, d.id)).toHaveCount(0)
  // The user's own typing, saved; then the reply leaves the thread for a compose window.
  await editor(page).locator('p', { hasText: 'See you Friday.' }).click()
  await page.keyboard.press('End')
  await page.keyboard.type(' Bring the agenda.')
  await expect(page.locator('[data-inline-reply] .status')).toHaveText('Draft saved')
  await page.getByRole('button', { name: 'Pop out reply' }).click()
  await expect(page.locator('[data-inline-reply]')).toHaveCount(0)
  await expect(cardBody(page, d.id)).toContainText('See you Friday. Bring the agenda.')
  // Its own saves are not "updated by" anyone.
  await expect(card(page, d.id).getByTestId('draft-updated')).toHaveCount(0)
  await a.dispose()
})

test('an editor with unsaved typing gets the choice, not a silent overwrite', async ({ page }) => {
  const d = await replyDraft(page, 'Thanks for the list.', 'See you Thursday.')
  await openThread(page, d, 'See you Thursday.')
  await card(page, d.id).getByRole('button', { name: 'Edit draft' }).click()
  await expect(editor(page)).toContainText('See you Thursday.')
  await apiSettled(page, /\/api\/v1\/(drafts|live\/draft)\//)
  // The page's saves wait until the agent's write has landed, as when the agent is quicker.
  let release!: () => void
  const held = new Promise<void>((r) => { release = r })
  const route = async (r: import('@playwright/test').Route) => {
    if (r.request().method() === 'PUT') await held
    await r.fallback()
  }
  await page.route('**/api/v1/drafts/*', route)
  await editor(page).locator('p', { hasText: 'See you Thursday.' }).click()
  await page.keyboard.press('End')
  await page.keyboard.type(' Mine.')
  const a = await agent(page)
  await updateDraft(a, { draft_id: d.id, base_version: d.version, body: 'Thanks for the list.\n\nSee you on Friday.' })
  release()
  await page.unroute('**/api/v1/drafts/*', route)
  const bar = page.getByTestId('live-conflict')
  await expect(bar).toBeVisible()
  await expect(bar.getByRole('button', { name: 'Use mine' })).toBeVisible()
  await expect(editor(page)).toContainText('See you Thursday. Mine.')
  await bar.getByRole('button', { name: 'Use theirs' }).click()
  await expect(editor(page)).toContainText('See you on Friday.')
  await expect(card(page, d.id)).toHaveCount(0)
  await a.dispose()
})
