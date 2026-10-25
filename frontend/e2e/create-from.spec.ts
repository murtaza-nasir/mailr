// ACT-SURFACES: web.reader web.list web.note
// Create from email: one menu in the reader's toolbar (Shift+T) and
// in the row menu, with Task, Event, Meeting with these people, Note, Checklist and Link to a note or
// table. It replaces the Notes button, More → Add to, and the header's calendar button.
// Covers: mail.to_task, mail.to_event, mail.schedule_meeting, note.from_email, note.link and
// table.link from the row menu; the reader's own cells are in reader-header.spec.ts,
// calendar-integration-mail.spec.ts and submenus.spec.ts. On a note, Create from note offers Task,
// Event and Email (note.to_task, note.to_event, note.to_email). Screenshots light and dark.
import os from 'node:os'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-create-from-shots')
const ITEMS = ['Task', 'Event', 'Meeting with these people', 'Note', 'Checklist', 'Link to a note or table…']

const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()
const rowMenu = (page: Page) => page.getByRole('menu', { name: 'Conversation actions' })
const sub = (page: Page) => page.getByRole('menu', { name: 'Create from email', exact: true })

async function linkedNotes(request: APIRequestContext, cid: number) {
  const r = await request.get('/api/v1/notes/linked', { params: { target_kind: 'conversation', conversation_id: cid } })
  return ((await r.json()) as { notes: { id: string; title: string; kind: string }[] }).notes
}

async function fromRow(page: Page, subject: string, item: string) {
  const r = row(page, subject)
  await r.click({ button: 'right', position: { x: 200, y: 10 } })
  await expect(rowMenu(page)).toBeVisible()
  const t = rowMenu(page).getByRole('menuitem', { name: 'Create from email' })
  const b = (await t.boundingBox())!
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 3 })
  await sub(page).getByRole('menuitem', { name: item, exact: true }).click()
}

