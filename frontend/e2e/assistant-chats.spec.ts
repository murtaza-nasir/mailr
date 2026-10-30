// ACT-SURFACES: web.panel
// Chats in the Ambrose panel (features/rail/assistant/ChatList.tsx): the History button lists earlier
// chats (ACT agent.chat_list), a row replays its chat, which then goes on (ACT agent.chat_open), a turn
// that still runs is followed live, and Delete waits for its Undo toast (ACT agent.chat_delete). The
// server's chat routes are faked here, so each state (empty, failed, refused) is the test's to choose.
// The phone layout: e2e/phone/assistant-chats-phone.spec.ts.
import fs from 'node:fs'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const SHOTS = process.env.CHATS_SHOTS
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true })

const sse = (events: [string, unknown][]) => events.map(([name, data], i) => `event: ${name}\ndata: ${JSON.stringify({ ...(data as object), seq: i + 1 })}\n\n`).join('')

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()

const CHATS = [
  { id: 'e2e-c1', title: 'What did Dana say about the Lumen kickoff?', updated_at: ago(12), preview: 'Dana wants the kickoff on Monday at 10, with the design review moved to Thursday.' },
  { id: 'e2e-c2', title: 'Draft a reply to the landlord about the lease', updated_at: ago(60 * 26), preview: 'Here is a draft that asks for the renewal terms in writing.' },
  { id: 'e2e-c3', title: 'Which invoices are still unpaid?', updated_at: ago(60 * 24 * 9), preview: null },
]

const REPLAY = {
  id: 'e2e-c1',
  running: false,
  turns: [
    { role: 'user', id: 11, content: 'What did Dana say about the Lumen kickoff?', citations: [], steps: [] },
    {
      role: 'assistant', content: 'Dana wants the kickoff on Monday at 10 [1](mailr-cite:1).', status: 'done', user_message_id: 11, memory_ids: [7],
      citations: [{ n: 1, kind: 'message', conversation_id: 1, message_id: null, subject: 'Lumen kickoff', from: 'Dana Whitfield', date: null, quote: null, verified: true }],
      steps: [{ id: 's1', name: 'search_conversations', label: 'Searching your mail for “Lumen”', status: 'done', summary: '3 conversations' }],
    },
  ],
}

interface Fake {
  list: unknown[] | 'fail'
  deletes: string[]
  refuseDelete?: boolean
  replay?: Record<string, unknown>
}

/** Fakes GET /agent/sessions, GET and DELETE /agent/sessions/{id}. */
async function fakeChats(page: Page, f: Fake) {
  await page.route(/\/api\/v1\/agent\/sessions(\?.*)?$/, (route) =>
    f.list === 'fail' ? route.fulfill({ status: 500, json: { detail: 'boom' } }) : route.fulfill({ json: f.list }),
  )
  await page.route(/\/api\/v1\/agent\/sessions\/[^/?]+$/, async (route) => {
    const id = decodeURIComponent(route.request().url().split('/').pop()!)
    if (route.request().method() === 'DELETE') {
      if (f.refuseDelete) return route.fulfill({ status: 500, json: { detail: 'no' } })
      f.deletes.push(id)
      return route.fulfill({ json: { ok: true } })
    }
    return route.fulfill({ json: f.replay && f.replay.id === id ? f.replay : { id, turns: [] } })
  })
}

async function openAmbrose(page: Page, theme = 'classic') {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await expect(page.locator('.side-panel .chat')).toBeVisible()
}

const chatsButton = (page: Page) => page.locator('.side-panel-head').getByRole('button', { name: 'Chats' })
const rows = (page: Page) => page.getByTestId('chats').locator('[data-chat-row]')

