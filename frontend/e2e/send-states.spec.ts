// ACT-SURFACES: web.reader web.list web.nav
// What has not left yet, and one draft per thread. The seeded identity's
// connector is disabled and its undo window is 30 s, so a send stays queued; each test cancels what it
// queued. A failed send cannot happen here (no worker), so that test answers GET /outbox?active=true itself.
// Covers: draft.resume (Reply opens the thread's saved draft, "Start a new reply" makes a new one),
// draft.outbox (the Outbox nav entry exists only while something is queued or failed; the Outbox list),
// draft.retry_send (Retry on a failed send, in the thread and in the Outbox), approval.approve (Approve on
// a draft that waits for approval).
import type { Page } from '@playwright/test'
import { conv, expect, outbox, test } from './fixtures'
import { openConversation } from './helpers'

const SUBJECT = 'E2E Reply to me'

const open = (page: Page) => openConversation(page, SUBJECT, { heading: false })
const replyBtn = (page: Page) => page.locator('.reply-bar').getByRole('button', { name: 'Reply', exact: true })
const box = (page: Page) => page.locator('[data-inline-reply]')

async function drafts(page: Page) {
  const id = (await conv(page.request, SUBJECT)).id
  const j = await (await page.request.get(`/api/v1/conversations/${id}`)).json()
  return (j.messages as { id: number; draft: boolean }[]).filter((m) => m.draft)
}

test('draft.resume: Reply opens the draft the thread already has; Start a new reply makes another', async ({ page }) => {
  await open(page)
  await replyBtn(page).click()
  await box(page).locator('[contenteditable="true"]').first().click()
  await page.keyboard.type('E2E first answer')
  await expect(box(page).getByText('Draft saved')).toBeVisible()
  await expect.poll(async () => (await drafts(page)).length).toBe(1)

  // Leave and come back: Reply (on the message) resumes that draft instead of starting a second one.
  await page.goto('/mail/inbox')
  await open(page)
  await page.locator('article.msg').first().getByRole('button', { name: 'Reply', exact: true }).click()
  await expect(box(page)).toContainText('E2E first answer')
  const t = page.locator('.toast').filter({ hasText: 'Opened your existing draft.' })
  await expect(t).toBeVisible()
  expect((await drafts(page)).length).toBe(1)

  // The toast's second choice: a new, empty reply.
  await t.getByRole('button', { name: 'Start a new reply' }).click()
  await expect(box(page)).not.toContainText('E2E first answer')
})

test('draft.outbox: Sending… until it leaves, the thread says Sending, and the Outbox appears only meanwhile', async ({ page }) => {
  await open(page)
  // No Outbox entry while nothing is on its way.
  await expect(page.getByRole('link', { name: /^Outbox/ })).toHaveCount(0)
  await replyBtn(page).click()
  await box(page).locator('[contenteditable="true"]').first().click()
  await page.keyboard.type('E2E on its way')
  await expect(box(page).getByText('Draft saved')).toBeVisible()
  await box(page).getByRole('button', { name: 'Send', exact: true }).click()
  // The toast does not say "sent" while the message waits in the undo window.
  await expect(page.locator('.toast').filter({ hasText: 'Sending…' })).toBeVisible()
  await expect(page.locator('.toast').filter({ hasText: 'Message sent' })).toHaveCount(0)
  // The thread shows the message as Sending, with Undo.
  await expect(page.getByTestId('send-status').filter({ hasText: 'Sending…' })).toBeVisible()
  // The Outbox entry appears in the mail nav while it is queued.
  const entry = page.getByRole('link', { name: /^Outbox/ }).first()
  await expect(entry).toBeVisible()
  await entry.click()
  await expect(page.getByTestId('outbox-row').filter({ hasText: `Re: ${SUBJECT}` })).toContainText('Sending…')

  const ob = (await outbox(page.request)).find((o) => o.subject === `Re: ${SUBJECT}` && o.status === 'queued')!
  expect((await page.request.post(`/api/v1/outbox/${ob.id}/cancel`)).ok()).toBeTruthy()
  // Nothing queued any more: the entry goes away again.
  await expect(page.getByRole('link', { name: /^Outbox/ })).toHaveCount(0)
})

