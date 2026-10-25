// ACT-SURFACES: web.tasks web.pane
// The Tasks app (web.tasks, web.pane) against the seeded user's own
// dev-Radicale task list. Each test checks the result through the API.
// Covers: the smart lists; ACT task.create (quick add with the grammar), ACT task.subtask_add,
// ACT task.edit (inline title), ACT task.complete, ACT task.reopen, ACT task.reschedule (popover keys and
// typed), ACT task.set_priority, ACT task.tag, ACT task.reorder, ACT task.reparent, ACT task.move_list,
// ACT task.delete, ACT task.duplicate, ACT task.search, ACT task.bulk_complete, ACT task.bulk_move,
// ACT task.bulk_delete, ACT task.bulk_reschedule, ACT task.set_start, ACT task.open_email.
import type { APIRequestContext, Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { apiSettled, connectCalendar, conv, expect, test, type Seed } from './fixtures'
import { mkTask, openApp } from './helpers'

interface FullTask {
  uid: string; summary: string; due: string | null; due_all_day?: boolean; start?: string | null; status: string; priority?: number
  categories?: string[]; parent?: string | null; list?: string; sort_order?: number | null; notes?: string; conversation_id?: unknown
}

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const day = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return iso(d) }

async function all(request: APIRequestContext): Promise<FullTask[]> {
  const j = await (await request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
  return (Array.isArray(j) ? j : j.items) as FullTask[]
}
async function one(request: APIRequestContext, summary: string): Promise<FullTask | undefined> {
  return (await all(request)).find((t) => t.summary === summary)
}
const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkTask<FullTask>(request, body)

/** A second task list, made on the dev Radicale directly (list.create needs the B2 routes). */
async function mkList(request: APIRequestContext, seeded: Seed, name: string): Promise<string> {
  const user = seeded.caldav!.username
  const url = `http://localhost:15232/${user}/${randomUUID()}/`
  const r = await fetch(url, {
    method: 'MKCALENDAR',
    headers: { Authorization: `Basic ${Buffer.from(`${user}:x`).toString('base64')}`, 'Content-Type': 'application/xml' },
    body: `<?xml version="1.0" encoding="utf-8"?><C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:set><D:prop><D:displayname>${name}</D:displayname><C:supported-calendar-component-set><C:comp name="VTODO"/></C:supported-calendar-component-set></D:prop></D:set></C:mkcalendar>`,
  })
  expect(r.status).toBeLessThan(300)
  await request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  return url
}

const row = (page: Page, text: string) => page.getByTestId('task-row').filter({ hasText: text })
const nav = (page: Page, id: string) => page.getByTestId(`tasks-nav-${id}`)

async function open(page: Page, path = '/tasks/today') {
  await openApp(page, path, { ready: page.getByTestId('tasks-app') })
  await expect(page.getByTestId('tasks-header')).toBeVisible()
}

test('smart lists: sections, counts and what each list holds', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const source = await conv(page.request, 'E2E Reply to me')
  await mk(page.request, { summary: 'E2E Overdue one', due: day(-2) })
  await mk(page.request, { summary: 'E2E Due today', due: day(0) })
  await mk(page.request, { summary: 'E2E Due tomorrow', due: day(1) })
  await mk(page.request, { summary: 'E2E No date', priority: 1 })
  await mk(page.request, { summary: 'E2E From mail', conversation_id: source.id, due: day(9) })
  await mk(page.request, { summary: 'E2E Deferred', start: day(3), due: day(4) })
  await open(page)

  // Today: Overdue first, in red, then Today; the left column counts them.
  const list = page.getByTestId('tasks-list')
  await expect(list.locator('[data-section="overdue"]')).toContainText('Overdue')
  await expect(row(page, 'E2E Overdue one')).toBeVisible()
  await expect(row(page, 'E2E Overdue one').locator('.tk-due')).toHaveText('Overdue 2d')
  await expect(row(page, 'E2E Due today')).toBeVisible()
  await expect(row(page, 'E2E Due tomorrow')).toHaveCount(0)
  await expect(row(page, 'E2E Deferred')).toHaveCount(0)
  await expect(nav(page, 'today').locator('.tk-nav-count')).toHaveText('2')
  await expect(nav(page, 'today').locator('.tk-nav-danger')).toHaveText('1')

  // Upcoming: a section per day; the deferred task on its start day.
  await nav(page, 'upcoming').click()
  await expect(page).toHaveURL(/\/tasks\/upcoming/)
  await expect(page.locator('[data-section="day:' + day(1) + '"]')).toContainText('Tomorrow')
  await expect(row(page, 'E2E Due tomorrow')).toBeVisible()
  await expect(row(page, 'E2E Deferred').locator('.tk-due')).toContainText('Starts')

  // No date, High priority, From email, Scheduled, All.
  await nav(page, 'nodate').click()
  await expect(row(page, 'E2E No date')).toBeVisible()
  await expect(row(page, 'E2E Due today')).toHaveCount(0)
  await nav(page, 'high').click()
  await expect(row(page, 'E2E No date')).toBeVisible()
  await expect(page.getByTestId('task-row')).toHaveCount(1)
  await nav(page, 'email').click()
  await expect(row(page, 'E2E From mail')).toBeVisible()
  await expect(page.getByTestId('task-row')).toHaveCount(1)
  // ACT task.open_email
  await row(page, 'E2E From mail').getByRole('button', { name: 'Open the email' }).click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
  await page.goBack()
  await nav(page, 'scheduled').click()
  await expect(page.getByTestId('task-row')).toHaveCount(5)
  await nav(page, 'all').click()
  await expect(page.getByTestId('task-row')).toHaveCount(5)
  await page.getByRole('button', { name: 'More list actions' }).click()
  await page.getByRole('menuitemcheckbox', { name: 'Show deferred' }).click()
  await expect(page.getByTestId('task-row')).toHaveCount(6)

  // Completed: a completed task moves there.
  await row(page, 'E2E Due today').getByRole('checkbox', { name: 'Complete E2E Due today' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Due today'))?.status).toBe('completed')
  await nav(page, 'completed').click()
  await expect(page.locator('[data-section="today"]')).toContainText('Today')
  await expect(row(page, 'E2E Due today')).toBeVisible()
})

test('quick add: the grammar, chips, rapid entry, subtask mode and pasted lines', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await open(page, '/tasks/all')
  const input = page.getByRole('textbox', { name: 'Add a task' })
  // While a task is being created (aria-busy) the line ignores Enter, and when it is done it
  // clears the line: the next entry waits for that, as a person sees it.
  const idle = () => expect(input).toHaveAttribute('aria-busy', 'false')
  await input.click()
  await input.fill('E2E Call Dana fri 3pm !! @phone')
  const chips = page.getByTestId('tasks-quick-add').locator('.tk-quick-chips')
  await expect(chips).toContainText('Due Fri')
  await expect(chips).toContainText('Medium priority')
  await expect(chips).toContainText('@phone')
  await expect(chips.locator('.tk-new')).toHaveText('new')
  await expect(page.locator('.tk-quick-overlay mark.tk-tok-date')).toHaveText('fri')
  // ACT task.create: Enter creates and keeps the focus for the next one.
  await input.press('Enter')
  await idle()
  await expect(input).toHaveValue('')
  await expect(input).toBeFocused()
  await expect.poll(async () => (await one(page.request, 'E2E Call Dana'))?.priority).toBe(5)
  const t = (await one(page.request, 'E2E Call Dana'))!
  expect(t.categories).toEqual(['phone'])
  const due = new Date(t.due!)
  expect(due.getDay()).toBe(5)
  expect(due.getHours()).toBe(15)

  // × on a chip keeps its words as title text.
  await input.fill('E2E Read report fri')
  await chips.getByRole('button', { name: 'Keep “fri” as text' }).click()
  await input.press('Enter')
  await idle()
  await expect.poll(async () => (await one(page.request, 'E2E Read report fri'))?.due ?? 'none').toBe('none')

  // ACT task.subtask_add: Tab on the empty line makes the next entry a subtask of the last one.
  await input.fill('E2E Parent task')
  await input.press('Enter')
  await idle()
  await expect.poll(async () => !!(await one(page.request, 'E2E Parent task'))).toBe(true)
  await input.press('Tab')
  await expect(page.getByTestId('tasks-quick-add')).toContainText('Subtask of E2E Parent task')
  await input.fill('E2E Child task')
  await input.press('Enter')
  await idle()
  const parent = (await one(page.request, 'E2E Parent task'))!
  await expect.poll(async () => (await one(page.request, 'E2E Child task'))?.parent).toBe(parent.uid)
  await input.press('Shift+Tab')
  await expect(page.getByTestId('tasks-quick-add')).not.toContainText('Subtask of')

  // Shift+Enter creates and opens the pane.
  await input.fill('E2E Open me tom')
  await input.press('Shift+Enter')
  await expect(page.getByTestId('task-pane')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Task title' })).toHaveValue('E2E Open me')
  await expect(page).toHaveURL(/\/tasks\/all\/task\//)
  await page.keyboard.press('Escape')

  // Pasting several lines asks first; each line is parsed on its own.
  await input.focus()
  await page.evaluate(() => {
    const el = document.querySelector<HTMLInputElement>('input[aria-label="Add a task"]')!
    const dt = new DataTransfer()
    dt.setData('text/plain', 'E2E Paste one\nE2E Paste two tom\nE2E Paste three !!!')
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  })
  await page.getByRole('dialog', { name: 'Create 3 tasks?' }).getByRole('button', { name: 'Create 3 tasks' }).click()
  await expect.poll(async () => (await all(page.request)).filter((x) => x.summary.startsWith('E2E Paste')).length).toBe(3)
  expect((await one(page.request, 'E2E Paste three'))?.priority).toBe(1)
  expect((await one(page.request, 'E2E Paste two'))?.due).toBe(day(1))
})

test('inline title edit, complete and undo, delete and undo, duplicate', async ({ page, seeded }) => {
  // The page's own clock, so the delete's undo window can pass at once (below).
  await page.clock.install()
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Rename me', due: day(0) })
  await mk(page.request, { summary: 'E2E Finish me', due: day(0) })
  await mk(page.request, { summary: 'E2E Remove me', due: day(0) })
  await open(page)

  // ACT task.edit: double-click edits in place; the grammar offers its tokens as chips.
  await row(page, 'E2E Rename me').locator('.tk-title').dblclick()
  const edit = page.getByRole('textbox', { name: 'Edit title' })
  await edit.fill('E2E Renamed tom')
  await page.getByRole('button', { name: /Apply: Due/ }).click()
  await edit.press('Enter')
  await expect.poll(async () => (await one(page.request, 'E2E Renamed'))?.due).toBe(day(1))

  // ACT task.complete and its undo (ACT task.reopen).
  await row(page, 'E2E Finish me').getByRole('checkbox').click()
  await expect.poll(async () => (await one(page.request, 'E2E Finish me'))?.status).toBe('completed')
  await page.locator('.toast').filter({ hasText: 'Task completed' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Finish me'))?.status).not.toBe('completed')

  // ACT task.duplicate (Ctrl+D on the focused row).
  await row(page, 'E2E Finish me').locator('.tk-title').click()
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+d')
  await expect.poll(async () => (await all(page.request)).filter((x) => x.summary === 'E2E Finish me').length).toBe(2)

  // ACT task.delete: gone at once, sent after the undo window; Undo keeps it.
  await row(page, 'E2E Remove me').hover()
  await row(page, 'E2E Remove me').getByRole('button', { name: 'More actions for E2E Remove me' }).click()
  await page.getByRole('menuitem', { name: /Delete/ }).click()
  await expect(row(page, 'E2E Remove me')).toHaveCount(0)
  await page.locator('.toast').filter({ hasText: 'Task deleted' }).getByRole('button', { name: 'Undo' }).click()
  await expect(row(page, 'E2E Remove me')).toBeVisible()
  // The 7 s undo window passes on the page's clock; a delete it still sent would be in flight now.
  await page.clock.runFor(7500)
  await apiSettled(page)
  expect(await one(page.request, 'E2E Remove me')).toBeTruthy()
})

// ACT task.reopen (Undo of task.complete) against a read from before the complete (the tasks-app
// race, 10 Oct 2026). From the click on, every task list read answers with what the server held
// before it, as a read that started before the complete came back would. The Undo still goes
// through and nothing reports a change on another device.
test('Undo of complete wins over a list read from before the complete', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Undo race', due: day(0) })
  const before = new Map<string, string>()
  let stale = false
  let completeBack = false
  let staleAfterComplete = 0
  await page.route(/\/api\/v1\/tasks\?/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const key = route.request().url()
    const old = before.get(key)
    if (stale && old) {
      if (completeBack) staleAfterComplete++
      return route.fulfill({ status: 200, contentType: 'application/json', body: old })
    }
    const res = await route.fetch()
    const body = await res.text()
    if (!stale) before.set(key, body)
    return route.fulfill({ response: res, body })
  })
  page.on('response', (r) => {
    if (r.request().method() === 'PATCH' && r.url().includes('/api/v1/tasks/') && r.request().postDataJSON()?.completed === true) completeBack = true
  })
  await open(page)
  await expect(row(page, 'E2E Undo race')).toBeVisible()
  stale = true
  await row(page, 'E2E Undo race').getByRole('checkbox').click()
  await expect.poll(async () => (await one(page.request, 'E2E Undo race'))?.status).toBe('completed')
  // A read from before the complete has landed after it.
  await expect.poll(() => staleAfterComplete).toBeGreaterThan(0)
  await page.locator('.toast').filter({ hasText: 'Task completed' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Undo race'))?.status).not.toBe('completed')
  stale = false
  await expect(page.getByText(/changed on another device/)).toHaveCount(0)
})

test('the pane saves each field as it changes, with an undo per change', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const t = await mk(page.request, { summary: 'E2E Pane task', due: day(0) })
  await page.goto(`/tasks/today/task/${encodeURIComponent(t.uid)}`)
  const pane = page.getByTestId('task-pane')
  await expect(pane).toBeVisible()

  // Title: saved 600 ms after typing stops.
  await pane.getByRole('textbox', { name: 'Task title' }).fill('E2E Pane task renamed')
  await expect(pane.getByTestId('pane-saved')).toContainText('Saved')
  await expect.poll(async () => !!(await one(page.request, 'E2E Pane task renamed'))).toBe(true)

  // ACT task.set_priority
  await pane.getByRole('radio', { name: 'High' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Pane task renamed'))?.priority).toBe(1)

  // ACT task.tag: Enter creates a tag.
  const tagInput = pane.getByRole('textbox', { name: 'Add a tag' })
  await tagInput.fill('errand')
  await tagInput.press('Enter')
  await expect.poll(async () => (await one(page.request, 'E2E Pane task renamed'))?.categories).toEqual(['errand'])

  // Notes, with the light markdown preview after leaving the field.
  await pane.getByRole('textbox', { name: 'Notes' }).fill('Buy **white** paint')
  await expect.poll(async () => (await one(page.request, 'E2E Pane task renamed'))?.notes).toBe('Buy **white** paint')
  await pane.getByRole('textbox', { name: 'Notes' }).blur()
  await expect(pane.locator('.tk-notes-preview strong')).toHaveText('white')

  // ACT task.reschedule from the pane: the typed form.
  await pane.getByTestId('pane-due').click()
  const when = page.getByTestId('when-picker')
  await when.getByRole('textbox', { name: 'Type a date' }).fill('next week')
  await when.getByRole('textbox', { name: 'Type a date' }).press('Enter')
  await expect.poll(async () => (await one(page.request, 'E2E Pane task renamed'))?.due).not.toBe(day(0))

  // Each change has its own undo: the last one (the due) goes back.
  await pane.getByRole('button', { name: 'Undo due' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Pane task renamed'))?.due).toBe(day(0))
  // The priority stays as it was set.
  expect((await one(page.request, 'E2E Pane task renamed'))?.priority).toBe(1)

  // ACT task.subtask_add from the checklist.
  await pane.getByRole('textbox', { name: 'Add subtask' }).fill('E2E Pane sub')
  await pane.getByRole('textbox', { name: 'Add subtask' }).press('Enter')
  await expect.poll(async () => (await one(page.request, 'E2E Pane sub'))?.parent).toBe(t.uid)
  await expect(pane.getByTestId('pane-subtasks')).toContainText('E2E Pane sub')

  // Esc closes the pane; nothing to ask about.
  await pane.getByRole('button', { name: 'Close' }).click()
  await expect(pane).toHaveCount(0)
})

test('a task that leaves the view behind the pane stays in the pane, with its undo', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const t = await mk(page.request, { summary: 'E2E Leaves today', due: day(0) })
  // Hold the pane's look-up among all tasks: the wait for that answer is the moment under test
  // (the pane said "deleted elsewhere" then, and the Undo button's place went to Close).
  let hold = false
  let release!: () => void
  const held = new Promise<void>((r) => { release = r })
  await page.route('**/api/v1/tasks?*', async (route) => {
    const u = new URL(route.request().url())
    if (hold && u.searchParams.get('status') === 'all' && !u.searchParams.has('parent')) await held
    await route.fallback()
  })
  try {
    await page.goto(`/tasks/today/task/${encodeURIComponent(t.uid)}`)
    const pane = page.getByTestId('task-pane')
    await expect(pane.getByRole('textbox', { name: 'Task title' })).toHaveValue('E2E Leaves today')
    await expect(row(page, 'E2E Leaves today')).toBeVisible()

    hold = true
    await pane.getByTestId('pane-due').click()
    const when = page.getByTestId('when-picker')
    await when.getByRole('textbox', { name: 'Type a date' }).fill('next week')
    await when.getByRole('textbox', { name: 'Type a date' }).press('Enter')
    // The task leaves Today; the pane keeps it while it looks for it.
    await expect(row(page, 'E2E Leaves today')).toHaveCount(0)
    await expect(pane.getByRole('textbox', { name: 'Task title' })).toHaveValue('E2E Leaves today')
    await expect(pane.getByText(/deleted elsewhere/)).toHaveCount(0)
    await expect(pane.getByRole('button', { name: 'Undo due' })).toBeVisible()
    release()

    await pane.getByRole('button', { name: 'Undo due' }).click()
    await expect.poll(async () => (await one(page.request, 'E2E Leaves today'))?.due).toBe(day(0))
    await expect(row(page, 'E2E Leaves today')).toBeVisible()

    // A task deleted elsewhere still says so, once the look-up has answered.
    expect((await page.request.delete(`/api/v1/tasks/${encodeURIComponent(t.uid)}`)).ok()).toBeTruthy()
    await expect(pane.getByText(/deleted elsewhere/)).toBeVisible()
  } finally {
    release()
  }
})

test('reschedule popover: accelerators and typed dates', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Move me', due: day(-1) })
  await open(page, '/tasks/all')
  await expect(row(page, 'E2E Move me')).toBeVisible()
  await page.keyboard.press('j')
  await expect(row(page, 'E2E Move me')).toHaveClass(/focused/)
  // ACT task.reschedule: r then m (Tomorrow).
  await page.keyboard.press('r')
  await expect(page.getByTestId('when-picker')).toBeVisible()
  await page.keyboard.press('m')
  await expect.poll(async () => (await one(page.request, 'E2E Move me'))?.due).toBe(day(1))
  // r then a typed "fri 3p".
  await page.keyboard.press('r')
  // The picker takes the first typed letter and moves it into its date line: type the rest there
  // once it has the focus (on a busy machine the focus moves after the next key).
  const when = page.getByTestId('when-picker')
  await expect(when).toBeVisible()
  await expect(when).toBeFocused()
  await page.keyboard.type('f')
  const typed = when.getByRole('textbox', { name: 'Type a date' })
  await expect(typed).toBeFocused()
  await expect(typed).toHaveValue('f')
  await page.keyboard.type('ri 3p')
  await expect(page.getByTestId('when-picker').locator('.tk-when-result')).toContainText('3:00 PM')
  await page.keyboard.press('Enter')
  await expect.poll(async () => { const d = (await one(page.request, 'E2E Move me'))?.due; return d ? new Date(d).getDay() * 100 + new Date(d).getHours() : null }).toBe(515)
  // r then 0: no date (once the picker has the focus, as above).
  await page.keyboard.press('r')
  await expect(page.getByTestId('when-picker')).toBeFocused()
  await page.keyboard.press('0')
  await expect.poll(async () => (await one(page.request, 'E2E Move me'))?.due ?? null).toBeNull()
  // ACT task.set_start: s then w (this weekend), once the picker has the focus (as above).
  await page.keyboard.press('s')
  await expect(page.getByTestId('when-picker')).toBeFocused()
  await page.keyboard.press('w')
  await expect.poll(async () => (await one(page.request, 'E2E Move me'))?.start ?? null).not.toBeNull()
  // Undo with z puts the start back.
  await page.keyboard.press('z')
  await expect.poll(async () => (await one(page.request, 'E2E Move me'))?.start ?? null).toBeNull()
})

test('reorder and indent in a list, by keys and by drag', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  for (const [i, s] of ['E2E A', 'E2E B', 'E2E C'].entries()) await mk(page.request, { summary: s, sort_order: (i + 1) * 1024 })
  await open(page, '/tasks/nodate')
  const order = async () => (await all(page.request)).filter((t) => t.summary.startsWith('E2E ') && !t.parent)
    .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)).map((t) => t.summary)
  const first = page.getByTestId('task-row').first()
  await expect(first).toContainText('E2E A')

  // ACT task.reorder: Alt+↓ moves the focused task down (manual order).
  await page.keyboard.press('j')
  await page.keyboard.press('Alt+ArrowDown')
  await expect.poll(order).toEqual(['E2E B', 'E2E A', 'E2E C'])

  // ACT task.reparent: Tab makes the focused task a subtask of the one above; Shift+Tab undoes it.
  await row(page, 'E2E A').locator('.tk-title').click()
  await page.keyboard.press('Escape')
  await page.keyboard.press('Tab')
  const b = (await one(page.request, 'E2E B'))!
  await expect.poll(async () => (await one(page.request, 'E2E A'))?.parent).toBe(b.uid)
  // The server has it before the list redraws it as a subtask and focuses it again; under load
  // Shift+Tab pressed in between did nothing (ship run of 8 Oct 2026).
  await expect(row(page, 'E2E A')).toHaveClass(/\bsub\b/)
  await expect(row(page, 'E2E A')).toHaveClass(/\bfocused\b/)
  await page.keyboard.press('Shift+Tab')
  await expect.poll(async () => (await one(page.request, 'E2E A'))?.parent ?? null).toBeNull()

  // Drag C above B.
  await expect(page.getByTestId('task-row').first()).toContainText('E2E B')
  const c = row(page, 'E2E C')
  await c.hover()
  await c.locator('.tk-handle').dragTo(row(page, 'E2E B'), { targetPosition: { x: 40, y: 4 } })
  await expect.poll(async () => (await order())[0]).toBe('E2E C')
})

test('undo puts a reorder, an indent, a drag and a subtask move back', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  for (const [i, s] of ['E2E A', 'E2E B', 'E2E C', 'E2E D'].entries()) await mk(page.request, { summary: s, sort_order: (i + 1) * 1024 })
  await open(page, '/tasks/nodate')
  const order = async () => (await all(page.request)).filter((t) => t.summary.startsWith('E2E ') && !t.parent)
    .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)).map((t) => t.summary)
  await expect(page.getByTestId('task-row').first()).toContainText('E2E A')

  // ACT task.reorder: Alt+↓, then z puts it back between its old neighbours.
  await page.keyboard.press('j')
  await page.keyboard.press('Alt+ArrowDown')
  await expect.poll(order).toEqual(['E2E B', 'E2E A', 'E2E C', 'E2E D'])
  await expect(page.locator('.toast').filter({ hasText: 'Task moved' })).toBeVisible()
  await page.keyboard.press('z')
  await expect.poll(order).toEqual(['E2E A', 'E2E B', 'E2E C', 'E2E D'])

  // ACT task.reparent: Tab makes C a subtask of B; the toast's Undo makes it a task again, in its place.
  await expect(page.getByTestId('task-row').first()).toContainText('E2E A')
  await row(page, 'E2E C').locator('.tk-title').click()
  await page.keyboard.press('Escape')
  await page.keyboard.press('Tab')
  const b = (await one(page.request, 'E2E B'))!
  await expect.poll(async () => (await one(page.request, 'E2E C'))?.parent).toBe(b.uid)
  await page.locator('.toast').filter({ hasText: 'subtask of' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E C'))?.parent ?? null).toBeNull()
  await expect.poll(order).toEqual(['E2E A', 'E2E B', 'E2E C', 'E2E D'])

  // A drag of D above A, then the toast's Undo.
  await expect(row(page, 'E2E C')).not.toHaveClass(/\bsub\b/)
  const d = row(page, 'E2E D')
  await d.hover()
  await d.locator('.tk-handle').dragTo(row(page, 'E2E A'), { targetPosition: { x: 40, y: 4 } })
  await expect.poll(order).toEqual(['E2E D', 'E2E A', 'E2E B', 'E2E C'])
  // The newest toast (the earlier moves' toasts may still show).
  await page.locator('.toast').filter({ hasText: 'Task moved' }).last().getByRole('button', { name: 'Undo' }).click()
  await expect.poll(order).toEqual(['E2E A', 'E2E B', 'E2E C', 'E2E D'])

  // ACT task.reorder in the pane's subtasks: a drag, then Undo.
  const p = await mk(page.request, { summary: 'E2E Parent', sort_order: 9000 })
  for (const [i, s] of ['E2E Sub 1', 'E2E Sub 2'].entries()) await mk(page.request, { summary: s, parent: p.uid, sort_order: (i + 1) * 1024 })
  await page.goto(`/tasks/nodate/task/${encodeURIComponent(p.uid)}`)
  const pane = page.getByTestId('task-pane')
  const subs = pane.getByTestId('pane-subtasks')
  await expect(subs).toContainText('E2E Sub 2')
  const subOrder = async () => (await all(page.request)).filter((t) => t.parent === p.uid)
    .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)).map((t) => t.summary)
  await subs.locator('.tk-subtask', { hasText: 'E2E Sub 2' }).dragTo(subs.locator('.tk-subtask', { hasText: 'E2E Sub 1' }))
  await expect.poll(subOrder).toEqual(['E2E Sub 2', 'E2E Sub 1'])
  // The newest toast (the earlier moves' toasts may still show).
  await page.locator('.toast').filter({ hasText: 'Task moved' }).last().getByRole('button', { name: 'Undo' }).click()
  await expect.poll(subOrder).toEqual(['E2E Sub 1', 'E2E Sub 2'])
})