test('Chats lists earlier chats; a row replays its chat, which goes on from there', async ({ page }) => {
  const f: Fake = { list: CHATS, deletes: [], replay: REPLAY }
  await fakeChats(page, f)
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    bodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse([
      ['session', { session_id: 'e2e-c1', user_message_id: 12 }],
      ['final', { content: 'The design review is on Thursday.', citations: [] }],
      ['done', {}],
    ]) })
  })
  await openAmbrose(page)
  await chatsButton(page).click()
  await expect(chatsButton(page)).toHaveAttribute('aria-pressed', 'true')
  const list = page.getByTestId('chats')
  await expect(list.getByRole('heading', { name: 'Chats' })).toBeVisible()
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).nth(0)).toContainText('What did Dana say about the Lumen kickoff?')
  await expect(rows(page).nth(0)).toContainText('Dana wants the kickoff on Monday at 10')
  // The chat is hidden behind the list, not gone.
  await expect(page.locator('.side-panel .chat')).toBeHidden()
  // No chat is open yet: nothing is marked.
  await expect(list.getByText('Open now')).toHaveCount(0)
  if (SHOTS) await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-light-classic.png` })

  // Keyboard: the first row has the focus; Down, Up, Enter opens.
  await expect(rows(page).nth(0)).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(rows(page).nth(1)).toBeFocused()
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('Enter')

  await expect(list).toHaveCount(0)
  const chat = page.locator('.side-panel .chat')
  await expect(chat.locator('.chat-msg.user')).toContainText('What did Dana say about the Lumen kickoff?')
  const answer = chat.locator('.chat-turn').last()
  await expect(answer).toContainText('Dana wants the kickoff on Monday at 10')
  await expect(answer.locator('.cite-chip').first()).toBeVisible() // its source
  await expect(answer.getByTestId('used-memories')).toContainText('1') // Used memories
  await expect(page.getByLabel('Message Ambrose')).toBeFocused()

  // It goes on: the next message continues that chat.
  await page.getByLabel('Message Ambrose').fill('And the design review?')
  await page.getByLabel('Message Ambrose').press('Enter')
  await expect(chat.locator('.chat-turn').last()).toContainText('The design review is on Thursday.')
  expect(bodies[0].session_id).toBe('e2e-c1')

  // Back in the list, the open chat says so; Esc goes back to the chat.
  await chatsButton(page).click()
  const current = list.locator('.chats-item.current')
  await expect(current).toContainText('Open now')
  await expect(current.locator('[data-chat-row]')).toHaveAttribute('aria-current', 'true')
  await page.keyboard.press('Escape')
  await expect(list).toHaveCount(0)
  await expect(chat.locator('.chat-turn').last()).toContainText('The design review is on Thursday.')

  // New chat from the list: an empty chat.
  await chatsButton(page).click()
  await page.locator('.side-panel-head').getByRole('button', { name: 'New chat' }).click()
  await expect(list).toHaveCount(0)
  await expect(chat.locator('.chat-turn')).toHaveCount(0)
})

test('a chat whose turn still runs is followed live', async ({ page }) => {
  const running = { id: 'e2e-c2', running: true, turns: [
    { role: 'user', id: 21, content: 'Draft a reply to the landlord about the lease', citations: [], steps: [] },
    { role: 'assistant', content: '', status: 'running', user_message_id: 21, citations: [], steps: [] },
  ] }
  await fakeChats(page, { list: CHATS, deletes: [], replay: running })
  let streams = 0
  await page.route(/\/api\/v1\/agent\/chat\/e2e-c2\/stream/, async (route) => {
    streams += 1
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse([
      ['token', { text: 'Here is a draft', segment: 0 }],
      ['final', { content: 'Here is a draft that asks for the renewal terms in writing.', citations: [] }],
      ['done', {}],
    ]) })
  })
  await openAmbrose(page)
  await chatsButton(page).click()
  await rows(page).nth(1).click()
  const answer = page.locator('.side-panel .chat .chat-turn').last()
  await expect(answer).toContainText('Here is a draft that asks for the renewal terms in writing.')
  expect(streams).toBe(1)
})

test('Delete hides the row at once; Undo brings it back and nothing is sent', async ({ page }) => {
  const f: Fake = { list: CHATS, deletes: [] }
  await fakeChats(page, f)
  await openAmbrose(page)
  await chatsButton(page).click()
  await expect(rows(page)).toHaveCount(3)
  const second = page.getByTestId('chats').locator('.chats-item').nth(1)
  await second.hover()
  await second.getByRole('button', { name: 'More options for Draft a reply to the landlord about the lease' }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await expect(rows(page)).toHaveCount(2)
  const toastEl = page.locator('.toast', { hasText: 'Chat deleted.' })
  await expect(toastEl).toBeVisible()
  await toastEl.getByRole('button', { name: 'Undo' }).click()
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).nth(1)).toContainText('Draft a reply to the landlord')
  await page.waitForTimeout(300)
  expect(f.deletes).toEqual([])

  // Delete again (the Delete key on a row), then leave the list: the delete is sent then.
  await rows(page).nth(1).focus()
  await page.keyboard.press('Delete')
  await expect(rows(page)).toHaveCount(2)
  await expect(rows(page).nth(1)).toBeFocused() // the next row takes the focus
  await page.getByTestId('chats-back').click()
  await expect.poll(() => f.deletes).toEqual(['e2e-c2'])
  await expect(page.locator('.toast', { hasText: 'Chat deleted.' })).toHaveCount(0)
})

test('a delete the server refuses brings the row back and says so', async ({ page }) => {
  const f: Fake = { list: CHATS, deletes: [], refuseDelete: true }
  await fakeChats(page, f)
  await openAmbrose(page)
  await chatsButton(page).click()
  const first = page.getByTestId('chats').locator('.chats-item').first()
  await first.hover()
  await first.getByTestId('chat-more').click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await expect(rows(page)).toHaveCount(2)
  // The toast closes (here: Dismiss), the delete goes and is refused.
  await page.locator('.toast', { hasText: 'Chat deleted.' }).getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.locator('.toast', { hasText: "Couldn't delete. Check the connection." })).toBeVisible()
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).first()).toContainText('What did Dana say')
})

test('Chats: failed with Retry, empty, and offline', async ({ page, context }) => {
  const f: Fake = { list: 'fail', deletes: [] }
  await fakeChats(page, f)
  await openAmbrose(page)
  await chatsButton(page).click()
  const failed = page.getByTestId('chats-failed')
  await expect(failed).toContainText("Couldn't load chats.")
  if (SHOTS) await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-failed.png` })
  f.list = []
  await failed.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByTestId('chats-empty')).toContainText('No chats yet')
  await expect(page.getByTestId('chats-empty')).toContainText('Your chats with Ambrose show here.')
  if (SHOTS) await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-empty.png` })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('chats')).toHaveCount(0)

  await context.setOffline(true)
  await chatsButton(page).click()
  await expect(page.getByTestId('chats-offline')).toContainText("You're offline")
  await expect(page.getByTestId('chats-offline')).toContainText("Chats show when you're connected.")
  if (SHOTS) await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-offline.png` })
  await context.setOffline(false)
})

