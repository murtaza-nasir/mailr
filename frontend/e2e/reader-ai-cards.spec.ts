// Ambrose's brief and the summary above a long thread: both open,
// they pushed the newest message below the fold. In a thread of three messages they now start
// folded into one strip, and the newest message starts in the top half of the screen.
// Covers: web.reader.
import { expect, ingest, test } from './fixtures'

function mail(subject: string, n: number, tag: string): string {
  const date = new Date(Date.now() - (3 - n) * 3600_000).toUTCString().replace('GMT', '+0000')
  return [
    'From: Dana Whitfield <dana.whitfield@e2e.example.org>', 'To: me@example.com', `Subject: ${n ? 'Re: ' : ''}${subject}`,
    `Message-ID: <${tag}-${n}@e2e.example.org>`, ...(n ? [`In-Reply-To: <${tag}-${n - 1}@e2e.example.org>`, `References: <${tag}-0@e2e.example.org>`] : []),
    `Date: ${date}`, 'MIME-Version: 1.0', 'Content-Type: text/plain; charset=utf-8', '', `Message ${n} of the brief thread.`, '',
  ].join('\r\n')
}

test('a three-message thread: brief and summary fold into one strip above the messages', async ({ page, seeded }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const tag = `ai${Date.now()}`
  const subject = `E2E Rebrand brief ${tag}`
  let cid = 0
  for (let n = 0; n < 3; n++) cid = (await ingest(seeded.email, mail(subject, n, tag))).conversation_id
  // A brief and a summary as Ambrose would leave them.
  await page.route(new RegExp(`/api/v1/conversations/[^/]+/brief$`), (r) => r.fulfill({ json: { brief: {
    id: 1, order_id: 1, order_name: 'Client asks', trigger: 'message', conversation_id: cid, message_id: null, status: 'done', condition: {},
    actions: [], session_id: null, output: 'Dana asks you to confirm both directions for Thursday at 2 PM.\n\nThe van wrap can come last.',
    citations: [], delivered: ['brief'], error: '', created_at: null, finished_at: null } } }))
  await page.route(new RegExp(`/api/v1/conversations/[^/]+/summary$`), (r) => r.fulfill({ json: {
    status: 'ready', summary: ['Dana asks for both directions by Thursday.', 'The van wrap is in scope.'], action_items: ['Confirm Thursday at 2 PM.'] } }))
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first().click()
  const cards = page.locator('.ai-cards')
  await expect(cards.locator('.brief-card')).toBeVisible()
  await expect(cards.locator('.summary-card:not(.brief-card)')).toBeVisible()
  await expect(cards.locator('.summary-card.open')).toHaveCount(0)
  await expect(cards.locator('.brief-teaser')).toContainText('Dana asks you to confirm')
  expect((await cards.boundingBox())!.height).toBeLessThan(60)
  // Opening one shows it in full.
  await cards.locator('.brief-card .head').click()
  await expect(cards.locator('.brief-card .body')).toContainText('The van wrap can come last.')
})
