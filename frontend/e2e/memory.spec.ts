// ACT-SURFACES: web.settings web.people web.panel
// One Memory page and one People directory: learning status and Learn now,
// To review (memory.accept_suggestion, memory.dismiss_suggestion), About you, People with their card
// (person.card, person.memories), Topics (memory.topic_edit), the kinds with examples (memory.add),
// a commitment as a task (memory.make_task), "Used memories" under an answer (memory.used), and
// "From a chat" opening the chat a memory came from (memory.open_chat).
// ACT memory.learning_status, ACT memory.learn_now
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

test.use({ extras: true })

declare global {
  interface Window {
    __chat: { push: (event: string, data: unknown) => void; end: () => void; bodies: unknown[]; streams: number }
  }
}

test('the Memory page: learning, To review, About you, People and Topics, one search', async ({ page }) => {
  await page.goto('/settings/ambrose/memory')
  await expect(page.getByTestId('learning-status')).toHaveText(/^Last learned .+ · 1 new$/)
  const review = page.getByTestId('memories-review')
  await expect(review.locator('.as-mem')).toHaveCount(2)
  await expect(review).toContainText('From a chat')
  await expect(page.getByTestId('memories-about')).toContainText('You teach on Tuesday evenings')
  const people = page.locator('#memory-people')
  await expect(people.getByRole('heading', { name: 'Priya Natarajan' })).toBeVisible()
  await expect(people.getByRole('heading', { name: 'Jordan Lee' })).toBeVisible()
  await expect(page.locator('#memory-topics').getByRole('heading', { name: 'Northwind' })).toBeVisible()

  // Keep the chat suggestion: it joins About you. Dismiss the other.
  await review.locator('.as-mem', { hasText: 'You are vegetarian' }).getByRole('button', { name: 'Keep' }).click()
  await expect(page.getByTestId('memories-about')).toContainText('You are vegetarian')
  await review.locator('.as-mem', { hasText: 'Priya prefers calls' }).getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.locator('#memory-review')).toHaveCount(0)

  // One search across every section; the kind chips filter and show an example.
  await page.getByLabel('Search memories').fill('priya')
  await expect(page.locator('#memory-people .as-mem')).toHaveCount(2)
  await expect(page.locator('#memory-topics')).toHaveCount(0)
  await page.getByLabel('Search memories').fill('')
  const routine = page.getByRole('group', { name: 'Filter by kind' }).getByRole('button', { name: 'Routine' })
  await expect(routine).toHaveAttribute('data-tip', 'Teaches Tuesday evenings')
  await routine.click()
  await expect(page.locator('.as-mem')).toHaveCount(1)
  await routine.click()

  // Learn now queues a pass.
  const learn = page.waitForResponse((r) => r.url().includes('/assistant/memories/learn') && r.request().method() === 'POST')
  await page.getByTestId('learn-now').click()
  expect((await learn).ok()).toBeTruthy()
})

// ACT memory.open_chat: Keep on a suggestion from a chat keeps the chat turn; "From a chat" opens
// that chat with the message marked. A deleted chat leaves the words without a link.
test('a kept suggestion from a chat links back to the chat turn', async ({ page, seeded }) => {
  const chat = (seeded.extras as unknown as { memory: { chat: { session_id: string; chat_message_id: number } } }).memory.chat
  await page.goto('/settings/ambrose/memory')
  const review = page.getByTestId('memories-review')
  const sugg = review.locator('.as-mem', { hasText: 'You are vegetarian' })
  await expect(sugg.getByRole('button', { name: 'From a chat' })).toBeVisible()
  await sugg.getByRole('button', { name: 'Keep' }).click()
  const kept = page.getByTestId('memories-about').locator('.as-mem', { hasText: 'You are vegetarian' })
  await kept.getByRole('button', { name: 'From a chat' }).click()
  const log = page.locator('.chat-log:visible')
  await expect(log).toContainText('Find a restaurant near the office for Friday.')
  const found = log.locator(`.chat-user[data-message-id="${chat.chat_message_id}"]`)
  await expect(found).toContainText("I'm vegetarian")
  await expect(found).toHaveClass(/chat-found/)
  await expect(found).toBeInViewport()
  // The chat goes on from there: the tab's chat is this one.
  expect(await page.evaluate(() => sessionStorage.getItem('mailr.assistant.session'))).toBe(chat.session_id)

  expect((await page.request.delete(`/api/v1/agent/sessions/${chat.session_id}`)).ok()).toBeTruthy()
  await page.reload()
  const after = page.getByTestId('memories-about').locator('.as-mem', { hasText: 'You are vegetarian' })
  await expect(after).toContainText('From a chat')
  await expect(after.getByRole('button', { name: 'From a chat' })).toHaveCount(0)
})

test('Add memory: four kinds with examples, about you by default', async ({ page }) => {
  await page.goto('/settings/ambrose/memory')
  await page.getByRole('button', { name: 'Add memory' }).first().click()
  const dlg = page.getByRole('dialog', { name: 'Add memory' })
  await expect(dlg.getByLabel('Kind').locator('option')).toHaveText(['Fact', 'Preference', 'Routine', 'Commitment'])
  await expect(dlg.getByTestId('kind-example')).toHaveText('Example: Works at Northwind')
  await dlg.getByLabel('Kind').selectOption('routine')
  await expect(dlg.getByTestId('kind-example')).toHaveText('Example: Teaches Tuesday evenings')
  await expect(dlg.getByLabel('About')).toHaveValue('')
  await dlg.getByLabel('What should Ambrose remember?').fill('You swim on Saturday mornings')
  await dlg.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByTestId('memories-about')).toContainText('You swim on Saturday mornings')
  // The About you page shows the same notes.
  await page.goto('/settings/ambrose/about#about-memories')
  await expect(page.getByTestId('about-you-memories')).toContainText('You swim on Saturday mornings')
})