test('a chat that cannot be read stays closed, and the open chat stays as it was', async ({ page }) => {
  await fakeChats(page, { list: CHATS, deletes: [] })
  await page.route(/\/api\/v1\/agent\/sessions\/e2e-c3$/, (route) => route.fulfill({ status: 500, json: { detail: 'no' } }))
  await openAmbrose(page)
  await chatsButton(page).click()
  await rows(page).nth(2).click()
  await expect(page.locator('.toast', { hasText: "Couldn't open the chat. Check the connection." })).toBeVisible()
  await expect(page.getByTestId('chats')).toBeVisible()
})

for (const [scheme, theme] of [['dark', 'classic'], ['light', 'cards']] as const) {
  test(`Chats screenshot: ${theme}, ${scheme}`, async ({ page }) => {
    test.skip(!SHOTS, 'CHATS_SHOTS not set')
    await page.emulateMedia({ colorScheme: scheme })
    await fakeChats(page, { list: CHATS, deletes: [], replay: REPLAY })
    await openAmbrose(page, theme)
    await chatsButton(page).click()
    await rows(page).first().click()
    await chatsButton(page).click()
    await expect(page.getByTestId('chats').locator('.chats-item.current')).toBeVisible()
    await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-${scheme}-${theme}.png` })
  })
}

// A list request that started before a rename and is answered after it (a search cleared right after
// Enter) carries the old title; it must not put that title back (seen on a slow runner, 27 Oct 2026).
test('a list answer from before a rename does not undo the rename', async ({ page }) => {
  let title = CHATS[0].title
  let hold = false
  let gotList: () => void = () => {}
  const listArrived = new Promise<void>((r) => { gotList = r })
  let patched: () => void = () => {}
  const patchDone = new Promise<void>((r) => { patched = r })
  await page.route(/\/api\/v1\/agent\/sessions(\?.*)?$/, async (route) => {
    const snapshot = CHATS.map((c, i) => (i === 0 ? { ...c, title } : c))
    // Only the full list (the search cleared) is held; a search answer goes at once.
    if (hold && !route.request().url().includes('q=')) {
      gotList()
      await patchDone
      await new Promise((r) => setTimeout(r, 300))
    }
    try { await route.fulfill({ json: snapshot }) } catch { /* the app cancelled it */ }
  })
  await page.route(/\/api\/v1\/agent\/sessions\/e2e-c1$/, async (route) => {
    if (route.request().method() !== 'PATCH') return route.fulfill({ json: { id: 'e2e-c1', turns: [] } })
    // Answered only after a list request has started: that request carries the old title.
    await Promise.race([listArrived, new Promise((r) => setTimeout(r, 3000))])
    const previous = { title, renamed: false }
    title = (route.request().postDataJSON() as { title: string }).title
    await route.fulfill({ json: { id: 'e2e-c1', title, renamed: true, previous } })
    hold = false
    patched()
  })
  await openAmbrose(page)
  await chatsButton(page).click()
  await expect(rows(page)).toHaveCount(3)
  // As in the phone test: rename from the search results, then clear the search at once.
  const box = page.getByRole('searchbox', { name: 'Search chats' })
  await box.fill('Lumen')
  const first = page.getByTestId('chats').locator('.chats-item').first()
  await first.hover()
  await first.getByTestId('chat-more').click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await page.getByRole('textbox', { name: 'Chat title' }).fill('Lumen kickoff notes')
  hold = true
  await page.getByRole('textbox', { name: 'Chat title' }).press('Enter')
  await box.fill('')
  await expect(page.locator('.toast', { hasText: 'Chat renamed.' })).toBeVisible()
  await page.waitForTimeout(800)
  await expect(rows(page).first()).toContainText('Lumen kickoff notes')
})
