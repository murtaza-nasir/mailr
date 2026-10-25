// ACT-SURFACES: web.notes web.note web.table
// Reminders and templates of notes on the web:
// ACT note.remind from the editor's toolbar and a card (the chip, the Reminders view, × with Undo),
// ACT note.from_template (the gallery: a starter list opens with its sections), ACT note.save_template
// and ACT note.delete_template (with Undo), ACT table.save_template from a table's ⋮ menu, one Undo for
// a batch on several selected cards, the order of the items above the sections
// (ACT checksection.order_mode from the editor's ⋮), and the owner's people dialog listing who left.
// Each test checks the result through the API.
import type { APIRequestContext, Browser, Page } from '@playwright/test'
import { accessTest, expect, type SeedAccount, shot, signIn, test } from './fixtures'
import { getNote, mkNote, openApp } from './helpers'

const BASE = process.env.E2E_BASE ?? 'http://localhost:5175'

interface Note {
  id: string; kind: string; title: string; top_order_mode?: string
  state: { pinned: boolean; archived: boolean; labels: string[] }
  reminders?: { key: string; at: string; state: string }[]
  sections?: { id: string; name: string }[]; items?: { id: string; text: string; checked: boolean }[]
  deleted_at: string | null
}

const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkNote<Note>(request, body)
const get = (request: APIRequestContext, id: string) => getNote<Note>(request, id)
async function list(request: APIRequestContext, view = 'all'): Promise<Note[]> {
  return ((await (await request.get('/api/v1/notes', { params: { view } })).json()) as { notes: Note[] }).notes
}
async function templates(request: APIRequestContext): Promise<{ id: string; name: string; builtin: boolean; kind: string }[]> {
  return ((await (await request.get('/api/v1/notes/templates')).json()) as { templates: { id: string; name: string; builtin: boolean; kind: string }[] }).templates
}

const open = (page: Page, path = '/notes') => openApp(page, path, { theme: 'classic', ready: page.getByTestId('notes-app') })
const card = (page: Page, text: string) => page.locator('.nt-card').filter({ hasText: text })
const editor = (page: Page) => page.getByTestId('note-editor')

test('a reminder on a note: set from the editor and a card, listed under Reminders, removed with Undo', async ({ page }) => {
  const n = await mk(page.request, { kind: 'text', title: 'E2E Renew passport', body: 'Photos first' })
  await mk(page.request, { kind: 'text', title: 'E2E No reminder' })
  await open(page, `/notes/n/${n.id}`)
  // ACT note.remind @web.note: Reminder → Tomorrow.
  await editor(page).getByRole('button', { name: 'Reminder' }).click()
  await page.getByRole('menuitem', { name: /^Tomorrow/ }).click()
  await expect(editor(page).getByTestId('note-reminders')).toContainText('Tomorrow, ')
  await expect.poll(async () => (await get(page.request, n.id)).reminders?.length).toBe(1)
  const key = (await get(page.request, n.id)).reminders![0].key
  expect(key).toMatch(new RegExp(`^n\\|${n.id}\\|abs\\|\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\+00:00$`))
  await page.screenshot({ path: shot('notes-reminder-editor.png') })
  await editor(page).getByRole('button', { name: 'Close', exact: true }).click()
  await expect(card(page, 'E2E Renew passport').getByTestId('card-reminder')).toContainText('Tomorrow')
  // The Reminders view: only the note with a reminder.
  await page.getByTestId('notes-nav-reminders').click()
  await expect(page).toHaveURL(/\/notes\/reminders$/)
  await expect(card(page, 'E2E Renew passport')).toBeVisible()
  await expect(card(page, 'E2E No reminder')).toHaveCount(0)
  // ACT note.remind @web.notes: a card's Remind me → Pick a date and time.
  await page.getByTestId('notes-sidebar').getByRole('button', { name: 'Notes', exact: true }).click()
  await card(page, 'E2E No reminder').hover()
  await card(page, 'E2E No reminder').getByRole('button', { name: 'Remind me' }).click()
  await page.getByRole('menuitem', { name: 'Pick a date and time…' }).click()
  await page.getByLabel('Remind me at').fill('2031-05-04T08:30')
  await page.getByRole('button', { name: 'Set reminder' }).click()
  await expect(card(page, 'E2E No reminder').getByTestId('card-reminder')).toContainText('4 May')
  // × on the editor's chip removes it; Undo puts it back.
  await card(page, 'E2E Renew passport').click()
  await editor(page).getByRole('button', { name: /^Remove the reminder/ }).click()
  await expect(editor(page).getByTestId('note-reminders')).toHaveCount(0)
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
  await expect(editor(page).getByTestId('note-reminders')).toContainText('Tomorrow')
  await expect.poll(async () => (await get(page.request, n.id)).reminders?.map((r) => r.key)).toEqual([key])
})

