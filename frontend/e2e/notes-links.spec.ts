// ACT-SURFACES: web.note web.calendar web.pane web.reader
// Links between notes, tables and the rest of mailr: the note editor's
// search picker (ACT note.link from the note), the links of an email (chips on the subject line), and
// the links panels of an event and a task
// (ACT note.link, ACT note.unlink, ACT table.link, ACT table.unlink), and ACT checkitem.to_task's
// round trip: the task pane names the list it came from. Each test checks the result through the API.
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'

interface Note { id: string; title: string; kind: string; items?: { id: string; text: string; task_uid?: string | null }[]; links?: { id: number; target_kind: string; target_ref: Record<string, unknown> }[] }

async function mkNote(request: APIRequestContext, body: Record<string, unknown>): Promise<Note> {
  const r = await request.post('/api/v1/notes', { data: body })
  expect(r.ok(), await r.text()).toBeTruthy()
  return ((await r.json()) as { note: Note }).note
}
async function getNote(request: APIRequestContext, id: string): Promise<Note> {
  return (await (await request.get(`/api/v1/notes/${id}`)).json()) as Note
}
async function mkTable(request: APIRequestContext, name: string): Promise<{ id: string }> {
  const r = await request.post('/api/v1/tables', { data: { name } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return ((await r.json()) as { table: { id: string } }).table
}
async function linked(request: APIRequestContext, kind: string, ref: string) {
  const n = (await (await request.get('/api/v1/notes/linked', { params: { target_kind: kind, ref } })).json()) as { notes: { id: string; item_id: string | null }[] }
  const t = (await (await request.get('/api/v1/tables/linked', { params: { target_kind: kind, ref } })).json()) as { tables: { id: string }[] }
  return { notes: n.notes, tables: t.tables }
}
const panel = (page: Page) => page.getByTestId('linked-panel')

test('the note editor links an email found by its subject', async ({ page }) => {
  const n = await mkNote(page.request, { kind: 'text', title: 'E2E Link from search' })
  await page.goto(`/notes/n/${n.id}`)
  const editor = page.getByTestId('note-editor')
  await expect(editor).toBeVisible()
  // ACT note.link @web.note: the picker searches mail, events, tasks and recordings.
  await editor.getByRole('button', { name: 'Link', exact: true }).click()
  const picker = page.getByTestId('link-picker')
  await picker.getByRole('combobox').fill('E2E Read me')
  await expect(picker.getByRole('option').filter({ hasText: 'E2E Read me' }).first()).toBeVisible()
  await picker.getByRole('combobox').press('Enter')
  await expect(editor.locator('.nt-link-chip')).toHaveCount(1)
  const got = await getNote(page.request, n.id)
  expect(got.links?.[0]?.target_kind).toBe('conversation')
  // The email's links panel lists the note (web.reader).
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Read me' }).first().click()
  await expect(panel(page).getByTestId('linked-note')).toContainText('E2E Link from search')
})

// The pointer only moves the highlight; a click or Enter links. Results that appear under a resting
// pointer leave the highlight where the keys put it (it once followed the row under the pointer, and
// Enter then linked that row).
test('the link picker links the highlighted row; a resting pointer does not move the highlight', async ({ page }) => {
  const n = await mkNote(page.request, { kind: 'text', title: 'E2E Link by keys' })
  await page.goto(`/notes/n/${n.id}`)
  const editor = page.getByTestId('note-editor')
  await editor.getByRole('button', { name: 'Link', exact: true }).click()
  const picker = page.getByTestId('link-picker')
  const box = picker.getByRole('combobox')
  const options = picker.getByRole('option')
  await box.fill('E2E')
  await expect(options.nth(2)).toBeVisible()
  // Rest the pointer where the third result shows, then let a new search draw the rows under it.
  const third = (await options.nth(2).boundingBox())!
  await box.fill('')
  await expect(options).toHaveCount(0)
  await page.mouse.move(third.x + third.width / 2, third.y + third.height / 2)
  await box.fill('E2E')
  await expect(options.nth(2)).toBeVisible()
  await page.waitForTimeout(300) // a frame or two for any hover update the browser makes
  await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true')
  // ArrowDown moves the highlight to the second row, and Enter links that row.
  await box.press('ArrowDown')
  await expect(options.nth(1)).toHaveAttribute('aria-selected', 'true')
  const second = (await options.nth(1).locator('.nt-link-opt-title').textContent())!
  await box.press('Enter')
  await expect(editor.locator('.nt-link-chip')).toHaveCount(1)
  await expect(editor.locator('.nt-link-chip')).toContainText(second)
  // Moving the pointer over a row highlights it and links nothing; a click links it.
  await editor.getByRole('button', { name: 'Link', exact: true }).click()
  await box.fill('E2E')
  await expect(options.nth(2)).toBeVisible()
  const r = (await options.nth(2).boundingBox())!
  await page.mouse.move(r.x + 10, r.y + r.height / 2, { steps: 4 })
  await expect(options.nth(2)).toHaveAttribute('aria-selected', 'true')
  await expect(editor.locator('.nt-link-chip')).toHaveCount(1)
  await options.nth(2).click()
  await expect(editor.locator('.nt-link-chip')).toHaveCount(2)
})

test('a task pane links a table and unlinks a note with undo; a list item made a task names its list', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const r = await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Linked task' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const task = (await r.json()) as { uid: string }
  const n = await mkNote(page.request, { kind: 'text', title: 'E2E Task note' })
  expect((await page.request.post(`/api/v1/notes/${n.id}/links`, { data: { target_kind: 'task', target_ref: { uid: task.uid } } })).ok()).toBeTruthy()
  const t = await mkTable(page.request, 'E2E Task table')
  await page.goto(`/tasks/task/${encodeURIComponent(task.uid)}`)
  await expect(page.getByTestId('task-pane')).toBeVisible()
  await expect(panel(page).getByTestId('linked-note')).toContainText('E2E Task note')
  // ACT table.link @web.pane
  await panel(page).getByRole('button', { name: 'Link a note or table' }).click()
  await page.getByTestId('note-link-picker').getByRole('searchbox').fill('E2E Task table')
  await page.getByTestId('note-link-picker').getByRole('option').filter({ hasText: 'E2E Task table' }).click()
  await expect(panel(page).getByTestId('linked-table')).toContainText('E2E Task table')
  expect((await linked(page.request, 'task', task.uid)).tables.map((x) => x.id)).toEqual([t.id])
  // ACT note.unlink @web.pane, with the undo toast.
  await panel(page).getByRole('button', { name: 'Unlink E2E Task note' }).click()
  await expect(panel(page).getByTestId('linked-note')).toHaveCount(0)
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
  await expect(panel(page).getByTestId('linked-note')).toContainText('E2E Task note')
  // ACT table.unlink @web.pane
  await panel(page).getByRole('button', { name: 'Unlink E2E Task table' }).click()
  await expect(panel(page).getByTestId('linked-table')).toHaveCount(0)

  // ACT checkitem.to_task @web.pane: the task made from an item names its list and opens it on the item.
  const list = await mkNote(page.request, { kind: 'checklist', title: 'E2E Garden', lines: 'buy soil' })
  const item = list.items![0]
  const made = await page.request.post(`/api/v1/notes/${list.id}/items/${item.id}/to-task`, { data: {} })
  expect(made.ok(), await made.text()).toBeTruthy()
  const uid = ((await made.json()) as { task_uid: string }).task_uid
  await page.goto(`/tasks/task/${encodeURIComponent(uid)}`)
  await expect(panel(page).getByTestId('linked-list-item')).toContainText('From the list E2E Garden')
  // The Notes field names the list as the link, not the long address back to it.
  const notesField = page.locator('.tk-notes-preview')
  await expect(notesField).toHaveText('From the list E2E Garden')
  await expect(notesField.getByRole('link', { name: 'E2E Garden' })).toBeVisible()
  await expect(notesField).not.toContainText('http')
  await panel(page).getByTestId('linked-list-item').getByRole('button').click()
  await expect(page).toHaveURL(new RegExp(`/notes/n/${list.id}#i-${item.id}`))
})

test('an event detail makes a new linked note', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const start = new Date(Date.now() + 26 * 3600_000)
  start.setMinutes(0, 0, 0)
  const r = await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Planning', start: start.toISOString(), end: new Date(start.getTime() + 3600_000).toISOString() } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const ev = (await r.json()) as { uid: string; occurrence?: string; start?: string }
  await page.goto(`/calendar/event/${encodeURIComponent(ev.uid)}/${encodeURIComponent(ev.occurrence ?? ev.start ?? start.toISOString())}`)
  await expect(page.getByTestId('cal-detail')).toBeVisible()
  // ACT note.link @web.calendar: "New note", the last row of the link search, makes a note named after
  // the event, linked to it. The panel is one button while nothing is linked.
  await expect(panel(page).getByTestId('linked-chips')).toHaveCount(0)
  await panel(page).getByRole('button', { name: 'Link a note or table' }).click()
  await page.getByTestId('note-link-picker').getByRole('button', { name: /New note/ }).click()
  await expect(page.getByTestId('note-editor')).toBeVisible()
  const got = await linked(page.request, 'event', ev.uid)
  expect(got.notes).toHaveLength(1)
  expect((await getNote(page.request, got.notes[0].id)).title).toBe('E2E Planning')
})
