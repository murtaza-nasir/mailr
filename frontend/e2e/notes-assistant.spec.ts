// ACT-SURFACES: web.note web.panel
// The assistant on a note: Ask assistant in the
// note editor attaches the note (ACT agent.ask_about), the turn carries the Notes screen context by id, and
// the answer's note citation, its "Open" card and the Undo of the change work. The model is faked here (a
// canned event stream); the server side of the turn is covered by backend/tests/test_notes_assistant.py.
import type { APIRequestContext, Page, Request } from '@playwright/test'
import { expect, test } from './fixtures'
import { mkNote } from './helpers'

interface Item { id: string; text: string }
interface Note { id: string; title: string; version: number; items?: Item[] }

const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkNote<Note>(request, body)
async function get(request: APIRequestContext, id: string): Promise<Note> {
  return (await (await request.get(`/api/v1/notes/${id}`)).json()) as Note
}

const sse = (events: [string, unknown][]) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('')

/** Answers the chat with `events` and keeps the request bodies. */
async function fakeChat(page: Page, events: () => [string, unknown][]): Promise<Record<string, unknown>[]> {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    bodies.push((route.request() as Request).postDataJSON() as Record<string, unknown>)
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse(events()) })
  })
  await page.route('**/api/v1/agent/sessions/e2e-notes', (route) => route.fulfill({ json: { id: 'e2e-notes', turns: [] } }))
  return bodies
}

test('Ask assistant on a list: the note goes with the turn; the answer cites it, opens it and undoes its change', async ({ page }) => {
  const list = await mk(page.request, { kind: 'checklist', title: 'E2E Shopping', shopping: true, lines: 'bread' })
  // The change the canned answer reports, made for real so its undo is real (the assistant's own writes
  // return the same undo).
  const added = await page.request.post(`/api/v1/notes/${list.id}/items`, { data: { lines: 'milk' } })
  const { items, undo } = (await added.json()) as { items: Item[]; undo: Record<string, unknown> }
  const milk = items[0]
  const bodies = await fakeChat(page, () => [
    ['session', { session_id: 'e2e-notes', title: 'Shopping' }],
    ['config', { title: 'Added milk', undo }],
    ['ref', { kind: 'note', id: list.id, version: 3, title: 'E2E Shopping' }],
    ['final', {
      content: 'Added milk to your list [1](mailr-cite:1).',
      citations: [{ n: 1, kind: 'note', note_id: list.id, item_id: milk.id, note_kind: 'checklist', title: 'E2E Shopping',
        subject: 'E2E Shopping', from: 'List', snippet: 'milk', checked: false, conversation_id: null, message_id: null,
        quote: null, verified: true, start: null, end: null, date: null, url: null }],
    }],
    ['done', {}],
  ])

  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  await page.goto(`/notes/n/${list.id}`)
  const editor = page.getByTestId('note-editor')
  await expect(editor).toBeVisible()
  // ACT agent.ask_about @web.note: the capture opens with the list as its chip; Send goes to the chat.
  await editor.getByRole('button', { name: 'Ask Ambrose' }).click()
  const ask = page.getByTestId('ask-capture')
  await expect(ask.getByTestId('ask-chip')).toContainText('E2E Shopping')
  await ask.getByRole('textbox', { name: 'Your words for Ambrose' }).fill('Add milk')
  await ask.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByTestId('chat-about')).toContainText('E2E Shopping')
  await expect.poll(() => bodies.length).toBe(1)
  const ctx = bodies[0].context as Record<string, unknown>
  expect(ctx.focused_note).toEqual({ id: list.id })
  // The Notes screen context: ids only.
  expect((ctx.ui as Record<string, unknown>).notes).toMatchObject({ view: 'all', focused: list.id })

  await expect(page.getByText('Added milk to your list')).toBeVisible()
  // The change's Undo takes the item back off the list.
  const change = page.locator('li').filter({ hasText: 'Added milk' })
  await change.getByRole('button', { name: 'Undo' }).click()
  await expect(change).toContainText('Undone: Added milk')
  await expect.poll(async () => (await get(page.request, list.id)).items?.map((i) => i.text)).toEqual(['bread'])
  // The card of the note the turn changed opens it.
  await page.getByTestId('open-note-ref').click()
  await expect(page).toHaveURL(new RegExp(`/notes/n/${list.id}$`))
  // The citation chip opens the list at the item.
  const chip = page.getByTestId('cite-note')
  await expect(chip).toHaveAttribute('aria-label', /List 1: E2E Shopping, item milk/)
  await chip.click()
  await expect(page).toHaveURL(new RegExp(`/notes/n/${list.id}#i-${milk.id}$`))
})

test('Shift+A in the open note attaches it to the ask capture', async ({ page }) => {
  const note = await mk(page.request, { kind: 'text', title: 'E2E Trip plan', body: 'Book the train' })
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  await page.goto(`/notes/n/${note.id}`)
  const editor = page.getByTestId('note-editor')
  await expect(editor).toBeVisible()
  // Out of the text fields (keys typed there are text), on the editor's toolbar.
  await editor.getByRole('button', { name: 'Version history' }).focus()
  await page.keyboard.press('Shift+A')
  await expect(page.getByTestId('ask-capture').getByTestId('ask-chip')).toContainText('E2E Trip plan')
})