test('templates: a starter list from the gallery, a note saved as a template and deleted with Undo', async ({ page }) => {
  await open(page)
  // ACT note.from_template @web.notes: the capture bar's "New from template".
  await page.getByTestId('notes-from-template').click()
  const gallery = page.getByTestId('templates-dialog')
  await expect(gallery.getByRole('region', { name: 'Starters' })).toContainText('Shopping list')
  await page.screenshot({ path: shot('notes-templates.png') })
  await gallery.getByRole('button', { name: 'New list from Packing list' }).click()
  await expect(editor(page)).toBeVisible()
  await expect(editor(page).locator('.nt-section-name')).toContainText(['Documents', 'Clothes', 'Toiletries', 'Electronics'])
  const packing = (await list(page.request, 'lists')).find((n) => n.title === 'Packing')!
  expect((await get(page.request, packing.id)).items!.every((i) => !i.checked)).toBe(true)
  // ACT note.save_template @web.note: ⋮ → Save as template.
  await editor(page).getByRole('toolbar', { name: 'Note' }).getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Save as template' }).click()
  await page.getByLabel('Template name').fill('E2E Weekend trip')
  await page.getByRole('button', { name: 'Save template' }).click()
  await expect.poll(async () => (await templates(page.request)).map((t) => t.name)).toContain('E2E Weekend trip')
  await editor(page).getByRole('button', { name: 'Close', exact: true }).click()
  // ACT note.delete_template @web.notes: the gallery's Delete, then Undo.
  await page.getByTestId('notes-from-template').click()
  await expect(gallery.getByRole('region', { name: 'Your templates' })).toContainText('E2E Weekend trip')
  await gallery.getByRole('button', { name: 'Delete template E2E Weekend trip' }).click()
  await expect(gallery.getByRole('region', { name: 'Your templates' })).not.toContainText('E2E Weekend trip')
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
  await expect.poll(async () => (await templates(page.request)).map((t) => t.name)).toContain('E2E Weekend trip')
})

test('a table saved as a template; a new table from it has its columns and no rows', async ({ page }) => {
  const r = await page.request.post('/api/v1/tables', { data: { name: 'E2E Reading', columns: [{ name: 'Title', type: 'text' }, { name: 'Pages', type: 'number' }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const t = ((await r.json()) as { table: { id: string } }).table
  await page.goto(`/tables/t/${t.id}`)
  // ACT table.save_template @web.table
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Save as template' }).click()
  await page.getByLabel('Template name').fill('E2E Reading log')
  await page.getByRole('button', { name: 'Save template' }).click()
  await expect.poll(async () => (await templates(page.request)).find((x) => x.name === 'E2E Reading log')?.kind).toBe('table')
  const tpl = (await templates(page.request)).find((x) => x.name === 'E2E Reading log')!
  const made = await page.request.post('/api/v1/notes/from-template', { data: { template: tpl.id } })
  expect(made.ok(), await made.text()).toBeTruthy()
  const table = ((await made.json()) as { table: { id: string; columns: { name: string }[] } }).table
  expect(table.columns.map((c) => c.name)).toEqual(['Title', 'Pages'])
})

test('several selected cards: one toast and one Undo for the batch', async ({ page }) => {
  await mk(page.request, { kind: 'text', title: 'E2E Batch one' })
  await mk(page.request, { kind: 'text', title: 'E2E Batch two' })
  await open(page)
  for (const t of ['E2E Batch one', 'E2E Batch two']) {
    await card(page, t).hover()
    await card(page, t).getByRole('button', { name: 'Select' }).click()
  }
  const bar = page.getByTestId('notes-bulk')
  await expect(bar).toContainText('2 selected')
  await bar.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText('2 items moved to Trash')).toBeVisible()
  await expect(card(page, 'E2E Batch one')).toHaveCount(0)
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
  await expect(card(page, 'E2E Batch one')).toBeVisible()
  await expect(card(page, 'E2E Batch two')).toBeVisible()
  expect((await list(page.request, 'trash')).filter((n) => n.title.startsWith('E2E Batch'))).toEqual([])
  // Pin both with one write each and one toast.
  for (const t of ['E2E Batch one', 'E2E Batch two']) {
    await card(page, t).hover()
    await card(page, t).getByRole('button', { name: 'Select' }).click()
  }
  await bar.getByRole('button', { name: 'Pin' }).click()
  await expect(page.getByText('2 items pinned')).toBeVisible()
  await expect.poll(async () => (await list(page.request)).filter((n) => n.title.startsWith('E2E Batch') && n.state.pinned).length).toBe(2)
})

test('the order of the items above the sections is chosen in the editor’s ⋮ menu', async ({ page }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Order', lines: 'milk\neggs\nCostco:\ntowels' })
  await open(page, `/notes/n/${n.id}`)
  // ACT checksection.order_mode @web.note
  await editor(page).getByRole('toolbar', { name: 'Note' }).getByRole('button', { name: 'More', exact: true }).click()
  await expect(page.getByText('Order of the items above the sections')).toBeVisible()
  await page.getByRole('menuitemradio', { name: /^Learned/ }).click()
  await expect.poll(async () => (await get(page.request, n.id)).top_order_mode).toBe('learned')
})

async function person(browser: Browser, who: SeedAccount): Promise<{ page: Page; request: APIRequestContext; close: () => Promise<void> }> {
  const ctx = await browser.newContext({ baseURL: BASE })
  const page = await ctx.newPage()
  await signIn(page, who)
  return { page, request: page.request, close: () => ctx.close() }
}

accessTest('the owner’s people dialog lists who removed a shared note, and shares it again', async ({ page, browser, access }) => {
  await signIn(page, access.admin)
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Left list', lines: 'tent' })
  expect((await page.request.put(`/api/v1/notes/${n.id}/shares/${access.family.id}`, { data: { level: 'edit' } })).ok()).toBeTruthy()
  const dana = await person(browser, access.family)
  try {
    expect((await dana.request.delete(`/api/v1/notes/${n.id}/shares/me`)).ok()).toBeTruthy()
  } finally {
    await dana.close()
  }
  await open(page, `/notes/n/${n.id}`)
  await editor(page).getByRole('button', { name: 'Share' }).click()
  const left = page.getByTestId('share-left')
  await expect(left).toContainText('E2E Family')
  await page.screenshot({ path: shot('notes-share-left.png') })
  await left.getByRole('button', { name: 'Share again with E2E Family' }).click()
  await expect(page.getByTestId('share-left')).toHaveCount(0)
  await expect(page.getByTestId('share-person')).toContainText('E2E Family')
})