test('drag to a list, a tag, a smart list and a day', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const work = await mkList(page.request, seeded, 'E2E Work')
  await mk(page.request, { summary: 'E2E Drag me', due: day(0), categories: ['seed'] })
  await mk(page.request, { summary: 'E2E Tag source', categories: ['urgent'] })
  const handle = async (text: string) => {
    await row(page, text).hover()
    return row(page, text).locator('.tk-handle')
  }
  // The handle shows in manual order: All, grouped by nothing, sorted by hand.
  await open(page, '/tasks/all')
  await page.getByRole('button', { name: 'Group' }).click()
  await page.getByRole('menuitemcheckbox', { name: 'None' }).click()
  await page.getByRole('button', { name: /^Sort/ }).click()
  await page.getByRole('menuitemcheckbox', { name: 'Manual' }).click()
  // ACT task.move_list
  await (await handle('E2E Drag me')).dragTo(page.getByTestId('tasks-nav-list-E2E Work'))
  await expect.poll(async () => (await one(page.request, 'E2E Drag me'))?.list).toBe(work)
  // ACT task.tag
  await (await handle('E2E Drag me')).dragTo(page.getByTestId('tasks-nav-tag-urgent'))
  await expect.poll(async () => (await one(page.request, 'E2E Drag me'))?.categories).toEqual(['seed', 'urgent'])
  // ACT task.reschedule: onto No date, then onto Today.
  await (await handle('E2E Drag me')).dragTo(page.getByTestId('tasks-nav-nodate'))
  await expect.poll(async () => (await one(page.request, 'E2E Drag me'))?.due ?? null).toBeNull()
  await (await handle('E2E Drag me')).dragTo(page.getByTestId('tasks-nav-upcoming'))
  await expect.poll(async () => (await one(page.request, 'E2E Drag me'))?.due).toBe(day(1))
})

