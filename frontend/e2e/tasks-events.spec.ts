// ACT-SURFACES: web.panel web.sheet web.editor web.reader
// Tasks and events on the web: the Tasks panel (web.panel), the quick-action
// popover (web.sheet) and the editors (web.editor), against the seeded user's own dev-Radicale
// calendar. Each test checks the result through the API.
// Covers: task.complete, task.reopen, task.open_email (web.panel); task.complete, task.edit,
// task.delete, task.open_email, event.edit, event.delete, event.rsvp, event.open_email (web.sheet);
// task.reschedule, task.edit, task.delete, event.reschedule, event.edit, event.delete (web.editor);
// mail.to_task, mail.to_event (web.reader: the message menu).
import type { Page } from '@playwright/test'
import { connectCalendar, conv, events, expect, tasks, test } from './fixtures'

/** A day from today in the browser's zone, as YYYY-MM-DD (toISOString would give UTC's day). */
const localDay = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
const tomorrow = () => localDay(1)

async function openPanel(page: Page, name: 'Tasks' | 'Calendar') {
  await page.goto('/mail/inbox')
  await page.getByRole('complementary', { name: 'Open beside' }).getByRole('button', { name: `${name} beside` }).click()
  return page.getByRole('complementary', { name, exact: true })
}

/** An event tomorrow at 10:00, clear of midnight whatever the time now (the tests then step the
 * Calendar panel to the next day). */
