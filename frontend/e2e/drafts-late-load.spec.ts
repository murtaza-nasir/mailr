// ACT-SURFACES: web.reader web.editor
// A draft whose stored copy loads slowly never costs what the user typed.
// Edit draft shows "Opening draft…" until the stored copy arrives, with no editor
// to type in; a second click, or an answer that arrives after the user started another reply, never
// replaces a form in use; a window that opened on an older copy is read-only until the stored copy
// arrives, then saves on top of it. The GETs are held back with page.route to stand for a slow server.
import type { Page, Route } from '@playwright/test'
import { apiSettled, conv, expect, test } from './fixtures'

const P = (...xs: string[]) => xs.map((x) => `<p>${x}</p>`).join('')

/** Hold back the page's GETs matching `re` by `ms` each (the n-th call by ms(n) when a function). */
async function slowGets(page: Page, re: RegExp, ms: number | ((n: number) => number)) {
  let n = 0
  await page.route(re, async (r: Route) => {
    if (r.request().method() === 'GET') await new Promise((ok) => setTimeout(ok, typeof ms === 'number' ? ms : ms(n++)))
    await r.fallback()
  })
}

const editorOf = (page: Page) => page.locator('.compose-form [contenteditable="true"]').first()

/** A reply draft in the seeded "E2E Reply to me" conversation, made through the API. */
async function replyDraft(page: Page, text: string): Promise<{ id: number; conv: number }> {
  const c = await conv(page.request, 'E2E Reply to me')
  const det = await (await page.request.get(`/api/v1/conversations/${c.id}`)).json()
  const m = det.messages.filter((x: { draft: boolean }) => !x.draft).pop()
  const got = await (await page.request.get('/api/v1/compose/reply', { params: { message_id: String(m.id), mode: 'reply' } })).json()
  const payload = 'payload' in got ? got.payload : got
  const r = await page.request.post('/api/v1/drafts', { data: { payload: { ...payload, html: P(text) } } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return { id: (await r.json()).draft.id, conv: c.id }
}

async function openConversation(page: Page, draftText: string) {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Reply to me' }).first().click()
  // The draft shows folded: open it to reach its Edit button.
  await page.getByRole('button', { name: new RegExp(`^Draft ${draftText}`) }).click()
  await expect(page.getByRole('button', { name: 'Edit draft' }).first()).toBeVisible()
}

test('Edit draft: "Opening draft…" until the stored copy arrives; a second click never replaces the typing', async ({ page }) => {
  await replyDraft(page, 'Stored text.')
  await openConversation(page, 'Stored text.')
  // The first answer takes 2 s, the second 5 s: before the fix the second replaced the form.
  await slowGets(page, /\/api\/v1\/drafts\/\d+$/, (n) => (n === 0 ? 2000 : 5000))
  const edit = page.getByRole('button', { name: 'Edit draft' }).first()
  await edit.click()
  const box = page.locator('[data-inline-reply]')
  await expect(box.getByText('Opening draft…')).toBeVisible()
  await expect(box.locator('[contenteditable="true"]')).toHaveCount(0)
  await edit.click()
  await expect(editorOf(page)).toContainText('Stored text.')
  await editorOf(page).locator('p', { hasText: 'Stored text.' }).click()
  await page.keyboard.press('End')
  await page.keyboard.type(' Typed while loading.')
  // Any second load of the draft (5 s late, above) has come back, and the page has had time to act on it.
  await apiSettled(page, /\/api\/v1\/drafts\//)
  await expect(editorOf(page)).toContainText('Stored text. Typed while loading.')
  await expect(page.locator('.compose-form')).toHaveCount(1)
})

test('Edit draft answered after a new reply was started: the reply keeps its text, the draft opens in a window', async ({ page }) => {
  await replyDraft(page, 'Older draft text.')
  await openConversation(page, 'Older draft text.')
  await slowGets(page, /\/api\/v1\/drafts\/\d+$/, 4000)
  await page.getByRole('button', { name: 'Edit draft' }).first().click()
  await page.getByRole('article', { name: 'Message from Dana Tester' }).getByRole('button', { name: 'Reply', exact: true }).click()
  // Reply resumes the thread's draft; its notice offers a new reply, which is what is wanted here.
  await page.locator('.toast').getByRole('button', { name: 'Start a new reply' }).click()
  const box = page.locator('[data-inline-reply]')
  await box.locator('[contenteditable="true"]').first().click()
  await page.keyboard.type('A new reply.')
  // The draft's answer arrives: it opens beside the reply, in a compose window.
  await expect(page.locator('.compose-win .compose-form [contenteditable="true"]')).toContainText('Older draft text.', { timeout: 15_000 })
  await expect(box.locator('[contenteditable="true"]').first()).toContainText('A new reply.')
})

test('a window opened on an older copy is read-only until the stored copy arrives, then saves on top of it', async ({ page }) => {
  const d = await (await page.request.post('/api/v1/drafts', { data: { payload: { to: [{ email: 'dana@example.org', name: 'Dana' }], cc: [], bcc: [], subject: 'Late E2E', html: P('Hi Dana,', 'See you Thursday.', 'Best,'), attachments: [] } } })).json()
  const payload = (await (await page.request.get(`/api/v1/drafts/${d.draft.id}`)).json()).payload
  const s = await page.request.post('/api/v1/send', { data: { payload, draft_id: d.draft.id, send_at: new Date(Date.now() + 3 * 86400_000).toISOString() } })
  expect(s.ok(), await s.text()).toBeTruthy()
  await page.goto('/mail/scheduled')
  // Edit cancels the send and opens the window on the outbox's copy, without its version: the
  // window asks for the stored copy (GET /live/draft/…), which is slow here.
  await slowGets(page, /\/api\/v1\/live\/draft\//, 4000)
  const cancelled = page.waitForResponse((r) => /\/outbox\/\d+\/cancel$/.test(r.url()))
  await page.getByRole('button', { name: 'Edit' }).first().click()
  const id = (await (await cancelled).json()).draft_id as number
  const form = page.locator('.compose-win .compose-form')
  const body = form.locator('.ProseMirror')
  await expect(body).toContainText('See you Thursday.')
  // Another device changes the middle line while the window waits.
  const other = await page.request.put(`/api/v1/drafts/${id}`, { data: { payload: { ...payload, html: P('Hi Dana,', 'See you Friday.', 'Best,') } } })
  expect(other.ok(), await other.text()).toBeTruthy()
  // Meanwhile the window says so and takes no typing (before: typing here was saved with no
  // base_version, over the other device's change).
  await expect(form.getByTestId('draft-loading')).toBeVisible()
  await expect(body).toHaveAttribute('contenteditable', 'false')
  await expect(form.getByTestId('draft-loading')).toHaveCount(0, { timeout: 15_000 })
  await expect(body).toContainText('See you Friday.')
  await body.locator('p', { hasText: 'Best,' }).click()
  await page.keyboard.press('End')
  await page.keyboard.type(' Alex')
  await expect.poll(async () => (await (await page.request.get(`/api/v1/drafts/${id}`)).json()).payload.html).toMatch(/Friday[\s\S]*Best, Alex/)
  await expect(body).toContainText('See you Friday.')
})