test('select and bulk: complete, move, reschedule and delete with one undo', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const work = await mkList(page.request, seeded, 'E2E Bulk list')
  for (const s of ['E2E Bulk 1', 'E2E Bulk 2', 'E2E Bulk 3']) await mk(page.request, { summary: s, due: day(0) })
  await open(page, '/tasks/today')
  await expect(row(page, 'E2E Bulk')).toHaveCount(3)
  const bulk = (s: string) => all(page.request).then((ts) => ts.filter((t) => t.summary.startsWith('E2E Bulk')).map((t) => (t as unknown as Record<string, unknown>)[s]))

  // x toggles the focused row; Ctrl+click adds; Shift+click selects a range.
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('j')
    await page.keyboard.press('x')
  }
  const bar = page.getByTestId('tasks-bulk')
  await expect(bar).toContainText('3 selected')

  // ACT task.bulk_complete, one undo for all.
  await bar.getByRole('button', { name: 'Complete' }).click()
  await expect.poll(() => bulk('status')).toEqual(['completed', 'completed', 'completed'])
  await page.locator('.toast').filter({ hasText: 'Completed 3 tasks' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(() => bulk('status')).toEqual(['needs-action', 'needs-action', 'needs-action'])
  await expect(row(page, 'E2E Bulk')).toHaveCount(3)
  await expect(page.locator('.tk-row.lingering')).toHaveCount(0)

  // ACT task.bulk_reschedule: Shift by +1 week keeps each task's own date and time.
  await page.keyboard.press('*')
  await page.keyboard.press('a')
  await expect(bar).toContainText('3 selected')
  await bar.getByRole('button', { name: 'Reschedule' }).click()
  await page.getByRole('button', { name: /Shift by \+1 week/ }).click()
  await expect.poll(() => bulk('due')).toEqual([day(7), day(7), day(7)])

  // ACT task.bulk_move
  await page.goto('/tasks/all')
  await expect(row(page, 'E2E Bulk')).toHaveCount(3)
  await page.keyboard.press('*')
  await page.keyboard.press('a')
  await bar.getByRole('button', { name: 'Move' }).click()
  await page.getByRole('menuitem', { name: 'E2E Bulk list' }).click()
  await expect.poll(() => bulk('list'), { timeout: 30_000 }).toEqual([work, work, work])

  // ACT task.bulk_delete: hidden at once; Undo brings them back; without undo they go.
  await page.keyboard.press('*')
  await page.keyboard.press('a')
  await bar.getByRole('button', { name: 'Delete' }).click()
  await expect(row(page, 'E2E Bulk')).toHaveCount(0)
  await page.locator('.toast').filter({ hasText: 'Deleted 3 tasks' }).getByRole('button', { name: 'Undo' }).click()
  await expect(row(page, 'E2E Bulk')).toHaveCount(3)
  await page.keyboard.press('*')
  await page.keyboard.press('a')
  await page.keyboard.press('#')
  await expect(row(page, 'E2E Bulk')).toHaveCount(0)
  await expect.poll(async () => (await bulk('uid')).length).toBe(0)
})