test('the old People & memories page opens Memory', async ({ page }) => {
  await page.goto('/settings/ambrose/people')
  await expect(page).toHaveURL(/\/settings\/ambrose\/memory#memory-people$/)
})

test('one person card: contact details and What Ambrose knows, from People and from Memory', async ({ page }) => {
  await page.goto('/settings/mail/people?person=priya@example.org')
  const card = page.getByTestId('person-card')
  const knows = card.getByTestId('person-knows')
  await expect(knows).toContainText('Priya runs the reading group')
  // Add a note from the card: it belongs to Priya.
  await knows.getByRole('button', { name: 'Add memory' }).click()
  const dlg = page.getByRole('dialog', { name: 'Add memory' })
  await expect(dlg.getByLabel('About')).toBeDisabled()
  await dlg.getByLabel('What should Ambrose remember?').fill('Priya is on leave in December')
  await dlg.getByRole('button', { name: 'Save' }).click()
  await expect(knows).toContainText('Priya is on leave in December')
  // A commitment becomes a task.
  const made = page.waitForResponse((r) => r.url().endsWith('/api/v1/tasks') && r.request().method() === 'POST')
  await knows.locator('.as-mem', { hasText: 'chapter notes' }).getByRole('button', { name: 'Create task' }).click()
  const body = (await made).request().postDataJSON() as { summary: string }
  expect(body.summary).toBe('Promised Priya the chapter notes by Friday')

  // A person without mail is in the same list.
  await page.getByLabel('Search people').fill('Jordan')
  await expect(page.getByTestId('no-mail')).toBeVisible()

  // The Memory page opens the same card.
  await page.goto('/settings/ambrose/memory')
  await page.locator('.as-mem-group', { hasText: 'Priya Natarajan' }).getByRole('button', { name: 'Open card' }).click()
  await expect(page.getByRole('dialog').getByTestId('person-knows')).toContainText('Priya is on leave in December')
  await page.keyboard.press('Escape')
  // A topic's card: name, kind, domains and its notes.
  await page.locator('.as-mem-group', { hasText: 'Northwind' }).getByRole('button', { name: 'Open card' }).click()
  const topic = page.getByRole('dialog', { name: 'Northwind' })
  await expect(topic.getByLabel('Kind').locator('option')).toHaveText(['Organisation', 'Project', 'Topic', 'Place'])
  await expect(topic.getByLabel('Domains')).toHaveValue('northwind.example')
})

/** POST /agent/chat answers with a stream the test feeds (as in agent-present.spec.ts). */
async function controlledChat(page: Page) {
  await page.addInitScript(() => {
    const real = window.fetch.bind(window)
    let ctrl: ReadableStreamDefaultController<Uint8Array> | null = null
    let seq = 0
    const enc = new TextEncoder()
    window.__chat = {
      bodies: [],
      streams: 0,
      push(event, data) {
        seq += 1
        ctrl?.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify({ ...(data as object), seq })}\n\n`))
      },
      end() {
        ctrl?.close()
        ctrl = null
      },
    }
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.includes('/api/v1/agent/chat') && init?.method === 'POST' && !url.includes('/stop')) {
        window.__chat.streams += 1
        const body = new ReadableStream<Uint8Array>({ start: (c) => { ctrl = c } })
        return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
      }
      return real(input, init)
    }
  })
}

test('Used memories under an answer, and the card of a person Ambrose looked up', async ({ page, seeded }) => {
  const mem = (seeded.extras as unknown as { memory: { memories: Record<string, number> } }).memory.memories
  await controlledChat(page)
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await page.getByLabel('Message Ambrose').fill('When is Priya free?')
  await page.getByLabel('Message Ambrose').press('Enter')
  await expect.poll(() => page.evaluate(() => window.__chat.streams)).toBeGreaterThan(0)
  const push = (e: string, d: unknown) => page.evaluate(([x, y]) => window.__chat.push(x as string, y), [e, d] as const)
  await push('session', { session_id: 'e2e-mem', user_message_id: 1, memory_ids: [mem['priya-1'], mem['me-1']] })
  await push('tool_start', { id: 'a', name: 'get_person', label: 'Looking up priya@example.org', icon: 'person', args: { email: 'priya@example.org' }, segment: 0 })
  await push('tool', { id: 'a', name: 'get_person', label: 'Looking up priya@example.org', args: { email: 'priya@example.org' }, ok: true, summary: 'found', segment: 0 })
  await push('final', { segment: 1, content: 'Priya runs the reading group on Tuesdays, when you teach. Try Wednesday.', citations: [] })
  await push('done', {})
  await page.evaluate(() => window.__chat.end())

  const used = page.getByTestId('used-memories')
  await expect(used).toContainText('Used memories · 2')
  await used.getByRole('button', { name: /Used memories/ }).click()
  await expect(used.locator('.used-memory')).toHaveText([/Priya runs the reading group/, /You teach on Tuesday evenings/])
  await used.locator('.used-memory').first().click()
  await expect(page.getByRole('dialog', { name: 'Edit memory' }).getByLabel('What should Ambrose remember?')).toHaveValue('Priya runs the reading group')
  await page.getByRole('dialog', { name: 'Edit memory' }).getByRole('button', { name: 'Cancel' }).click()

  await page.getByTestId('open-person-card').click()
  await expect(page.getByRole('dialog').getByTestId('person-knows')).toContainText('Priya runs the reading group')
})
