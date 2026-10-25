// ACT-SURFACES: web.reader
// Drafts in the reader (surface web.reader): the inline reply under an open
// conversation. Each test checks the result through the API. The seeded identity's connector is
// disabled and its undo window is 30 s, so a send only queues; each test cancels what it queued.
// Covers: draft.save, draft.discard, draft.send, draft.schedule.
import type { Page } from '@playwright/test'
import { conv, expect, outbox, test } from './fixtures'

const SUBJECT = 'E2E Reply to me'

async function reply(page: Page, text: string) {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: SUBJECT }).first().click()
  await page.locator('.reply-bar').getByRole('button', { name: 'Reply', exact: true }).click()
  const box = page.locator('[data-inline-reply]')
  await box.locator('[contenteditable="true"]').first().click()
  await page.keyboard.type(text)
  return box
}

/** The drafts in the conversation, as the API sees them now. */
async function drafts(page: Page) {
  const id = (await conv(page.request, SUBJECT)).id
  const j = await (await page.request.get(`/api/v1/conversations/${id}`)).json()
  return (j.messages as { id: number; draft: boolean }[]).filter((m) => m.draft)
}

test('draft.save while typing, draft.discard from the inline reply', async ({ page }) => {
  const box = await reply(page, 'E2E draft text')
  await expect(box.getByText('Draft saved')).toBeVisible()
  await expect.poll(async () => (await drafts(page)).length).toBe(1)

  await box.getByRole('button', { name: 'Discard draft' }).click()
  await expect(box).toHaveCount(0)
  await expect.poll(async () => (await drafts(page)).length).toBe(0)
  // The toast's Undo opens the same text again, as a new draft.
  await page.locator('.toast').filter({ hasText: 'Draft discarded' }).getByRole('button', { name: 'Undo' }).click()
  await expect(page.locator('[contenteditable="true"]').filter({ hasText: 'E2E draft text' })).toBeVisible()
})

test('draft.send queues the reply in the outbox for the undo window', async ({ page }) => {
  const box = await reply(page, 'E2E sent text')
  await box.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(box).toHaveCount(0)
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === `Re: ${SUBJECT}`).length).toBe(1)
  const ob = (await outbox(page.request)).find((o) => o.subject === `Re: ${SUBJECT}`)!
  expect(ob.status).toBe('queued')
  expect(ob.sent_at).toBeNull()
  expect(new Date(ob.send_at).getTime()).toBeGreaterThan(Date.now())
  expect((await page.request.post(`/api/v1/outbox/${ob.id}/cancel`)).ok()).toBeTruthy()
})

test('draft.schedule queues the reply for later', async ({ page }) => {
  const box = await reply(page, 'E2E scheduled text')
  await box.getByRole('button', { name: 'More send options' }).click()
  await page.getByRole('menuitem').filter({ hasText: /Tomorrow/ }).first().click()
  await expect(box).toHaveCount(0)
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === `Re: ${SUBJECT}`).length).toBe(1)
  const ob = (await outbox(page.request)).find((o) => o.subject === `Re: ${SUBJECT}`)!
  expect(ob.status).toBe('queued')
  expect(new Date(ob.send_at).getTime()).toBeGreaterThan(Date.now() + 60 * 60_000)
  expect((await page.request.post(`/api/v1/outbox/${ob.id}/cancel`)).ok()).toBeTruthy()
})

test('a Help me write draft has one blank line after the greeting, not two', async ({ page }) => {
  // The server's Markdown conversion writes a blank line as <div><br></div>, as email HTML does.
  await page.route(/\/api\/v1\/agent\/draft$/, (r) => r.fulfill({
    json: { payload: { subject: 'E2E things to bring', html: '<div>Hi,</div><div><br></div><div>Here are the items:</div><ul><li>Invoices</li><li>Quotes</li></ul><div>See you there.</div>' }, edit: false, memory_ids: [] },
  }))
  await page.goto('/mail/inbox')
  await page.getByRole('button', { name: 'Compose', exact: true }).first().click()
  const win = page.locator('.compose-form')
  await win.getByLabel('To recipients').fill('pat@example.net')
  await page.keyboard.press('Enter')
  await win.getByRole('button', { name: 'Help me write' }).click()
  await page.getByLabel('Instructions for Help me write').fill('three things to bring')
  await page.keyboard.press('Enter')
  const editor = win.locator('.ProseMirror')
  await expect(editor).toContainText('See you there.')
  // One empty paragraph between the greeting and the text, with no line break inside it.
  const blocks = await editor.evaluate((el) => Array.from(el.children, (c) => c.tagName + ':' + c.innerHTML))
  expect(blocks.slice(0, 3)).toEqual([
    'P:Hi,', expect.stringMatching(/^P:(<br class="ProseMirror-trailingBreak">)?$/), 'P:Here are the items:',
  ])
  expect(await editor.locator('p br:not(.ProseMirror-trailingBreak)').count()).toBe(0)
})

test('a conversation that was only a draft leaves the reader once the draft is gone', async ({ page }) => {
  const r = await page.request.post('/api/v1/drafts', { data: { payload: { to: [{ email: 'pat@example.net' }], subject: 'E2E lone draft', html: '<p>Lone draft text.</p>' } } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const draft = (await r.json()).draft as { id: number }
  await page.goto('/mail/drafts')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E lone draft' }).first().click()
  await expect(page.getByRole('button', { name: 'Edit draft' }).first()).toBeVisible()
  await expect(page).toHaveURL(/\/mail\/drafts\/[^/?]+/)
  // Sent (or deleted) elsewhere: the conversation no longer exists.
  expect((await page.request.delete(`/api/v1/drafts/${draft.id}`)).ok()).toBeTruthy()
  await expect(page.getByRole('button', { name: 'Edit draft' })).toHaveCount(0, { timeout: 15_000 })
  await expect(page).toHaveURL(/\/mail\/drafts(\?|$)/)
})