test('search with operators, and the keyboard flow', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Send budget', due: day(-1), categories: ['finance'], priority: 1 })
  await mk(page.request, { summary: 'E2E Paint the fence', notes: 'white matte', due: day(3) })
  const done = await mk(page.request, { summary: 'E2E Done already' })
  await page.request.patch(`/api/v1/tasks/${encodeURIComponent(done.uid)}`, { data: { completed: true } })
  await open(page, '/tasks/all')

  // ACT task.search: / focuses it; free text matches notes; operators narrow.
  await page.keyboard.press('/')
  const search = page.getByRole('combobox', { name: 'Search tasks' })
  await expect(search).toBeFocused()
  await search.fill('matte')
  await expect(page.getByTestId('task-row')).toHaveCount(1)
  await expect(row(page, 'E2E Paint the fence')).toBeVisible()
  await search.fill('@finance is:overdue')
  await expect(page.getByTestId('task-row')).toHaveCount(1)
  await expect(row(page, 'E2E Send budget')).toBeVisible()
  await search.fill('pri:high')
  await expect(page.getByTestId('task-row')).toHaveCount(1)
  // Esc returns the box to All; Clear ends the search.
  await search.press('Escape')
  await expect(page.getByTestId('search-scope')).toHaveText('All')
  await page.getByRole('search').getByRole('button', { name: 'Clear search' }).click()
  await expect(page.getByTestId('task-row')).toHaveCount(2)

  // n focuses quick add; Esc leaves it with the text kept.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('n')
  await expect(page.getByRole('textbox', { name: 'Add a task' })).toBeFocused()
  await page.keyboard.press('Escape')
  // j/k move; 3 sets High; Space completes; z undoes.
  await page.keyboard.press('j')
  await page.keyboard.press('j')
  const focusedRow = page.locator('.tk-row.focused')
  await expect(focusedRow).toHaveCount(1)
  const name = (await focusedRow.locator('.tk-title').textContent())!
  await page.keyboard.press('0')
  await expect.poll(async () => (await one(page.request, name))?.priority ?? 0).toBe(0)
  await page.keyboard.press('3')
  await expect.poll(async () => (await one(page.request, name))?.priority).toBe(1)
  await page.keyboard.press(' ')
  await expect.poll(async () => (await one(page.request, name))?.status).toBe('completed')
  await page.keyboard.press('z')
  await expect.poll(async () => (await one(page.request, name))?.status).not.toBe('completed')
  // Enter opens the pane, Esc closes it; g u goes to Upcoming.
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('task-pane')).toBeVisible()
  await page.locator('.tk-pane-head').click()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('task-pane')).toHaveCount(0)
  await page.keyboard.press('g')
  await page.keyboard.press('u')
  await expect(page).toHaveURL(/\/tasks\/upcoming/)
})