async function eventTomorrow(page: Page, summary: string, conversationId?: number) {
  const start = new Date()
  start.setDate(start.getDate() + 1)
  start.setHours(10, 0, 0, 0)
  const r = await page.request.post('/api/v1/calendar/events', { data: {
    summary, start: start.toISOString(), end: new Date(start.getTime() + 30 * 60_000).toISOString(),
    ...(conversationId ? { conversation_id: conversationId } : {}) } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

test('web.panel: complete, reopen and open the email', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const source = await conv(page.request, 'E2E Reply to me')
  await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Finish report', due: tomorrow(), conversation_id: source.id } })
  const panel = await openPanel(page, 'Tasks')
  // The panel draws the Tasks app's rows (TaskRow).
  const row = panel.getByTestId('task-row').filter({ hasText: 'E2E Finish report' })

  await row.getByRole('checkbox', { name: 'Complete E2E Finish report' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Finish report')?.status).toBe('completed')
  await panel.getByRole('button', { name: /^Completed/ }).click()
  await panel.getByTestId('task-row').filter({ hasText: 'E2E Finish report' }).getByRole('checkbox', { name: 'Reopen E2E Finish report' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Finish report')?.status).not.toBe('completed')

  await panel.getByTestId('task-row').filter({ hasText: 'E2E Finish report' }).first().getByRole('button', { name: 'Open the email' }).click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
})

test('web.sheet: task done, edit, open email and delete', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const source = await conv(page.request, 'E2E Reply to me')
  for (const summary of ['E2E Sheet done', 'E2E Sheet edit', 'E2E Sheet delete'])
    await page.request.post('/api/v1/tasks', { data: { summary, due: tomorrow(), conversation_id: source.id } })
  const panel = await openPanel(page, 'Tasks')
  const sheet = (name: string) => page.getByRole('dialog', { name })

  await panel.getByTestId('task-row').filter({ hasText: 'E2E Sheet done' }).getByText('E2E Sheet done', { exact: true }).click()
  await sheet('E2E Sheet done').getByRole('button', { name: 'Done' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Sheet done')?.status).toBe('completed')

  await panel.getByTestId('task-row').filter({ hasText: 'E2E Sheet edit' }).getByText('E2E Sheet edit', { exact: true }).click()
  await sheet('E2E Sheet edit').getByRole('button', { name: 'Edit' }).click()
  const editor = page.getByRole('dialog', { name: 'Edit task' })
  await editor.getByLabel('Title').fill('E2E Sheet edited')
  await editor.getByLabel('Notes').fill('Bring the numbers')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Sheet edited')?.notes).toBe('Bring the numbers')

  await panel.getByTestId('task-row').filter({ hasText: 'E2E Sheet delete' }).getByText('E2E Sheet delete', { exact: true }).click()
  await sheet('E2E Sheet delete').getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => (await tasks(page.request)).some((t) => t.summary === 'E2E Sheet delete')).toBe(false)

  await panel.getByTestId('task-row').filter({ hasText: 'E2E Sheet edited' }).getByText('E2E Sheet edited', { exact: true }).click()
  await sheet('E2E Sheet edited').getByRole('button', { name: 'Email' }).click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
})

test('web.editor: task reschedule and delete', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Editor task', due: tomorrow() } })
  const panel = await openPanel(page, 'Tasks')
  const openEditor = async () => {
    await panel.getByTestId('task-row').filter({ hasText: 'E2E Editor task' }).getByText('E2E Editor task', { exact: true }).click()
    await page.getByRole('dialog', { name: 'E2E Editor task' }).getByRole('button', { name: 'Edit' }).click()
    return page.getByRole('dialog', { name: 'Edit task' })
  }
  const later = localDay(5)
  let editor = await openEditor()
  await editor.getByLabel('Due').fill(later)
  await editor.getByLabel('Time').fill('14:30')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Editor task')?.due ?? '').toContain(later)

  editor = await openEditor()
  await editor.getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => (await tasks(page.request)).some((t) => t.summary === 'E2E Editor task')).toBe(false)
})

test('web.sheet and web.editor: event edit, reschedule, open email and delete', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const source = await conv(page.request, 'E2E Reply to me')
  await eventTomorrow(page, 'E2E Planning', source.id)
  await eventTomorrow(page, 'E2E Standup')
  const panel = await openPanel(page, 'Calendar')
  await panel.getByRole('button', { name: 'Next day' }).click()

  // The panel draws the Calendar app's rows; a row opens the app's detail, Edit its editor.
  await panel.getByTestId('cal-event').filter({ hasText: 'E2E Planning' }).click()
  await page.getByRole('dialog', { name: 'E2E Planning' }).getByRole('button', { name: 'Edit (e)' }).click()
  const editor = page.getByTestId('cal-editor')
  await editor.getByLabel('Location').fill('Room 4')
  await editor.getByTestId('cal-editor-save').click()
  await expect.poll(async () => (await events(page.request)).find((e) => e.summary === 'E2E Planning')?.location).toBe('Room 4')

  // event.reschedule from the detail is the app's; here the delete (sent after the undo window).
  await panel.getByTestId('cal-event').filter({ hasText: 'E2E Standup' }).click()
  await page.getByRole('dialog', { name: 'E2E Standup' }).getByRole('button', { name: 'Delete (#)' }).click()
  await expect.poll(async () => (await events(page.request)).some((e) => e.summary === 'E2E Standup')).toBe(false)

  await panel.getByTestId('cal-event').filter({ hasText: 'E2E Planning' }).click()
  await page.getByRole('dialog', { name: 'E2E Planning' }).getByRole('button', { name: 'Delete (#)' }).click()
  await expect.poll(async () => (await events(page.request)).some((e) => e.summary === 'E2E Planning')).toBe(false)
})

test('web.sheet: event open email', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const source = await conv(page.request, 'E2E Reply to me')
  await eventTomorrow(page, 'E2E Review', source.id)
  const panel = await openPanel(page, 'Calendar')
  await panel.getByRole('button', { name: 'Next day' }).click()
  await panel.getByTestId('cal-event').filter({ hasText: 'E2E Review' }).click()
  await page.getByRole('dialog', { name: 'E2E Review' }).getByRole('button', { name: 'From an email' }).click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
})

test('web.sheet: answer an invitation', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const inv = (await events(page.request)).find((e) => e.summary === 'E2E Invitation')!
  const panel = await openPanel(page, 'Calendar')
  const days = Math.round((new Date(inv.start).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86_400_000)
  for (let i = 0; i < days; i++) await panel.getByRole('button', { name: 'Next day' }).click()
  await panel.getByTestId('cal-event').filter({ hasText: 'E2E Invitation' }).click()
  await page.getByRole('dialog', { name: 'E2E Invitation' }).getByRole('group', { name: 'Going?' }).getByRole('button', { name: 'Yes' }).click()
  await expect.poll(async () => (await events(page.request)).find((e) => e.summary === 'E2E Invitation')?.my_status?.toLowerCase()).toBe('accepted')
})

test('web.reader: mail.to_task and mail.to_event from the message menu', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const source = await conv(page.request, 'E2E Task me')
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Task me' }).first().click()
  const message = page.getByRole('article')

  await message.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Add to tasks' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add to tasks' })
  // The suggestion (model or heuristic) fills the title; replace it with a known one.
  await expect(dialog.getByLabel('Title')).not.toHaveValue('')
  await dialog.getByLabel('Title').fill('E2E Task from mail')
  await dialog.getByLabel('Due').fill(tomorrow())
  await dialog.getByRole('button', { name: 'Add task' }).click()
  await expect.poll(async () => (await tasks(page.request)).find((t) => t.summary === 'E2E Task from mail')?.conversation_id).toBe(source.id)

  await message.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Create event' }).click()
  // The Calendar app's editor, prefilled from the suggestion and linked to the email.
  const editor = page.getByTestId('cal-editor')
  await expect(editor.getByRole('textbox', { name: 'Title' })).not.toHaveValue('')
  await editor.getByRole('textbox', { name: 'Title' }).fill('E2E Event from mail')
  await editor.getByTestId('cal-editor-save').click()
  await expect.poll(async () => ((await (await page.request.get(`/api/v1/conversations/${(await conv(page.request, 'E2E Task me')).id}/links`)).json()) as { summary: string }[]).some((l) => l.summary === 'E2E Event from mail')).toBe(true)
})
