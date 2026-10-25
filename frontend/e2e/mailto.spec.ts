// ACT-SURFACES: web.editor
// Every "email this" opens mailr's own composer (features/compose/mailtoLinks.ts, lib/mailto.ts), filled
// in and never sent: the /compose link the browser's email-link handler opens (also signed out, after
// sign-in), a mailto: link in a message body, in Ambrose's answer, on a person card and the invite
// dialog's Email it. Settings → Mail → Writing → Email links asks the browser to use mailr for mailto:
// links (settings.mail_handler) and says honestly when a browser cannot.
// Cells: ACT compose.deeplink @web.editor, ACT compose.deeplink @web.reader, ACT compose.deeplink @web.panel,
// ACT compose.deeplink @web.people, ACT compose.deeplink @web.settings, ACT settings.mail_handler @web.settings
import { accessTest, conv, expect, outbox, signIn, test, type Page } from './fixtures'

async function composer(page: Page) {
  const win = page.locator('.compose-win').last()
  await expect(win).toBeVisible()
  return win
}

const chip = (win: ReturnType<Page['locator']>, email: string) => win.locator(`.recip-chip[data-email="${email}"]`)

test('compose.deeplink: /compose?mailto= opens a filled-in draft over the inbox, and nothing is sent', async ({ page }) => {
  const link = 'mailto:%22Lee,%20Dana%22%20%3Cdana@example.com%3E,bob+news@example.org?cc=erin@example.com&subject=Plans%20%26%20dates&body=Line%20one%0D%0A%0D%0ALine%20two'
  await page.goto(`/compose?mailto=${encodeURIComponent(link)}`)
  const win = await composer(page)
  await expect(page).toHaveURL(/\/mail\/inbox$/)
  await expect(chip(win, 'dana@example.com')).toBeVisible()
  await expect(chip(win, 'bob+news@example.org')).toBeVisible()
  await expect(chip(win, 'erin@example.com')).toBeVisible()
  await expect(win.getByLabel('Subject')).toHaveValue('Plans & dates')
  await expect(win.locator('.ProseMirror')).toContainText('Line one')
  await expect(win.locator('.ProseMirror')).toContainText('Line two')
  // The plain form of the link works too.
  await page.goto('/compose?to=carol@example.net&subject=Hello+there')
  await expect(chip(page.locator('.compose-win').last(), 'carol@example.net')).toBeVisible()
  await expect(page.locator('.compose-win').last().getByLabel('Subject')).toHaveValue('Hello there')
  // The desktop handler's form: the mailto: link put in as it is (Exec=xdg-open "…/compose?mailto=%u").
  await page.goto('/compose?mailto=mailto:frank@example.com?subject=Raw%20form&cc=gina@example.com')
  const raw = page.locator('.compose-win').filter({ has: page.locator('.recip-chip[data-email="frank@example.com"]') })
  await expect(raw.getByLabel('Subject')).toHaveValue('Raw form')
  await expect(chip(raw, 'gina@example.com')).toBeVisible()
  expect(await outbox(page.request)).toEqual([])
})

test('compose.deeplink: signed out, the link waits for sign-in and then opens the draft', async ({ browser, seeded }) => {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  await page.goto(`/compose?mailto=${encodeURIComponent('mailto:dana@example.com?subject=After%20sign-in')}`)
  await expect(page).toHaveURL(/\/login\?next=%2Fcompose%3Fmailto%3D/)
  await page.getByLabel('Email').fill(seeded.email)
  await page.getByLabel('Password').fill(seeded.password)
  await page.locator('.login-submit').click()
  const win = await composer(page)
  await expect(chip(win, 'dana@example.com')).toBeVisible()
  await expect(win.getByLabel('Subject')).toHaveValue('After sign-in')
  // A long link (more than a cookie holds) waits in the tab while the user signs in.
  await ctx.clearCookies()
  const long = 'x'.repeat(3000)
  await page.goto(`/compose?to=erin@example.com&subject=Long&body=${long}`)
  await expect(page).toHaveURL(/\/login\?next=%2Fcompose%3Fkept%3D1/)
  await page.getByLabel('Email').fill(seeded.email)
  await page.getByLabel('Password').fill(seeded.password)
  await page.locator('.login-submit').click()
  const again = page.locator('.compose-win').filter({ has: page.locator('.recip-chip[data-email="erin@example.com"]') })
  await expect(again).toBeVisible()
  await expect(again.getByLabel('Subject')).toHaveValue('Long')
  await ctx.close()
})

test('compose.deeplink @web.reader: a mailto: link in a message body opens the composer, not another app', async ({ page }) => {
  const c = await conv(page.request, 'E2E Read me')
  await page.route('**/api/v1/messages/*/body*', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    const link = '<p><a href="mailto:dana@example.com?cc=erin@example.com&amp;subject=Re%3A%20plans&amp;body=See%20you">Write to Dana</a></p>'
    body.html = link + (body.html ?? `<p>${body.text ?? ''}</p>`)
    await route.fulfill({ response: res, json: body })
  })
  await page.goto(`/mail/inbox/${c.id}`)
  const frame = page.frameLocator('iframe[title="Message body"]').last()
  await frame.getByRole('link', { name: 'Write to Dana' }).click()
  const win = await composer(page)
  await expect(chip(win, 'dana@example.com')).toBeVisible()
  await expect(chip(win, 'erin@example.com')).toBeVisible()
  await expect(win.getByLabel('Subject')).toHaveValue('Re: plans')
  await expect(win.locator('.ProseMirror')).toContainText('See you')
  // The reader stays where it was; no new tab or page opened.
  await expect(page).toHaveURL(new RegExp(`/mail/inbox/${c.id}$`))
  expect(page.context().pages()).toHaveLength(1)
})