test('states: empty Today with the next hint, empty list, no account', async ({ page, seeded }) => {
  await page.goto('/tasks/today')
  await expect(page.getByTestId('tasks-no-account')).toContainText('No calendar connected yet')
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Tomorrow only', due: day(1) })
  await open(page, '/tasks/today')
  await expect(page.getByTestId('tasks-empty')).toContainText('Nothing due today')
  await expect(page.getByTestId('tasks-empty')).toContainText('1 task tomorrow →')
  await open(page, '/tasks/high')
  await expect(page.getByTestId('tasks-empty')).toContainText('No tasks')
})

test('waiting for reply: follow-ups as rows, made into a task', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const c = await conv(page.request, 'E2E Follow me')
  const until = new Date(Date.now() + 3 * 3600_000).toISOString()
  const r = await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'follow_up', until } })
  expect(r.ok()).toBeTruthy()
  await open(page, '/tasks/waiting')
  const fu = page.getByTestId('followup-row').filter({ hasText: 'E2E Follow me' })
  await expect(fu).toBeVisible()
  await expect(nav(page, 'waiting').locator('.tk-nav-count')).toHaveText('1')
  await fu.getByRole('button', { name: 'Make it a task' }).click()
  await expect.poll(async () => (await one(page.request, 'Follow up: E2E Follow me'))?.conversation_id).toBeTruthy()
  await expect(fu).toHaveCount(0)
})
