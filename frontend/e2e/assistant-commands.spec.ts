// ACT-SURFACES: web.panel
// Slash commands in the Ambrose panel (features/rail/assistant/commands.ts, backend agent/commands.py,
// ACT agent.commands): `/` at the start of a message lists the commands before the skills; app commands
// run in the panel without a request to the model; ask commands and /model go to the server, which
// answers /model itself (error code "command", shown as an answer). The chat stream is faked; the
// command list is the server's.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const sse = (events: [string, unknown][]) => events.map(([name, data], i) => `event: ${name}\ndata: ${JSON.stringify({ ...(data as object), seq: i + 1 })}\n\n`).join('')

async function openAmbrose(page: Page) {
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await expect(page.locator('.side-panel .chat')).toBeVisible()
}

const box = (page: Page) => page.locator('.side-panel .chat-input textarea')

/** Fakes POST /agent/chat: each request is kept, and answered with `reply(body)`. */
async function fakeChat(page: Page, reply: (body: Record<string, unknown>) => [string, unknown][]) {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as Record<string, unknown>
    bodies.push(body)
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse(reply(body)) })
  })
  return bodies
}

test('typing / lists the commands first, and Enter inserts one', async ({ page }) => {
  await openAmbrose(page)
  await box(page).fill('/')
  const pop = page.locator('.skill-pop')
  await expect(pop).toBeVisible()
  await expect(pop.locator('[role=option]').first()).toContainText('/new')
  await expect(pop).toContainText('Start a new chat')
  await box(page).fill('/tod')
  await expect(pop.locator('[role=option]')).toHaveCount(1)
  await box(page).press('Enter')
  await expect(box(page)).toHaveValue('/today ')
  // Inside a sentence, `/` lists skills only.
  await box(page).fill('please reply /')
  await expect(pop.locator('[role=option]').filter({ hasText: '/new' })).toHaveCount(0)
})

test('app commands run in the panel; ask commands go to the server', async ({ page }) => {
  const bodies = await fakeChat(page, (b) => [
    ['session', { session_id: 'e2e-cmd', user_message_id: 1, title: String(b.message) }],
    ['final', { content: 'Here is your day.', citations: [] }],
    ['done', {}],
  ])
  // The command list arrives after the first message: /help waits for it and still runs here.
  await page.route('**/api/v1/agent/commands', async (route) => {
    await new Promise((r) => setTimeout(r, 1500))
    await route.fallback()
  })
  await openAmbrose(page)

  await box(page).fill('/help')
  await box(page).press('Enter')
  const log = page.locator('.side-panel .chat-log')
  await expect(log).toContainText('/today')
  await expect(log).toContainText('This chat')
  expect(bodies).toHaveLength(0) // /help asks no one

  await box(page).fill('/local')
  await box(page).press('Enter')
  await expect(page.getByTestId('chat-local-only')).toBeVisible()

  await box(page).fill('/today')
  await box(page).press('Enter')
  await expect(log).toContainText('Here is your day.')
  expect(bodies).toHaveLength(1)
  expect(bodies[0].message).toBe('/today')
  expect(bodies[0].local_only).toBe(true)

  await box(page).fill('/copy')
  await box(page).press('Enter')
  await expect(page.locator('.toast').filter({ hasText: /Copied|Could not copy/ })).toBeVisible()

  await box(page).fill('/new')
  await box(page).press('Enter')
  await expect(log).not.toContainText('Here is your day.')
  await expect(page.getByTestId('chat-local-only')).toHaveCount(0)
  expect(bodies).toHaveLength(1)
})

test('a command\'s own reply is not an answer: /note after /copy in an empty chat saves nothing', async ({ page }) => {
  const notes: string[] = []
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().includes('/api/v1/notes')) notes.push(r.url()) })
  await openAmbrose(page)
  const log = page.locator('.side-panel .chat-log')
  await box(page).fill('/copy')
  await box(page).press('Enter')
  await expect(log.getByText('There is no answer in this chat yet.')).toHaveCount(1)
  await box(page).fill('/note')
  await box(page).press('Enter')
  await expect(log.getByText('There is no answer in this chat yet.')).toHaveCount(2)
  expect(notes).toHaveLength(0)
})

test("the server's own reply to a command shows as an answer, not an error", async ({ page }) => {
  await fakeChat(page, () => [['error', { detail: 'This chat uses m, a local model on your own server.', code: 'command' }]])
  await openAmbrose(page)
  await box(page).fill('/model')
  await box(page).press('Enter')
  const log = page.locator('.side-panel .chat-log')
  await expect(log).toContainText('a local model on your own server')
  await expect(log.locator('.chat-error, [role=alert]')).toHaveCount(0)
})
