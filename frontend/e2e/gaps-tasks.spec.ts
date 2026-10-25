// ACT-SURFACES: web.editor web.pane web.palette
// The task cells closed after the main build (web gaps): the task editor's list, repeat and
// "remind me at" (task.move_list, task.set_repeat, task.remind_at on web.editor), Skip and the
// "Subtask of" picker in the pane (task.skip, task.reparent on web.pane), and the palette's task
// search and new list (task.search, list.create on web.palette). Each test checks the result
// through the API.
import type { Page } from '@playwright/test'
import { connectCalendar, expect, shot, test } from './fixtures'

test.use({ extras: true })

const day = (offset: number) => {
  const d = new Date(Date.now() + offset * 86_400_000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

interface T { uid: string; summary: string; due: string | null; list: string; rrule: string | null; repeat_from?: string; reminders_at?: string[]; parent: string | null }

async function newTask(page: Page, body: Record<string, unknown>): Promise<T> {
  const r = await page.request.post('/api/v1/tasks', { data: body })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as T
}
const get = async (page: Page, uid: string) => (await (await page.request.get(`/api/v1/tasks/${encodeURIComponent(uid)}`)).json()) as T
const trim = (u: string) => u.replace(/\/$/, '')

test('task editor (web.editor): list, repeat from completion and a reminder at a set time', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const x = seeded.extras!
  const t = await newTask(page, { summary: 'E2E Editor fields', due: day(1) })
  await page.goto('/mail/inbox')
  await page.getByRole('complementary', { name: 'Open beside' }).getByRole('button', { name: 'Tasks beside' }).click()
  const panel = page.getByRole('complementary', { name: 'Tasks', exact: true })
  await panel.getByTestId('task-row').filter({ hasText: 'E2E Editor fields' }).getByText('E2E Editor fields', { exact: true }).click()
  await page.getByRole('dialog', { name: 'E2E Editor fields' }).getByRole('button', { name: 'Edit' }).click()
  const editor = page.getByRole('dialog', { name: 'Edit task' })
  // ACT task.move_list, ACT task.set_repeat, ACT task.remind_at
  await editor.getByLabel('List').selectOption({ label: 'E2E Errands' })
  await editor.getByLabel('Repeat').selectOption({ label: 'Daily' })
  await editor.getByLabel('Next one counts from').selectOption('completion')
  await editor.getByLabel('Remind me at').fill(`${day(1)}T08:30`)
  await editor.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(editor.getByTestId('remind-at')).toHaveCount(1)
  await page.waitForTimeout(200)
  await page.screenshot({ path: shot('task-editor.png') })
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => trim((await get(page, t.uid)).list ?? '')).toBe(trim(x.task_list!.url))
  const saved = await get(page, t.uid)
  expect(saved.rrule).toContain('FREQ=DAILY')
  expect(saved.repeat_from).toBe('completion')
  expect(saved.reminders_at).toHaveLength(1)
  expect(new Date(saved.reminders_at![0]).getTime()).toBe(new Date(`${day(1)}T08:30:00`).getTime())
})

test('task pane (web.pane): Skip this occurrence with undo, and the Subtask of picker', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const rep = await newTask(page, { summary: 'E2E Skip me', due: day(0), recurrence: 'FREQ=DAILY' })
  const parent = await newTask(page, { summary: 'E2E Parent task' })
  const child = await newTask(page, { summary: 'E2E Child task' })

  // ACT task.skip
  await page.goto(`/tasks/task/${encodeURIComponent(rep.uid)}`)
  const pane = page.getByTestId('task-pane')
  await pane.getByRole('button', { name: 'Skip this occurrence' }).click()
  await expect.poll(async () => (await get(page, rep.uid)).due).toBe(day(1))
  await page.locator('.toast').filter({ hasText: 'Skipped to the next occurrence' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await get(page, rep.uid)).due).toBe(day(0))

  // ACT task.reparent: one level, from the pane.
  await page.goto(`/tasks/task/${encodeURIComponent(child.uid)}`)
  await pane.getByRole('button', { name: 'Subtask of' }).click()
  const pick = page.getByRole('dialog', { name: 'Subtask of' }).or(page.locator('.tk-parent-pop'))
  await pick.getByRole('textbox', { name: 'Find a parent task' }).fill('parent')
  await page.waitForTimeout(200)
  await page.screenshot({ path: shot('parent-picker.png') })
  await pick.getByRole('menuitemcheckbox', { name: 'E2E Parent task' }).click()
  await expect.poll(async () => (await get(page, child.uid)).parent).toBe(parent.uid)
  await expect(pane.getByRole('button', { name: 'Subtask of' })).toContainText('E2E Parent task')
  await pane.getByRole('button', { name: 'Subtask of' }).click()
  await page.locator('.tk-parent-pop').getByRole('menuitemcheckbox', { name: 'None (top level)' }).click()
  await expect.poll(async () => (await get(page, child.uid)).parent ?? null).toBeNull()
})

test('palette (web.palette): task search and a new task list', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const t = await newTask(page, { summary: 'E2E Zebra report', due: day(2) })
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('waiting-bell')).toBeVisible()
  await page.keyboard.press('Control+k')
  const input = page.getByRole('combobox', { name: 'Command' })
  // ACT task.search: the matches open in Tasks; the last item runs the search there.
  await input.fill('zebra')
  await page.getByRole('option', { name: /E2E Zebra report/ }).click()
  await expect(page).toHaveURL(new RegExp(`/tasks/task/${encodeURIComponent(t.uid)}`))
  await expect(page.getByTestId('task-pane')).toBeVisible()
  await page.keyboard.press('Control+k')
  await input.fill('zebra')
  await page.getByRole('option', { name: 'Search tasks for “zebra”' }).click()
  await expect(page.getByRole('combobox', { name: 'Search tasks' })).toHaveValue('zebra')

  // ACT list.create: "New list: …" shows only the two create items, the checklist first and the
  // task list after it; ↓ then Enter makes the task list.
  // The ↵ hint sits on what Enter runs: the first item, then the one ↓ selects.
  await page.keyboard.press('Control+k')
  await input.fill('New list: E2E Palette list')
  await expect(page.getByRole('option')).toHaveText([/^New checklist: E2E Palette list/, /^New task list: E2E Palette list/])
  const enterHint = page.locator('.palette-item .pi-enter')
  const marked = page.locator('.palette-item', { has: page.locator('.pi-enter') })
  await expect(enterHint).toHaveCount(1)
  await expect(marked).toContainText('New checklist: E2E Palette list')
  await input.press('ArrowDown')
  await expect(page.getByRole('option', { name: /^New task list: E2E Palette list/ })).toHaveAttribute('aria-selected', 'true')
  await expect(enterHint).toHaveCount(1)
  await expect(marked).toContainText('New task list: E2E Palette list')
  await input.press('Enter')
  await expect.poll(async () => ((await (await page.request.get('/api/v1/calendar/calendars')).json()) as { name: string; tasks?: boolean }[])
    .find((c) => c.name === 'E2E Palette list')?.tasks).toBe(true)
  // The task list only: no checklist of that name.
  const { notes } = (await (await page.request.get('/api/v1/notes', { params: { view: 'lists' } })).json()) as { notes: { title: string }[] }
  expect(notes.filter((n) => n.title === 'E2E Palette list')).toHaveLength(0)
})