const sse = (events: [string, unknown][]) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('')

test('compose.deeplink @web.panel: an address in Ambrose\'s answer opens the composer', async ({ page }) => {
  await page.route('**/api/v1/agent/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse([
      ['session', { session_id: 'e2e-mailto' }],
      ['final', { content: 'Write to [Dana](mailto:dana@example.com?subject=Lunch) or to bob@example.org.', citations: [] }],
      ['done', {}],
    ]) })
  })
  await page.route('**/api/v1/agent/sessions/e2e-mailto', (route) => route.fulfill({ json: { id: 'e2e-mailto', turns: [] } }))
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await page.getByLabel('Message Ambrose').fill('Who do I ask')
  await page.getByLabel('Message Ambrose').press('Enter')
  const answer = page.locator('.side-panel .chat-turn').last()
  await expect(answer).toContainText('Write to')
  await answer.getByRole('link', { name: 'Dana' }).click()
  const win = await composer(page)
  await expect(chip(win, 'dana@example.com')).toBeVisible()
  await expect(win.getByLabel('Subject')).toHaveValue('Lunch')
  // A bare address in the answer is a link too (GFM), and opens a second draft.
  await answer.getByRole('link', { name: 'bob@example.org' }).click()
  await expect(page.locator('.compose-win').filter({ has: page.locator('.recip-chip[data-email="bob@example.org"]') })).toBeVisible()
  await expect(page.locator('.side-panel')).toBeVisible()
})

test('compose.deeplink @web.people: the address on a person card writes to them', async ({ page }) => {
  await page.goto('/settings/mail/people')
  await page.locator('.ppl-row').first().click()
  const card = page.getByTestId('person-card')
  const address = card.getByTestId('person-email')
  await expect(address).toBeVisible()
  const email = (await address.textContent())!.trim()
  await expect(address).toHaveAttribute('href', `mailto:${encodeURIComponent(email)}`)
  await address.click()
  const win = await composer(page)
  await expect(chip(win, email)).toBeVisible()
})

accessTest('compose.deeplink @web.settings: the invite dialog\'s Email it fills a draft with the link and its last day', async ({ page, access }) => {
  await signIn(page, access.admin)
  await page.goto('/settings/admin/users')
  await page.getByRole('button', { name: 'Invite someone' }).click()
  const d = page.getByRole('dialog', { name: 'Invite someone' })
  const email = `dana-${Date.now()}@example.com`
  await d.getByLabel('Email', { exact: true }).fill(email)
  await d.getByLabel('Name', { exact: true }).fill('Dana Example')
  await d.getByRole('button', { name: 'Create link' }).click()
  const made = page.getByTestId('invite-made')
  const url = await made.getByRole('textbox', { name: 'Invite link' }).inputValue()
  await made.getByTestId('invite-email-it').click()
  await expect(page.getByTestId('invite-made')).toHaveCount(0)
  await expect(page.getByText('The draft has the link. Check it, then press Send.')).toBeVisible()
  const win = await composer(page)
  await expect(chip(win, email)).toBeVisible()
  await expect(win.getByLabel('Subject')).toHaveValue('Your Mailr invite')
  await expect(win.locator('.ProseMirror')).toContainText(url)
  await expect(win.locator('.ProseMirror')).toContainText('It works once, until')
  expect(await outbox(page.request)).toEqual([])
})

test('settings.mail_handler: Use Mailr for email links asks the browser for <origin>/compose?mailto=%s', async ({ page }) => {
  await page.addInitScript(() => {
    const calls: unknown[][] = []
    ;(window as unknown as { __handlerCalls: unknown[][] }).__handlerCalls = calls
    Object.defineProperty(Navigator.prototype, 'registerProtocolHandler', { configurable: true, value: (...a: unknown[]) => void calls.push(a) })
  })
  await page.goto('/settings/mail/writing#email-links')
  const sec = page.getByTestId('email-links')
  await expect(sec).toHaveAttribute('data-support', 'chromium')
  await expect(sec).toContainText('Look for the icon at the right end of the address bar.')
  // The test link is left to the browser: it shows what a link from another site does.
  await expect(sec.getByTestId('email-links-test')).toHaveAttribute('data-mailto', 'native')
  await sec.getByTestId('email-links-use').click()
  const calls = await page.evaluate(() => (window as unknown as { __handlerCalls: unknown[][] }).__handlerCalls)
  const origin = new URL(page.url()).origin
  expect(calls).toEqual([['mailto', `${origin}/compose?mailto=%s`]])
  await expect(sec).toContainText('Browsers do not tell websites your answer')
})

test.describe('settings.mail_handler in Safari', () => {
  test.use({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15' })
  test('says Safari cannot, with no button', async ({ page }) => {
    await page.goto('/settings/mail/writing#email-links')
    const sec = page.getByTestId('email-links')
    await expect(sec).toHaveAttribute('data-support', 'safari')
    await expect(sec.getByTestId('email-links-unsupported')).toContainText('Safari cannot open email links in a website.')
    await expect(sec.getByTestId('email-links-use')).toHaveCount(0)
  })
})