test('the reader: one Create from email menu, opened by Shift+T; no Notes button or header calendar button', async ({ page, seeded }) => {
  const cid = seeded.conversations['E2E Read me']
  await page.goto(`/mail/inbox/${cid}`)
  await expect(page.getByRole('heading', { name: /E2E Read me/ })).toBeVisible()
  const head = page.locator('.conv-view-head')
  await expect(head.getByRole('button', { name: 'Notes' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Schedule a meeting' })).toHaveCount(0)
  await page.locator('.conv-scroll').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('Shift+T')
  const menu = page.getByRole('menu', { name: 'Create from email' })
  for (const name of ITEMS) await expect(menu.getByRole('menuitem', { name, exact: true })).toBeVisible()
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(150)
    await page.screenshot({ path: path.join(SHOTS, `reader-create-${scheme}.png`) })
  }
  // More no longer has Add to.
  await page.keyboard.press('Escape')
  await head.getByRole('button', { name: 'More' }).click()
  await expect(page.getByRole('menuitem', { name: 'Add to' })).toHaveCount(0)
})

test('the row menu: Task and Event (mail.to_task, mail.to_event)', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Reply to me')).toBeVisible()
  await fromRow(page, 'E2E Reply to me', 'Task')
  await expect(page.getByRole('dialog', { name: 'Add to tasks' })).toBeVisible()
  await page.keyboard.press('Escape')
  await fromRow(page, 'E2E Reply to me', 'Event')
  await expect(page.getByTestId('cal-editor')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('cal-editor')).toHaveCount(0)
})

test('the row menu: Checklist and Note make linked notes; Link to a note or table links one (note.from_email, note.link, table.link)', async ({ page, seeded }) => {
  const cid = seeded.conversations['E2E Read me']
  const r = await page.request.post('/api/v1/notes', { data: { kind: 'text', title: 'E2E Row link note' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Read me')).toBeVisible()
  await fromRow(page, 'E2E Read me', 'Checklist')
  const t = page.locator('.toast').filter({ hasText: 'Checklist created' })
  await expect(t.getByRole('button', { name: 'Open' })).toBeVisible()
  await expect(t.getByRole('button', { name: 'Undo' })).toBeVisible()
  await expect.poll(async () => (await linkedNotes(page.request, cid)).map((n) => n.kind)).toEqual(['checklist'])

  await fromRow(page, 'E2E Read me', 'Link to a note or table…')
  const picker = page.getByTestId('note-link-picker')
  await picker.getByRole('searchbox').fill('E2E Row link note')
  await picker.getByRole('option').filter({ hasText: 'E2E Row link note' }).click()
  await expect.poll(async () => (await linkedNotes(page.request, cid)).map((n) => n.title)).toContain('E2E Row link note')

  await fromRow(page, 'E2E Read me', 'Note')
  await expect(page.getByTestId('note-editor')).toBeVisible()
  await expect.poll(async () => (await linkedNotes(page.request, cid)).length).toBe(3)
})

// The note picker: the pointer only moves the highlight; Enter links the highlighted row. Rows that
// appear under a resting pointer leave the highlight where the keys put it.
test('Link to a note or table: a resting pointer does not move the highlight; Enter links the highlighted note (note.link)', async ({ page, seeded }) => {
  const cid = seeded.conversations['E2E Read me']
  for (const title of ['E2E Pick one', 'E2E Pick two', 'E2E Pick three']) {
    const r = await page.request.post('/api/v1/notes', { data: { kind: 'text', title } })
    expect(r.ok(), await r.text()).toBeTruthy()
  }
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Read me')).toBeVisible()
  await fromRow(page, 'E2E Read me', 'Link to a note or table…')
  const picker = page.getByTestId('note-link-picker')
  const box = picker.getByRole('searchbox')
  const options = picker.getByRole('option')
  await box.fill('E2E Pick')
  await expect(options).toHaveCount(3)
  const third = (await options.nth(2).boundingBox())!
  await box.fill('')
  await expect(options).toHaveCount(0)
  await page.mouse.move(third.x + third.width / 2, third.y + third.height / 2)
  await box.fill('E2E Pick')
  await expect(options).toHaveCount(3)
  await page.waitForTimeout(300) // a frame or two for any hover update the browser makes
  await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true')
  await box.press('ArrowDown')
  const second = (await options.nth(1).textContent())!.trim()
  await box.press('Enter')
  await expect.poll(async () => (await linkedNotes(page.request, cid)).map((n) => n.title)).toEqual([second])
})

test('the row menu: Meeting with these people opens the meeting step (mail.schedule_meeting)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Reply to me')).toBeVisible()
  await fromRow(page, 'E2E Reply to me', 'Meeting with these people')
  const step = page.getByRole('dialog', { name: 'Meeting with these people' })
  await expect(step.getByLabel('Title')).toHaveValue('E2E Reply to me')
  await expect(step.getByRole('checkbox', { name: /Dana Tester/ })).toBeChecked()
  await step.getByRole('button', { name: 'Find a time' }).click()
  await expect(page.getByTestId('cal-editor').getByTestId('cal-findtime')).toBeVisible()
})

test('a note: Create from note makes a linked task, opens the event editor, and writes an email (note.to_task, note.to_event, note.to_email)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const r = await page.request.post('/api/v1/notes', { data: { kind: 'text', title: 'E2E Plumber', body: 'Call about the leak under the sink' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const id = ((await r.json()) as { note: { id: string } }).note.id
  await page.goto(`/notes/n/${id}`)
  const editor = page.getByTestId('note-editor')
  await expect(editor).toBeVisible()
  const create = editor.getByRole('button', { name: 'Create from note' })
  await create.click()
  const menu = page.getByRole('menu', { name: 'Create from note' })
  for (const name of ['Task', 'Event', 'Email']) await expect(menu.getByRole('menuitem', { name, exact: true })).toBeVisible()
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(150)
    await page.screenshot({ path: path.join(SHOTS, `note-create-${scheme}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
  await menu.getByRole('menuitem', { name: 'Task', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Task from this note' })
  await expect(dialog.getByLabel('Title')).toHaveValue('E2E Plumber')
  await dialog.getByRole('button', { name: 'Add task' }).click()
  const t = page.locator('.toast').filter({ hasText: 'Task created from the note' })
  await expect(t.getByRole('button', { name: 'Open' })).toBeVisible()
  await expect(t.getByRole('button', { name: 'Undo' })).toBeVisible()
  await expect.poll(async () => {
    const j = await (await page.request.get(`/api/v1/notes/${id}`)).json() as { links?: { target_kind: string }[]; note?: { links?: { target_kind: string }[] } }
    return ((j.note ?? j).links ?? []).map((l) => l.target_kind)
  }).toContain('task')

  await create.click()
  await page.getByRole('menu', { name: 'Create from note' }).getByRole('menuitem', { name: 'Event', exact: true }).click()
  await expect(page.getByTestId('cal-editor')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('cal-editor')).toHaveCount(0)

  await create.click()
  await page.getByRole('menu', { name: 'Create from note' }).getByRole('menuitem', { name: 'Email', exact: true }).click()
  await expect(page.getByPlaceholder('Subject')).toHaveValue('E2E Plumber')
  await expect(page.locator('.ProseMirror').last()).toContainText('Call about the leak under the sink')
})
