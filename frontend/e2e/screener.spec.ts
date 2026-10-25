// ACT-SURFACES: web.screener web.settings
// The sender screener (features/mail/ScreenerView.tsx, Settings → Screener): first-time senders wait
// in Mail → Screener; Allow sends their mail to the Inbox, Block to Spam, Label to Feed, and the card
// leaves the list. Classic and Cards, desktop and phone width. ACT screener.review, screener.allow,
// screener.block, screener.label; Settings: screener.configure and screener.forget.
import { expect, test, type Page } from './fixtures'

test.use({ screener: true })

const card = (page: Page, name: string) => page.getByRole('list', { name: 'Senders waiting' }).getByRole('listitem').filter({ hasText: name })

async function subjects(page: Page, view: string): Promise<string[]> {
  const r = await page.request.get(`/api/v1/conversations?view=${view}`)
  expect(r.ok()).toBeTruthy()
  return ((await r.json()).items as { subject: string }[]).map((c) => c.subject)
}

for (const theme of ['classic', 'cards'] as const) {
  for (const phone of [false, true]) {
    test(`${theme}${phone ? ' phone' : ''}: review the Screener and allow, block and label senders`, async ({ page }) => {
      await page.setViewportSize(phone ? { width: 390, height: 844 } : { width: 1440, height: 900 })
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
      expect(await subjects(page, 'inbox')).not.toContain('E2E Screener pitch')
      await page.goto('/mail/screener')
      await expect(page.getByRole('heading', { name: 'Screener' })).toBeVisible()
      await expect(page.getByText('3 new senders waiting')).toBeVisible()
      await expect(page.getByRole('list', { name: 'Senders waiting' }).getByRole('listitem')).toHaveCount(3)

      const nora = card(page, 'Nora Pitch')
      await expect(nora).toContainText('Everyone at brightpitch.example')
      await expect(nora).toContainText('2 messages')
      await expect(nora).toContainText('E2E Screener follow-up')
      await expect(nora.getByLabel(/Not verified/)).toBeVisible()
      await expect(card(page, 'Leo Martins')).toContainText('This address only')
      await expect(card(page, 'Weekly Letters')).toContainText('Mailing list')

      await nora.getByRole('button', { name: 'Allow' }).click()
      const toast = page.getByText('Allowed Nora Pitch: their mail is in your Inbox')
      await expect(toast).toBeVisible()
      await expect(nora).toHaveCount(0)
      // On a phone the toast sits over the cards' buttons until it goes.
      if (phone) await expect(toast).toBeHidden()
      await expect.poll(() => subjects(page, 'inbox')).toContain('E2E Screener pitch')

      const leo = card(page, 'Leo Martins')
      await leo.getByRole('button', { name: /Block/ }).click()
      await page.getByRole('menuitem', { name: 'Block: mail goes to Spam' }).click()
      await expect(leo).toHaveCount(0)
      await expect.poll(() => subjects(page, 'spam')).toContain('E2E Screener hello')

      const letters = card(page, 'Weekly Letters')
      await letters.getByRole('button', { name: /Label/ }).click()
      await page.getByRole('menuitem', { name: 'Feed' }).click()
      await expect(letters).toHaveCount(0)
      await expect(page.getByText('No new senders')).toBeVisible()
      expect(await subjects(page, 'inbox')).not.toContain('E2E Screener issue 1')
      // The card leaves at once; the server's numbers follow when the request is done.
      await expect.poll(async () => (await (await page.request.get('/api/v1/counts')).json()).views.screener.total).toBe(0)
    })
  }
}

test('the nav shows the senders waiting, and a card opens the held conversation', async ({ page }) => {
  await page.goto('/mail/inbox')
  const nav = page.getByRole('link', { name: /Screener/ }).first()
  await expect(nav).toBeVisible()
  await expect(nav).toContainText('3')
  await nav.click()
  await expect(page).toHaveURL(/\/mail\/screener$/)
  await card(page, 'Leo Martins').getByRole('button', { name: 'Read: E2E Screener hello' }).click()
  await expect(page).toHaveURL(/\/mail\/screener\/c/)
  // The held message itself, in the reader's body frame (the card's snippet says the same words).
  await expect(page.getByRole('list', { name: 'Senders waiting' })).toHaveCount(0)
  await expect(page.getByRole('article', { name: 'Message from Leo Martins' })).toBeVisible()
  await expect(page.frameLocator('iframe[title="Message body"]').last().getByText('We met at the conference last week.')).toBeVisible()
})

test('Settings → Screener: accounts, turning it off, and screening a sender again', async ({ page }) => {
  await page.goto('/mail/screener')
  await card(page, 'Leo Martins').getByRole('button', { name: 'Allow' }).click()
  await expect(card(page, 'Leo Martins')).toHaveCount(0)
  await page.getByRole('button', { name: 'Screener settings' }).click()
  await expect(page).toHaveURL(/\/settings\/mail\/spam#screener$/)
  const sw = page.getByRole('switch', { name: 'Screen first-time senders' })
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  // The e2e account (an address on example.com over IMAP) is a personal account: screened by default.
  await expect(page.getByText(/Default: screened \(personal account\)/)).toBeVisible()
  // Screen again: Leo's decision is undone.
  const decisions = page.getByRole('list', { name: 'Screener decisions' })
  await expect(decisions).toContainText('leo.martins@gmail.com')
  await expect(decisions).toContainText('Allowed')
  await decisions.getByRole('button', { name: 'Screen leo.martins@gmail.com again' }).click()
  await expect(page.getByText('No decisions yet.')).toBeVisible()
  // Off: the waiting mail goes back to the Inbox (a worker job), and the nav entry goes once none wait.
  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  const s = await (await page.request.get('/api/v1/screener/settings')).json()
  expect(s.enabled).toBe(false)
  await page.goto('/mail/screener')
  await expect(page.getByRole('heading', { name: 'Screener' })).toBeVisible()
})
