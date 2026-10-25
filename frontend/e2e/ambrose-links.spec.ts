// ACT-SURFACES: web.panel
// Links in Ambrose's chat on a wide screen (features/rail/assistant/ambroseNav.ts): Ambrose sits beside
// the page, so an item opened from the chat shows next to it and the panel stays open. A link in an
// answer to one of mailr's own pages opens in the app; a web link opens in a new tab. The phone layout,
// where Ambrose steps aside: e2e/phone/ambrose-links-phone.spec.ts. The model is faked.
import { conv, expect, test } from './fixtures'

const sse = (events: [string, unknown][]) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('')

test('a citation and an in-app link open beside Ambrose; the panel stays open', async ({ page }) => {
  const c = await conv(page.request, 'E2E Read me')
  await page.route('**/api/v1/agent/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse([
      ['session', { session_id: 'e2e-links-wide' }],
      ['final', { content: 'This one [1](mailr-cite:1); see [the week](/calendar/week) and [the web](https://example.org/).', citations: [
        { n: 1, kind: 'message', conversation_id: c.id, message_id: null, subject: 'E2E Read me', from: 'E2E Sender', date: null, quote: null, verified: true },
      ] }],
      ['done', {}],
    ]) })
  })
  await page.route('**/api/v1/agent/sessions/e2e-links-wide', (route) => route.fulfill({ json: { id: 'e2e-links-wide', turns: [] } }))
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await page.getByLabel('Message Ambrose').fill('Which email')
  await page.getByLabel('Message Ambrose').press('Enter')
  const answer = page.locator('.side-panel .chat-turn').last()
  await expect(answer).toContainText('This one')

  await answer.locator('.cite-chip').first().click()
  await expect(page.getByRole('heading', { name: 'E2E Read me' })).toBeVisible()
  await expect(page.locator('.side-panel')).toBeVisible()
  await expect(page.getByTestId('ambrose-return')).toHaveCount(0)

  await expect(answer.getByRole('link', { name: 'the web' })).toHaveAttribute('target', '_blank')
  await answer.getByRole('link', { name: 'the week' }).click()
  await expect(page).toHaveURL(/\/calendar\/week$/)
  await expect(page.locator('.side-panel .chat-turn').last()).toContainText('This one')
})
