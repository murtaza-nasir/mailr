// ACT-SURFACES: web.tasks web.pane
// Tasks app: lists and tags from the left column, bulk tags and priority, Delete completed, Skip,
// and the pane's own fields (web.tasks, web.pane). Each step checks the
// result through the API.
// Covers: ACT list.create, ACT list.rename, ACT list.color, ACT list.reorder, ACT list.set_default,
// ACT list.delete, ACT tag.rename, ACT tag.color, ACT tag.delete, ACT task.bulk_tag,
// ACT task.bulk_priority, ACT task.clear_completed, ACT task.skip, ACT task.complete,
// ACT task.reopen, ACT task.edit, ACT task.set_repeat, ACT task.remind_at, ACT task.reply_email.
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, conv, expect, test } from './fixtures'
import { mkTask, openApp } from './helpers'

interface FullTask {
  uid: string; summary: string; due: string | null; status: string; priority?: number; categories?: string[]
  rrule?: string | null; reminders?: number[]; reminders_at?: string[]; list?: string
}
interface Coll { url: string; name: string; color?: string | null; sort_order?: number | null; tasks?: boolean }

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
async function colls(request: APIRequestContext): Promise<Coll[]> {
  const j = await (await request.get('/api/v1/calendar/calendars')).json()
  return (Array.isArray(j) ? j : j.items) as Coll[]
}
const coll = async (request: APIRequestContext, name: string) => (await colls(request)).find((c) => c.name === name)

const row = (page: Page, text: string) => page.getByTestId('task-row').filter({ hasText: text })
const nav = (page: Page, id: string) => page.getByTestId(`tasks-nav-${id}`)
/** A menu entry, plain or with a check mark (menuitemcheckbox). */
const item = (page: Page, name: string) => page.getByRole('menuitem', { name }).or(page.getByRole('menuitemcheckbox', { name }))

async function open(page: Page, path = '/tasks/all') {
  await openApp(page, path, { ready: page.getByTestId('tasks-app') })
  await expect(page.getByTestId('tasks-header')).toBeVisible()
}

async function listMenu(page: Page, name: string) {
  await nav(page, `list-${name}`).hover()
  await page.getByRole('button', { name: `Actions for list ${name}` }).click()
}

test('lists: create, rename, colour, reorder, default and delete', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await open(page)
  await expect(nav(page, 'list-Personal')).toBeVisible()

  // ACT list.create
  await page.getByRole('button', { name: 'New list' }).first().click()
  const dlg = page.getByRole('dialog', { name: 'New list' })
  await dlg.getByRole('textbox', { name: 'Name' }).fill('E2E Groceries')
  await dlg.getByRole('button', { name: 'Create' }).click()
  await expect(nav(page, 'list-E2E Groceries')).toBeVisible()
  await expect.poll(async () => !!(await coll(page.request, 'E2E Groceries'))).toBe(true)

  // ACT list.rename
  await listMenu(page, 'E2E Groceries')
  await item(page, 'Rename').click()
  const ren = page.getByRole('dialog', { name: 'Rename list' })
  await ren.getByRole('textbox', { name: 'Name' }).fill('E2E Shopping')
  await ren.getByRole('button', { name: 'Rename' }).click()
  await expect.poll(async () => !!(await coll(page.request, 'E2E Shopping'))).toBe(true)
  await expect(nav(page, 'list-E2E Shopping')).toBeVisible()

  // ACT list.color
  await listMenu(page, 'E2E Shopping')
  await page.getByRole('button', { name: 'Grape' }).click()
  await expect.poll(async () => (await coll(page.request, 'E2E Shopping'))?.color?.toLowerCase()).toBeTruthy()
  const colour = (await coll(page.request, 'E2E Shopping'))!.color

  // ACT list.reorder: drag the new list above Personal.
  await nav(page, 'list-E2E Shopping').dragTo(nav(page, 'list-Personal'))
  await expect.poll(async () => {
    const cs = (await colls(page.request)).filter((c) => c.name === 'E2E Shopping' || c.name === 'Personal')
    const s = cs.find((c) => c.name === 'E2E Shopping')?.sort_order
    const p = cs.find((c) => c.name === 'Personal')?.sort_order
    return s != null && p != null && s < p
  }).toBe(true)
  expect(colour).toBeTruthy()

  // ACT list.set_default
  const shopping = (await coll(page.request, 'E2E Shopping'))!
  await listMenu(page, 'E2E Shopping')
  await item(page, 'Default list').click()
  await expect.poll(async () => (await (await page.request.get('/api/v1/calendar/defaults')).json()).task_list).toBe(shopping.url)
  // Back to Personal, so the new list may be deleted.
  const personal = (await coll(page.request, 'Personal'))!
  await listMenu(page, 'Personal')
  await item(page, 'Default list').click()
  await expect.poll(async () => (await (await page.request.get('/api/v1/calendar/defaults')).json()).task_list).toBe(personal.url)

  // ACT list.delete: an empty list asks once.
  await listMenu(page, 'E2E Shopping')
  await item(page, 'Delete list…').click()
  await page.getByRole('dialog', { name: 'Delete “E2E Shopping”?' }).getByRole('button', { name: 'Delete list' }).click()
  await expect.poll(async () => !!(await coll(page.request, 'E2E Shopping'))).toBe(false)
  await expect(nav(page, 'list-E2E Shopping')).toHaveCount(0)
})

