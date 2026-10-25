// ACT-SURFACES: web.reader
// Content hardening in the reader: regression tests, each proof of a hole inverted.
// - An address with a right-to-left override never reaches the reader header or the clipboard.
// - An attached email's images follow the OUTER sender, never its (forgeable) inner From, and an
//   attached email offers no "Always display images from".
// - The invitation card shows the organizer's address beside its name, and flags a name that
//   shows another address.
// - A program inside an attached email gets the risky-file warning and icon.
// Messages are ingested for the seeded user through the worker's seeder (fixtures.ts ingest).
import { expect, ingest, test } from './fixtures'

const RLO = '‮'

const id = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`

test('an override inside the address never reaches the reader or the clipboard', async ({ page, seeded, context, browserName }) => {
  const raw = [
    `From: Service <"${RLO}moc.lapyap@ecivres"@evil.test>`, `To: ${seeded.email}`, 'Subject: SEC2 address override',
    `Date: ${new Date().toUTCString()}`, `Message-ID: <${id()}@evil.test>`, 'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=utf-8', '', 'Please confirm your account.', '',
  ].join('\r\n')
  const ids = await ingest(seeded.email, raw)
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  const email = page.locator('.email-text').first()
  await expect(email).toBeVisible()
  // The server drops it at ingest; the header shows the address as it is.
  expect(await email.textContent()).not.toContain(RLO)
  await expect(email).toContainText('"moc.lapyap@ecivres"@evil.test')
  await page.screenshot({ path: test.info().outputPath('reader-bidi-address.png') })
  if (browserName === 'chromium') {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.locator('.msg-head, article').first().hover()
    await page.getByTestId('addr-copy').first().click({ force: true })
    const clip = await page.evaluate(() => navigator.clipboard.readText())
    expect(clip).not.toContain(RLO)
  }
})

test('a forged inner From on an attached email never turns its images on', async ({ page, seeded }) => {
  // Remote hosts are never reached from the test.
  await page.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (r) => r.abort())
  expect((await page.request.patch('/api/v1/me/settings', { data: { images: 'ask' } })).ok()).toBeTruthy()
  // The user once chose "Always display images from news@bigshop.example" (a newsletter).
  await page.addInitScript(() => localStorage.setItem('mailr.imagesAllowed', JSON.stringify(['news@bigshop.example'])))
  const b = `b${id()}`
  const inner = [
    'From: Big Shop <news@bigshop.example>', `To: ${seeded.email}`, 'Subject: Your order',
    'Date: Mon, 5 Oct 2026 09:30:00 +0000', `Message-ID: <${id()}@forged.example>`, 'MIME-Version: 1.0',
    'Content-Type: text/html; charset=utf-8', '',
    '<p>Order details below.</p><img src="https://tracker.evil.test/open?u=victim" width="300" height="200" alt="banner">',
  ].join('\r\n')
  const outer = [
    'From: Stranger <stranger@evil.test>', `To: ${seeded.email}`, 'Subject: SEC2 attached images',
    `Date: ${new Date().toUTCString()}`, `Message-ID: <${id()}@evil.test>`, 'MIME-Version: 1.0',
    `Content-Type: multipart/mixed; boundary="${b}"`, '',
    `--${b}`, 'Content-Type: message/rfc822', 'Content-Disposition: attachment', '', inner,
    `--${b}--`, '',
  ].join('\r\n')
  const asked: string[] = []
  page.on('request', (r) => { if (r.url().includes('/attachments/')) asked.push(r.url()) })
  const ids = await ingest(seeded.email, outer)
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  // A forward with no text of its own opens its card at once.
  const card = page.getByTestId('attached-email')
  await expect(card.locator('.ae-body iframe')).toBeVisible()
  await expect(card.locator('.ae-body iframe').contentFrame().getByText('Order details below.')).toBeVisible()
  // The stranger outside decides: images blocked, the banner shows, and it offers no "Always
  // display images from" for the unverified inner sender.
  await expect(card.getByTestId('images-banner')).toBeVisible()
  expect(asked.some((u) => /\/attachments\/\d+\/email\?images=allowed/.test(u))).toBe(false)
  await expect(card.getByTestId('images-banner')).not.toContainText('news@bigshop.example')
  await page.screenshot({ path: test.info().outputPath('attached-forged-from.png') })
})

test('the invitation card shows the organizer address and flags a name that shows another one', async ({ page, seeded }) => {
  const b = `b${id()}`
  const ics = [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//evil//EN', 'METHOD:REQUEST', 'BEGIN:VEVENT',
    `UID:${id()}@evil.test`, 'DTSTAMP:20261010T000000Z', 'DTSTART:20261020T150000Z', 'DTEND:20261020T160000Z',
    'SUMMARY:Payroll account check', 'LOCATION:Verify at https://payroll-check.evil.test',
    'ORGANIZER;CN="IT Helpdesk (helpdesk@example.com)":mailto:x@evil.test',
    `ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:${seeded.email}`, 'SEQUENCE:0', 'END:VEVENT', 'END:VCALENDAR', '',
  ].join('\r\n')
  const raw = [
    'From: IT Helpdesk <x@evil.test>', `To: ${seeded.email}`, 'Subject: SEC2 organizer name',
    `Date: ${new Date().toUTCString()}`, `Message-ID: <${id()}@evil.test>`, 'MIME-Version: 1.0',
    `Content-Type: multipart/alternative; boundary="${b}"`, '',
    `--${b}`, 'Content-Type: text/plain; charset=utf-8', '', 'Please attend.',
    `--${b}`, 'Content-Type: text/calendar; method=REQUEST; charset=utf-8', '', ics,
    `--${b}--`, '',
  ].join('\r\n')
  const ids = await ingest(seeded.email, raw)
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  await expect(page.getByTestId('invite-organizer')).toContainText('IT Helpdesk (helpdesk@example.com)')
  // The real organizer address is on the card, and the name's other address is flagged.
  await expect(page.getByTestId('invite-organizer')).toContainText('x@evil.test')
  await expect(page.getByTestId('invite-organizer-warning')).toContainText('helpdesk@example.com')
  await page.screenshot({ path: test.info().outputPath('invite-organizer.png') })
})

test('a program inside an attached email gets the risky-file warning', async ({ page, seeded }) => {
  const ib = `i${id()}`
  const ob = `o${id()}`
  const inner = [
    'From: Accounts <accounts@supplier.example>', `To: ${seeded.email}`, 'Subject: Invoice 4471',
    'Date: Mon, 5 Oct 2026 09:30:00 +0000', `Message-ID: <${id()}@supplier.example>`, 'MIME-Version: 1.0',
    `Content-Type: multipart/mixed; boundary="${ib}"`, '',
    `--${ib}`, 'Content-Type: text/plain; charset=utf-8', '', 'Invoice attached.',
    `--${ib}`, 'Content-Type: application/pdf', 'Content-Disposition: attachment; filename="Invoice-4471.pdf.exe"',
    'Content-Transfer-Encoding: base64', '', 'TVqQAAMAAAAEAAAA//8AALgAAAAAAAAAQAAAAAAAAAAA',
    `--${ib}--`, '',
  ].join('\r\n')
  const outer = [
    'From: Stranger <stranger@evil.test>', `To: ${seeded.email}`, 'Subject: SEC2 attached program',
    `Date: ${new Date().toUTCString()}`, `Message-ID: <${id()}@evil.test>`, 'MIME-Version: 1.0',
    `Content-Type: multipart/mixed; boundary="${ob}"`, '',
    `--${ob}`, 'Content-Type: message/rfc822', 'Content-Disposition: attachment', '', inner,
    `--${ob}--`, '',
  ].join('\r\n')
  const ids = await ingest(seeded.email, outer)
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  const files = page.getByTestId('attached-email').locator('.ae-files')
  const chip = files.locator('.att-chip').filter({ hasText: 'Invoice-4471.pdf.exe' })
  await expect(chip).toBeVisible()
  // As in the reader's own attachment list: the warning note and the risky icon.
  await expect(page.getByTestId('attached-email').locator('.att-warning')).toHaveCount(1)
  await expect(chip.locator('.att-risky')).toBeVisible()
  await expect(chip).toHaveAttribute('href', /\/attachments\/\d+\?download=1$/)
  await page.screenshot({ path: test.info().outputPath('attached-program.png') })
})

test('a link whose text shows another site asks first and names the real one', async ({ page, seeded }) => {
  // Remote hosts are never reached from the test.
  await page.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (r) => r.abort())
  const raw = [
    'From: Your Bank <alerts@yourbank.example>', `To: ${seeded.email}`, 'Subject: SEC masked link',
    `Date: ${new Date().toUTCString()}`, `Message-ID: <${id()}@evil.test>`, 'MIME-Version: 1.0',
    'Content-Type: text/html; charset=utf-8', '',
    '<p>Sign in at <a href="https://login.evil.test/x">www.yourbank.example</a> or read <a href="https://news.example/">the news</a>.</p>',
  ].join('\r\n')
  const ids = await ingest(seeded.email, raw)
  await page.goto(`/mail/inbox/${ids.conversation_id}`)
  const frame = page.frameLocator('iframe[title]').first()
  const opened: string[] = []
  page.context().on('page', (p) => opened.push(p.url()))
  await frame.getByText('www.yourbank.example').click()
  const dialog = page.getByRole('dialog', { name: 'This link opens another site' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('login.evil.test')
  await page.screenshot({ path: test.info().outputPath('reader-masked-link.png') })
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toBeHidden()
  expect(opened).toEqual([])
})
