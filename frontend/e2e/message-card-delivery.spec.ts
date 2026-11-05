// ACT-SURFACES: web.reader
// The details popover says which of the user's accounts and addresses received a message
// (backend core/delivery.py): "delivered to" with "(Bcc)" when To and Cc leave the user out, and
// "account" for the mail accounts it came through. The folded line adds "· Bcc to you".
import type { Page } from '@playwright/test'
import { expect, ingest, test } from './fixtures'

function mail(deliveredTo: string, to: string, subject: string) {
  return [
    `Delivered-To: ${deliveredTo}`, 'From: Lee Park <lee.park@example.org>', `To: ${to}`, `Subject: ${subject}`,
    `Date: ${new Date().toUTCString()}`, `Message-ID: <${Date.now()}-${Math.random().toString(16).slice(2)}@example.org>`,
    'MIME-Version: 1.0', 'Content-Type: text/plain; charset=utf-8', '', 'See you at the planning meeting.', '',
  ].join('\r\n')
}

/** The account the message came through, as the server sends it for mail fetched by a connector. */
async function withAccount(page: Page, cid: number) {
  await page.route(`**/api/v1/conversations/${cid}`, async (route) => {
    const r = await route.fetch()
    const j = (await r.json()) as { messages: Record<string, unknown>[] }
    for (const m of j.messages) m.account = [{ id: 1, name: 'Work', address: 'sam@northwind.example', kind: 'imap' }]
    await route.fulfill({ response: r, json: j })
  })
}

const head = (page: Page) => page.locator('.msg:not(.collapsed) .msg-head').first()

test('a Bcc: the details name the receiving address with (Bcc) and the account; the folded line says Bcc to you', async ({ page, seeded }) => {
  const ids = await ingest(seeded.email, mail(seeded.email, 'Kim Ito <kim.ito@example.org>, Ana Ruiz <ana.ruiz@example.org>', 'E2E Bcc plans'))
  await withAccount(page, ids.conversation_id)
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  await expect(head(page).getByTestId('to-bcc')).toHaveText('Bcc to you')
  await head(page).getByRole('button', { name: 'Show details' }).click()
  const details = page.locator('.details-pop')
  await expect(details).toBeVisible()
  await expect(details.locator('dt', { hasText: 'delivered to:' })).toBeVisible()
  await expect(details.getByTestId('detail-delivered-to')).toContainText(seeded.email.toLowerCase())
  await expect(details.getByTestId('detail-delivered-to')).toContainText('(Bcc)')
  await expect(details.locator('dt', { hasText: 'account:' })).toBeVisible()
  await expect(details.getByTestId('detail-account')).toHaveText('Work <sam@northwind.example>')
})

test('named in To: delivered to without (Bcc), no Bcc on the folded line, no account row without one', async ({ page, seeded }) => {
  const ids = await ingest(seeded.email, mail(seeded.email, seeded.email, 'E2E Direct plans'))
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  await expect(head(page).locator('.to-line')).toBeVisible()
  await expect(head(page).getByTestId('to-bcc')).toHaveCount(0)
  await head(page).getByRole('button', { name: 'Show details' }).click()
  const details = page.locator('.details-pop')
  await expect(details.getByTestId('detail-delivered-to')).toContainText(seeded.email.toLowerCase())
  await expect(details.getByTestId('detail-delivered-to')).not.toContainText('(Bcc)')
  await expect(details.getByTestId('detail-account')).toHaveCount(0)
})