test('tags: rename, colour and delete from the left column', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await mk(page.request, { summary: 'E2E Tagged one', categories: ['alpha'] })
  await mk(page.request, { summary: 'E2E Tagged two', categories: ['alpha', 'keep'] })
  await open(page)
  const tagMenu = async (name: string) => {
    await nav(page, `tag-${name}`).hover()
    await page.getByRole('button', { name: `Actions for tag ${name}` }).click()
  }
  await expect(nav(page, 'tag-alpha')).toBeVisible()

  // ACT tag.rename
  await tagMenu('alpha')
  await item(page, 'Rename').click()
  const ren = page.getByRole('dialog', { name: 'Rename tag' })
  await ren.getByRole('textbox', { name: 'Name' }).fill('beta')
  await ren.getByRole('button', { name: 'Rename' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Tagged one'))?.categories).toEqual(['beta'])
  await expect.poll(async () => (await one(page.request, 'E2E Tagged two'))?.categories?.slice().sort()).toEqual(['beta', 'keep'])

  // ACT tag.color
  await expect(nav(page, 'tag-beta')).toBeVisible()
  await tagMenu('beta')
  await page.getByRole('button', { name: 'Grape' }).click()
  await expect.poll(async () => {
    const tags = (await (await page.request.get('/api/v1/tasks/tags')).json()) as { name: string; color: string | null }[]
    return !!tags.find((t) => t.name === 'beta')?.color
  }).toBe(true)

  // ACT tag.delete
  await tagMenu('beta')
  await item(page, 'Delete tag').click()
  await page.getByRole('dialog', { name: 'Delete the tag @beta?' }).getByRole('button', { name: 'Delete tag' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Tagged one'))?.categories ?? []).toEqual([])
  await expect.poll(async () => (await one(page.request, 'E2E Tagged two'))?.categories).toEqual(['keep'])
})

test('bulk tags and priority, Delete completed, and Skip on a repeating task', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  for (const s of ['E2E Many 1', 'E2E Many 2']) await mk(page.request, { summary: s, due: day(0) })
  const weekly = await mk(page.request, { summary: 'E2E Weekly skip', due: day(1), recurrence: 'FREQ=WEEKLY' })
  await open(page, '/tasks/today')
  await expect(row(page, 'E2E Many')).toHaveCount(2)
  for (let i = 0; i < 2; i++) {
    await page.keyboard.press('j')
    await page.keyboard.press('x')
  }
  const bar = page.getByTestId('tasks-bulk')
  await expect(bar).toContainText('2 selected')
  const many = async (k: 'categories' | 'priority') => (await all(page.request)).filter((t) => t.summary.startsWith('E2E Many')).map((t) => JSON.stringify(t[k] ?? null))

  // ACT task.bulk_tag
  await bar.getByRole('button', { name: 'Tags' }).click()
  const tagInput = page.getByRole('textbox', { name: 'Add a tag' })
  await tagInput.fill('batch')
  await tagInput.press('Enter')
  await expect.poll(() => many('categories')).toEqual(['["batch"]', '["batch"]'])

  // ACT task.bulk_priority (the batch ends the selection; select again)
  await open(page, '/tasks/today')
  await expect(row(page, 'E2E Many')).toHaveCount(2)
  for (let i = 0; i < 2; i++) {
    await page.keyboard.press('j')
    await page.keyboard.press('x')
  }
  await expect(bar).toContainText('2 selected')
  await bar.getByRole('button', { name: 'Priority' }).click()
  await item(page, 'High').click()
  await expect.poll(() => many('priority')).toEqual(['1', '1'])

  // ACT task.skip: the row menu's "Skip this occurrence" moves the due a week on.
  await page.keyboard.press('Escape')
  await open(page, '/tasks/all')
  await row(page, 'E2E Weekly skip').hover()
  await row(page, 'E2E Weekly skip').getByRole('button', { name: 'More actions for E2E Weekly skip' }).click()
  await item(page, 'Skip this occurrence').click()
  await expect.poll(async () => (await one(page.request, 'E2E Weekly skip'))?.due).toBe(day(8))
  expect((await one(page.request, 'E2E Weekly skip'))?.uid).toBe(weekly.uid)

  // ACT task.clear_completed: in Completed, confirmed, no undo.
  const done = await mk(page.request, { summary: 'E2E Done long ago' })
  const p = await page.request.patch(`/api/v1/tasks/${encodeURIComponent(done.uid)}`, { data: { completed: true } })
  expect(p.ok()).toBeTruthy()
  await open(page, '/tasks/completed')
  await expect(row(page, 'E2E Done long ago')).toBeVisible()
  await page.getByRole('button', { name: 'More list actions' }).click()
  await item(page, 'Delete completed…').click()
  await page.getByRole('dialog', { name: /^Delete \d+ completed task/ }).getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => !!(await one(page.request, 'E2E Done long ago'))).toBe(false)
})

test('the pane: complete and reopen, title, repeat, reminder and reply', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const source = await conv(page.request, 'E2E Reply to me')
  const t = await mk(page.request, { summary: 'E2E Pane fields', due: day(2), conversation_id: source.id })
  await page.goto(`/tasks/all/task/${encodeURIComponent(t.uid)}`)
  const pane = page.getByTestId('task-pane')
  await expect(pane).toBeVisible()

  // ACT task.complete, then ACT task.reopen, from the pane's ring.
  await pane.getByRole('checkbox', { name: 'Complete' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Pane fields'))?.status).toBe('completed')
  await pane.getByRole('checkbox', { name: 'Reopen' }).click()
  await expect.poll(async () => (await one(page.request, 'E2E Pane fields'))?.status).toBe('needs-action')

  // ACT task.edit: the title saves as it changes.
  await pane.getByRole('textbox', { name: 'Task title' }).fill('E2E Pane fields edited')
  await expect.poll(async () => !!(await one(page.request, 'E2E Pane fields edited'))).toBe(true)

  // ACT task.set_repeat
  await pane.getByTestId('pane-repeat').click()
  await item(page, 'Daily').click()
  await expect.poll(async () => (await one(page.request, 'E2E Pane fields edited'))?.rrule).toBe('FREQ=DAILY')

  // ACT task.remind_at
  await pane.getByRole('button', { name: 'Remind me' }).click()
  await item(page, '1 day before at 9:00').click()
  await expect.poll(async () => (await one(page.request, 'E2E Pane fields edited'))?.reminders ?? []).toEqual([1440 - 540])

  // ACT task.reply_email: a reply to the source email opens in compose.
  await pane.getByRole('button', { name: 'Reply' }).click()
  const win = page.locator('.compose-win')
  await expect(win).toBeVisible()
  await expect(win.getByLabel('Subject')).toHaveValue(/E2E Reply to me/)
})

test('the pane shows reminders at a set time, removes one, and adds one with At a time…', async ({ page, seeded }) => {
  // ACT task.remind_at: absolute reminders (reminders_at), also days after the due (10 Oct 2026: the
  // pane showed only "Remind me" while the task had one).
  await connectCalendar(page.request, seeded)
  const at = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); d.setHours(6, 0, 0, 0); return d.toISOString().replace('.000Z', '+00:00') }
  const t = await mk(page.request, { summary: 'E2E Absolute reminders', due: `${day(-2)}T08:00`, reminders_at: [at(3), at(4)] })
  await page.goto(`/tasks/all/task/${encodeURIComponent(t.uid)}`)
  const pane = page.getByTestId('task-pane')
  await expect(pane.getByTestId('remind-at')).toHaveCount(2)
  await expect(pane.getByTestId('remind-at').first()).toContainText(/6:00|06:00/)
  await pane.getByTestId('remind-at').first().getByRole('button').click()
  await expect.poll(async () => (await one(page.request, 'E2E Absolute reminders'))?.reminders_at ?? []).toEqual([at(4)])
  await expect(pane.getByTestId('remind-at')).toHaveCount(1)

  await pane.getByRole('button', { name: 'Remind me' }).click()
  await item(page, 'At a time…').click()
  const when = page.getByTestId('when-picker')
  await expect(when).toBeVisible()
  await when.getByRole('textbox', { name: 'Type a date' }).fill('fri 3p')
  await expect(when.locator('.tk-when-result')).toContainText('3:00 PM')
  await page.keyboard.press('Enter')
  await expect.poll(async () => ((await one(page.request, 'E2E Absolute reminders'))?.reminders_at ?? []).length).toBe(2)
  const added = ((await one(page.request, 'E2E Absolute reminders'))?.reminders_at ?? []).find((x) => x !== at(4))!
  expect(new Date(added).getDay() * 100 + new Date(added).getHours()).toBe(515)
  await expect(pane.getByTestId('remind-at')).toHaveCount(2)
})
