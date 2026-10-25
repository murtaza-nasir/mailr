// ACT-SURFACES: web.panel
// Links in Ambrose's chat on the phone layout (features/rail/assistant/
// ambroseNav.ts). Ambrose covers the page on a phone, so an item opened from the chat used to open
// under the panel and stay hidden. Now Ambrose steps aside and the item shows; "Back to Ambrose", the
// Ambrose tab and the back gesture bring the same chat back (no new request, the reply still there).
// Web links still open in a new tab. The model is faked (a canned event stream).
import type { Page } from '@playwright/test'
import { connectCalendar, conv, expect, test } from '../fixtures'

const SHOTS = process.env.AMBROSE_SHOTS

const sse = (events: [string, unknown][]) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('')

/** Answers the chat with `events`; returns how many chat requests the page made. */
async function fakeChat(page: Page, events: () => [string, unknown][]): Promise<() => number> {
  let n = 0
  await page.route('**/api/v1/agent/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    n += 1
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse(events()) })
  })
  // A chat opened again from the server would come back empty: the tests see whether it stayed.
  await page.route('**/api/v1/agent/sessions/e2e-links', (route) => route.fulfill({ json: { id: 'e2e-links', turns: [] } }))
  return () => n
}

const mailCite = (id: number, subject: string) => ({
  n: 1, kind: 'message', conversation_id: id, message_id: null, subject, from: 'E2E Sender', date: null,
  quote: null, verified: true,
})

async function ask(page: Page, text: string) {
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  await page.goto('/mail/inbox')
  await page.getByTestId('phone-tabs').locator('[data-tab="assistant"]').click()
  await expect(page.locator('.side-panel .chat')).toBeVisible()
  await page.getByLabel('Message Ambrose').fill(text)
  await page.getByLabel('Message Ambrose').press('Enter')
}

const panel = (page: Page) => page.locator('.side-panel')
const back = (page: Page) => page.getByTestId('ambrose-return')

test('a citation opens the email over Ambrose; Back to Ambrose and the back gesture return to the chat', async ({ page }) => {
  const c = await conv(page.request, 'E2E Read me')
  const requests = await fakeChat(page, () => [
    ['session', { session_id: 'e2e-links' }],
    ['final', { content: 'The email you asked about [1](mailr-cite:1).', citations: [mailCite(c.id, 'E2E Read me')] }],
    ['done', {}],
  ])
  await ask(page, 'Find the email I should read')
  const answer = panel(page).locator('.chat-turn').last()
  await expect(answer).toContainText('The email you asked about')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/1-chat.png` })

  // The chip: Ambrose steps aside and the conversation has the screen.
  await answer.locator('.cite-chip').first().click()
  await expect(page).toHaveURL(/\/mail\/[^/]+\/[0-9A-Za-z]+/)
  await expect(panel(page)).toHaveCount(0)
  await expect(page.locator('.conv-view').getByText('E2E Read me').first()).toBeVisible()
  await expect(back(page)).toBeVisible()
  await expect(back(page)).toHaveText('Back to Ambrose')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/2-conversation.png` })

  // Back to Ambrose: the same chat, as it was (no new request, no reload from the server).
  await back(page).click()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).locator('.chat-turn').last()).toContainText('The email you asked about')
  await expect(back(page)).toHaveCount(0)
  expect(requests()).toBe(1)

  // The Sources row does the same; the browser's back then returns to the chat, on the page it was
  // open over (here the conversation the chip opened).
  const over = page.url()
  await panel(page).locator('.sources .source').first().click()
  await expect(panel(page)).toHaveCount(0)
  await expect(page.locator('.conv-view').getByText('E2E Read me').first()).toBeVisible()
  await page.goBack()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).locator('.chat-turn').last()).toContainText('The email you asked about')
  expect(page.url()).toBe(over)
  expect(requests()).toBe(1)
})

test('the Ambrose tab returns to the chat; another tab ends the way back', async ({ page }) => {
  const c = await conv(page.request, 'E2E Read me')
  await fakeChat(page, () => [
    ['session', { session_id: 'e2e-links' }],
    ['final', { content: 'Here [1](mailr-cite:1), and the week in [your calendar](/calendar/week). More on [the web](https://example.org/page).', citations: [mailCite(c.id, 'E2E Read me')] }],
    ['done', {}],
  ])
  await ask(page, 'Where is it')
  const answer = panel(page).locator('.chat-turn').last()
  await expect(answer).toContainText('Here')

  // A web link leaves mailr in a new tab.
  const web = answer.getByRole('link', { name: 'the web' })
  await expect(web).toHaveAttribute('target', '_blank')
  await expect(web).toHaveAttribute('href', 'https://example.org/page')

  // A link to one of mailr's own pages opens in the app, with Ambrose aside.
  const pages = page.context().pages().length
  await answer.getByRole('link', { name: 'your calendar' }).click()
  await expect(page).toHaveURL(/\/calendar\/week$/)
  await expect(panel(page)).toHaveCount(0)
  expect(page.context().pages().length).toBe(pages)
  await expect(back(page)).toBeVisible()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/3-calendar.png` })

  // The Ambrose tab shows the same chat.
  await page.getByTestId('phone-tabs').locator('[data-tab="assistant"]').click()
  await expect(panel(page).locator('.chat-turn').last()).toContainText('Here')
  await expect(back(page)).toHaveCount(0)

  // Leaving again, then choosing another app: the pill goes.
  await panel(page).locator('.chat-turn').last().getByRole('link', { name: 'your calendar' }).click()
  await expect(back(page)).toBeVisible()
  await page.getByTestId('phone-tabs').locator('[data-tab="mail"]').click()
  await expect(back(page)).toHaveCount(0)
  await expect(panel(page)).toHaveCount(0)
})

test('a task citation opens the task over Ambrose; back returns to the chat', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const r = await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Cited task' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const task = (await r.json()) as { uid: string }
  await fakeChat(page, () => [
    ['session', { session_id: 'e2e-links' }],
    ['final', { content: 'You have one task [1](mailr-cite:1).', citations: [{ n: 1, kind: 'task', uid: task.uid, title: 'E2E Cited task', subject: 'E2E Cited task', from: 'Tasks', conversation_id: null, message_id: null, quote: null, verified: true, date: null }] }],
    ['done', {}],
  ])
  await ask(page, 'What tasks do I have')
  const answer = panel(page).locator('.chat-turn').last()
  await answer.locator('.cite-chip').first().click()
  await expect(page).toHaveURL(/\/tasks\/.*task\//)
  await expect(panel(page)).toHaveCount(0)
  await expect(page.getByTestId('tasks-app')).toBeVisible()
  await expect(page.getByTestId('task-pane')).toBeVisible()
  await expect(page.getByTestId('task-pane').getByRole('textbox').first()).toHaveValue('E2E Cited task')
  // The task's sheet has the bottom of the screen: the pill is hidden there; the Ambrose tab stays.
  await expect(back(page)).toBeHidden()
  await expect(page.getByTestId('phone-tabs').locator('[data-tab="assistant"]')).toBeVisible()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/4-task.png` })
  await page.goBack()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).locator('.chat-turn').last()).toContainText('You have one task')
})