test('draft.retry_send: a failed send says Not sent in plain words, with Retry, Edit and the raw error under Details', async ({ page }) => {
  const id = (await conv(page.request, SUBJECT)).id
  let retried = 0
  const failed = {
    id: 999001, status: 'failed', send_at: new Date().toISOString(), recipients: ['marco@example.org'], subject: `Re: ${SUBJECT}`,
    identity_id: 1, token_name: null, approval_reason: '', error: 'SMTPRecipientsRefused: 550 5.1.1 <marco@example.org>: no such user',
    conversation_id: id, created_at: new Date().toISOString(), sent_at: null, draft_id: null, sent_message_id: null, payload: {},
  }
  await page.route('**/api/v1/outbox?active=true', (r) => r.fulfill({ json: retried ? [] : [failed] }))
  await page.route('**/api/v1/outbox/999001/retry', (r) => {
    retried++
    return r.fulfill({ json: { outbox: { ...failed, status: 'queued', error: '' } } })
  })
  await open(page)
  const line = page.getByTestId('send-status').filter({ hasText: 'Not sent.' })
  await expect(line).toContainText('An address was refused by the mail server. Check the recipients.')
  // The raw error is only behind Details.
  await expect(line.getByText('SMTPRecipientsRefused')).toBeHidden()
  await line.getByText('Details').click()
  await expect(line.getByText(/SMTPRecipientsRefused/)).toBeVisible()
  // The Outbox lists it with the same words.
  const entry = page.getByRole('link', { name: /^Outbox/ }).first()
  await expect(entry).toBeVisible()
  await entry.click()
  const row = page.getByTestId('outbox-row')
  await expect(row).toContainText('Not sent')
  await expect(row).toContainText('An address was refused by the mail server')
  await row.getByRole('button', { name: 'Retry' }).click()
  await expect.poll(() => retried).toBe(1)
  await expect(page.locator('.toast').filter({ hasText: 'Sending again' })).toBeVisible()
})

test('approval.approve: a draft that waits for approval says so in the thread, with Approve and Edit', async ({ page }) => {
  await open(page)
  await replyBtn(page).click()
  await box(page).locator('[contenteditable="true"]').first().click()
  await page.keyboard.type('E2E Ambrose wrote this')
  await expect(box(page).getByText('Draft saved')).toBeVisible()
  const [d] = await drafts(page)
  const id = (await conv(page.request, SUBJECT)).id
  let approved = 0
  const asked = {
    id: 999002, status: 'awaiting_approval', send_at: new Date().toISOString(), recipients: ['marco@example.org'], subject: `Re: ${SUBJECT}`,
    identity_id: 1, token_name: null, approval_reason: 'outside the conversation', error: '', conversation_id: id,
    created_at: new Date().toISOString(), sent_at: null, draft_id: d.id, sent_message_id: null, payload: {},
  }
  await page.route('**/api/v1/outbox?active=true', (r) => r.fulfill({ json: approved ? [] : [asked] }))
  await page.route('**/api/v1/outbox/999002/approve', (r) => {
    approved++
    return r.fulfill({ json: { outbox: { ...asked, status: 'queued' } } })
  })
  await page.goto('/mail/inbox')
  await open(page)
  const line = page.getByTestId('send-status').filter({ hasText: 'Waiting for your approval to send to marco@example.org' })
  await expect(line).toBeVisible()
  await expect(line.getByRole('button', { name: 'Edit' })).toBeVisible()
  await line.getByRole('button', { name: 'Approve' }).click()
  await expect.poll(() => approved).toBe(1)
  await expect(page.locator('.toast').filter({ hasText: 'Approved. Sending to marco@example.org' })).toBeVisible()
})
