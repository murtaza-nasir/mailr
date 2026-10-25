// ACT-SURFACES: web.reader
// Attached emails (mail.open_attached_email): a message that
// carries another email shows its own text only; the attached email is a card that opens to its
// sanitised body, with its sender and a download as .eml. A forward as attachment (no text of its
// own) opens its card at once. A search that matches only inside an attached email says so on the
// row. Messages are ingested for the seeded user through the worker's seeder (fixtures.ts ingest).
// Covers: mail.open_attached_email.
import { expect, ingest, test } from './fixtures'

function inner(subject: string, text: string) {
  return [
    'From: Harbor Property <office@harborproperty.example>', 'To: ops@example.org', `Subject: ${subject}`,
    'Date: Mon, 5 Oct 2026 09:30:00 +0000', `Message-ID: <${Date.now()}-${Math.random().toString(16).slice(2)}@harborproperty.example>`,
    'MIME-Version: 1.0', 'Content-Type: text/html; charset=utf-8', '',
    `<p>${text}</p><script>document.title='x'</script>`,
  ].join('\r\n')
}

function outer(to: string, subject: string, own: string | null, attached: string) {
  const b = `b${Date.now()}`
  return [
    'From: Dana Tester <dana@example.org>', `To: ${to}`, `Subject: ${subject}`, `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${Date.now()}-${Math.random().toString(16).slice(2)}@example.org>`, 'MIME-Version: 1.0',
    `Content-Type: multipart/mixed; boundary="${b}"`, '',
    ...(own !== null ? [`--${b}`, 'Content-Type: text/plain; charset=utf-8', '', own] : []),
    `--${b}`, 'Content-Type: message/rfc822', 'Content-Disposition: attachment', '', attached,
    `--${b}--`, '',
  ].join('\r\n')
}

test('mail.open_attached_email: the card opens to the attached email; the outer body stays its own', async ({ page, seeded }) => {
  const ids = await ingest(seeded.email, outer(seeded.email, 'E2E Fwd: access cards', 'Forwarding this for the file.',
    inner('New access cards', 'Old cards stop working on Friday at 6 PM.')))
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  const article = page.locator('article').last()
  await expect(article.locator('.msg-body iframe').first()).toBeVisible()
  const outerFrame = article.locator('.msg-body iframe').first().contentFrame()
  await expect(outerFrame.getByText('Forwarding this for the file.')).toBeVisible()
  await expect(outerFrame.getByText(/Old cards stop working/)).toHaveCount(0)

  const card = page.getByTestId('attached-email')
  await expect(card).toHaveCount(1)
  const toggle = card.getByRole('button', { name: /Attached email: New access cards — Harbor Property/ })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(card.locator('.ae-excerpt')).toContainText('Old cards stop working')
  // Not listed again with the files.
  await expect(article.locator('.attachments')).toHaveCount(0)
  await expect(card.getByRole('link', { name: 'Download New access cards.eml' })).toHaveAttribute('href', /\/attachments\/\d+\?download=1$/)

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(card.locator('.ae-meta')).toContainText('office@harborproperty.example')
  const innerFrame = card.locator('.ae-body iframe').contentFrame()
  await expect(innerFrame.getByText('Old cards stop working on Friday at 6 PM.')).toBeVisible()
  await expect(innerFrame.locator('script')).toHaveCount(0)
  await page.screenshot({ path: test.info().outputPath('attached-open.png') })
})

test('mail.open_attached_email: a forward as attachment opens its card; search says the match is in it', async ({ page, seeded }) => {
  await ingest(seeded.email, outer(seeded.email, 'E2E Fwd only', null, inner('Quarterly report', 'The quokkaline numbers are final.')))
  await ingest(seeded.email, outer(seeded.email, 'E2E Own words', 'Our quokkaline order shipped.', inner('Unrelated', 'Nothing here.')))
  await page.goto('/mail/search?q=quokkaline')
  const rows = page.getByRole('row')
  const fwd = rows.filter({ hasText: 'E2E Fwd only' })
  const own = rows.filter({ hasText: 'E2E Own words' })
  await expect(fwd).toHaveCount(1)
  await expect(own).toHaveCount(1)
  await expect(fwd.getByTestId('match-in-attached')).toHaveText('In an attached email')
  await expect(own.getByTestId('match-in-attached')).toHaveCount(0)
  await page.screenshot({ path: test.info().outputPath('attached-search.png') })

  await fwd.click()
  const card = page.getByTestId('attached-email')
  await expect(card.getByRole('button', { name: /Attached email: Quarterly report/ })).toHaveAttribute('aria-expanded', 'true')
  await expect(card.locator('.ae-body iframe').contentFrame().getByText('The quokkaline numbers are final.')).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('attached-forward-only.png') })
})

test('mail.open_attached_email: the card on a phone', async ({ page, seeded }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const ids = await ingest(seeded.email, outer(seeded.email, 'E2E Phone fwd', null,
    inner('A rather long subject line for the attached email on a narrow screen', 'Phone text of the attached email.')))
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  const card = page.getByTestId('attached-email')
  await expect(card.locator('.ae-body iframe').contentFrame().getByText('Phone text of the attached email.')).toBeVisible()
  const box = await card.boundingBox()
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: test.info().outputPath('attached-phone.png') })
})
