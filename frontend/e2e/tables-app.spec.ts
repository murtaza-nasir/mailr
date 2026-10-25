// ACT-SURFACES: web.table web.notes web.palette
// Tables on the web (web.table) and in the Notes app (web.notes), against the
// seeded user's own tables. Each test checks the result through the API.
// Covers: ACT table.create, ACT table.rename, ACT table.color, ACT table.pin, ACT table.label, ACT table.archive,
// ACT table.duplicate, ACT table.delete, ACT table.restore, ACT table.purge, ACT table.search, ACT table.view_edit,
// ACT table.view_save, ACT table.view_delete, ACT table.stats, ACT table.import_csv, ACT table.export_csv,
// ACT table.export_xlsx,
// ACT table.history, ACT table.restore_point, ACT table.link, ACT table.unlink, ACT column.add, ACT column.rename,
// ACT column.retype, ACT column.options, ACT column.config, ACT column.reorder, ACT column.set_primary, ACT column.delete,
// ACT column.restore, ACT row.add, ACT row.edit, ACT row.duplicate, ACT row.move, ACT row.delete, ACT row.restore,
// ACT row.history, ACT row.restore_version, ACT row.bulk_edit, ACT row.bulk_delete.
import { readFile } from 'node:fs/promises'
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { THEMES } from './matrix'

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'

interface Column { id: string; name: string; type: string; config: { options?: { id: string; name: string }[] } }
interface Table { id: string; name: string; color: string | null; version: number; primary_column: string; columns: Column[]; views: { id: string; name: string; spec: Record<string, unknown> }[]; state: { pinned: boolean; archived: boolean; labels: string[] }; counts: { rows: number } }
interface Row { id: string; cells: Record<string, unknown>; raw: Record<string, string>; version: number }

async function mkTable(request: APIRequestContext, body: Record<string, unknown>): Promise<Table> {
  const r = await request.post('/api/v1/tables', { data: body })
  expect(r.ok(), await r.text()).toBeTruthy()
  return ((await r.json()) as { table: Table }).table
}
async function getTable(request: APIRequestContext, id: string): Promise<Table> {
  const r = await request.get(`/api/v1/tables/${id}`)
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as Table
}
async function rows(request: APIRequestContext, id: string, params: Record<string, string> = {}): Promise<Row[]> {
  return ((await (await request.get(`/api/v1/tables/${id}/rows`, { params })).json()) as { rows: Row[] }).rows
}
async function addRows(request: APIRequestContext, id: string, list: Record<string, unknown>[]): Promise<Row[]> {
  const r = await request.post(`/api/v1/tables/${id}/rows`, { data: { rows: list.map((cells) => ({ cells })), parse: true } })
  expect(r.ok(), await r.text()).toBeTruthy()
  return ((await r.json()) as { rows: Row[] }).rows
}
const colId = (t: Table, name: string) => t.columns.find((c) => c.name === name)!.id
const optId = (t: Table, col: string, name: string) => t.columns.find((c) => c.name === col)!.config.options!.find((o) => o.name === name)!.id

/** A workout log with four rows: Item (text, primary), Exercise (select), Weight (number), Done (checkbox). */
async function workout(request: APIRequestContext) {
  const t = await mkTable(request, {
    name: 'E2E Workouts', columns: [
      { name: 'Item', type: 'text' }, { name: 'Exercise', type: 'select', options: ['Squat', 'Bench'] },
      { name: 'Weight', type: 'number' }, { name: 'Done', type: 'checkbox' },
    ],
  })
  const c = (n: string) => colId(t, n)
  await addRows(request, t.id, [
    { [c('Item')]: 'Mon', [c('Exercise')]: 'Squat', [c('Weight')]: '80', [c('Done')]: 'yes' },
    { [c('Item')]: 'Tue', [c('Exercise')]: 'Bench', [c('Weight')]: '60' },
    { [c('Item')]: 'Wed', [c('Exercise')]: 'Squat', [c('Weight')]: '85' },
  ])
  return getTable(request, t.id)
}

async function openTable(page: Page, id: string, theme: Theme = 'classic', path = '') {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  await page.goto(`/tables/t/${id}${path}`)
  await expect(page.getByTestId('table-grid')).toBeVisible()
}
const cell = (page: Page, r: number, c: number) => page.locator(`[data-testid="table-grid"] [data-r="${r}"][data-c="${c}"]`)
const values = async (page: Page, c: number) => page.locator(`[data-testid="table-grid"] [data-c="${c}"]`).allInnerTexts()

/** Paste text into the focused element, as the browser does (a ClipboardEvent with text/plain). */
async function paste(page: Page, text: string) {
  await page.evaluate((t) => {
    const dt = new DataTransfer()
    dt.setData('text/plain', t)
    document.activeElement?.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, text)
}

test('a new table from the Notes app: the column builder, the card in the grid, pin, labels, Trash', async ({ page }) => {
  await page.goto('/notes')
  await expect(page.getByTestId('notes-app')).toBeVisible()
  // ACT table.create @web.notes: the capture bar's New table opens the column builder.
  await page.getByRole('button', { name: 'New table' }).click()
  const builder = page.getByTestId('new-table')
  await builder.getByRole('textbox', { name: 'Table name' }).fill('E2E Filament')
  await builder.getByRole('button', { name: 'Inventory' }).click()
  await page.getByTestId('new-table-create').click()
  await expect(page.getByTestId('table-grid')).toBeVisible()
  await expect(page.locator('.tb-head-cell').filter({ hasText: 'Location' })).toBeVisible()
  const t = ((await (await page.request.get('/api/v1/tables')).json()) as { tables: Table[] }).tables.find((x) => x.name === 'E2E Filament')!
  expect((await getTable(page.request, t.id)).columns.map((c) => c.name)).toEqual(['Item', 'Qty', 'Location'])

  // The card shows in the Notes grid and the Tables view.
  await page.goto('/notes/tables')
  const card = page.locator('.nt-card[data-kind="table"]').filter({ hasText: 'E2E Filament' })
  await expect(card).toBeVisible()
  // ACT table.pin @web.notes, ACT table.archive @web.notes
  await card.hover()
  await card.getByRole('button', { name: 'Pin' }).click()
  await expect.poll(async () => (await getTable(page.request, t.id)).state.pinned).toBe(true)
  // ACT table.label @web.notes: the labels menu creates a label.
  await card.hover()
  await card.getByRole('button', { name: 'Labels' }).click()
  await page.getByPlaceholder(/label/i).fill('Hobby')
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await getTable(page.request, t.id)).state.labels).toEqual(['Hobby'])
  await page.keyboard.press('Escape')
  // ACT table.delete @web.notes, ACT table.restore @web.notes, ACT table.purge @web.notes
  await card.hover()
  await card.getByRole('button', { name: 'More' }).click()
  await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
  await page.goto('/notes/trash')
  const trashed = page.locator('.nt-card[data-kind="table"]').filter({ hasText: 'E2E Filament' })
  await trashed.hover()
  await trashed.getByRole('button', { name: 'Restore' }).click()
  await expect(trashed).toHaveCount(0)
  expect((await getTable(page.request, t.id)).state.labels).toEqual(['Hobby'])
  await page.request.delete(`/api/v1/tables/${t.id}`)
  await page.reload()
  await trashed.hover()
  await trashed.getByRole('button', { name: 'Delete forever' }).click()
  await page.getByRole('button', { name: 'Delete forever' }).last().click()
  await expect.poll(async () => (await page.request.get(`/api/v1/tables/${t.id}`)).status()).toBe(404)
})

test('the palette makes a table and goes to one; g b opens Tables', async ({ page }) => {
  await page.goto('/notes')
  await expect(page.getByTestId('notes-app')).toBeVisible()
  // ACT table.create @web.palette
  await page.keyboard.press('Control+k')
  await page.keyboard.type('New table: E2E Expenses')
  await page.getByRole('option', { name: /New table: E2E Expenses/ }).click()
  await expect(page.getByTestId('table-grid')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Table name' })).toHaveValue('E2E Expenses')
  await page.goto('/notes')
  await expect(page.locator('.nt-card[data-kind="table"]')).toHaveCount(1)
  await page.keyboard.press('Control+k')
  await page.keyboard.type('E2E Expe')
  await page.getByRole('option', { name: /Go to E2E Expenses/ }).click()
  await expect(page.getByRole('textbox', { name: 'Table name' })).toHaveValue('E2E Expenses')
  await page.goto('/notes')
  await expect(page.locator('.nt-card[data-kind="table"]')).toHaveCount(1)
  await page.keyboard.press('g')
  await page.keyboard.press('b')
  await expect(page).toHaveURL(/\/notes\/tables$/)
})

test('grid keys: typing edits, Enter and Tab move, Delete clears, Space checks, select menu creates an option, undo', async ({ page }) => {
  const t = await workout(page.request)
  await openTable(page, t.id)
  // ACT row.edit @web.table: typing starts the edit; Enter saves and moves down.
  await cell(page, 0, 2).click()
  await page.keyboard.type('82.5')
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[colId(t, 'Weight')]).toBe('82.5')
  await expect(cell(page, 1, 2)).toHaveClass(/focused/)
  // Tab saves and moves right; text that is not a number is kept with a warning mark.
  await page.keyboard.press('Enter')
  await page.keyboard.press('Control+a')
  await page.keyboard.type('about 60')
  await page.keyboard.press('Tab')
  await expect(cell(page, 1, 2)).toHaveClass(/raw/)
  await expect(cell(page, 1, 2)).toHaveAttribute('title', "Not a number: 'about 60'")
  await expect(cell(page, 1, 3)).toHaveClass(/focused/)
  // Space toggles a checkbox.
  await page.keyboard.press('Space')
  await expect.poll(async () => (await rows(page.request, t.id))[1].cells[colId(t, 'Done')]).toBe(true)
  // ACT column.options @web.table (a new option from the select menu)
  await cell(page, 2, 1).click()
  await page.keyboard.press('Enter')
  await page.getByRole('textbox', { name: 'Filter the options' }).fill('Deadlift')
  await page.getByRole('option', { name: /Create option/ }).click()
  await expect(cell(page, 2, 1)).toContainText('Deadlift')
  const t2 = await getTable(page.request, t.id)
  expect((await rows(page.request, t.id))[2].cells[colId(t, 'Exercise')]).toBe(optId(t2, 'Exercise', 'Deadlift'))
  // Delete clears; z undoes it.
  await cell(page, 0, 0).click()
  await page.keyboard.press('Delete')
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[colId(t, 'Item')]).toBeUndefined()
  await page.keyboard.press('z')
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[colId(t, 'Item')]).toBe('Mon')
})

test('ranges: copy and paste as TSV adds rows past the end, fill down, select and delete rows, move a row', async ({ page }) => {
  const t = await workout(page.request)
  await openTable(page, t.id)
  // ACT row.bulk_edit @web.table: a paste of a range from the focused cell; the extra line becomes a row.
  await cell(page, 1, 0).click()
  await paste(page, 'Thu\tSquat\t90\nFri\tBench\t65\nSat\tSquat\t95')
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(4)
  const after = await rows(page.request, t.id)
  expect(after.map((r) => r.cells[colId(t, 'Item')])).toEqual(['Mon', 'Thu', 'Fri', 'Sat'])
  // Ctrl+D fills the first row of the range down.
  await cell(page, 0, 2).click()
  await page.keyboard.press('Shift+ArrowDown')
  await page.keyboard.press('Shift+ArrowDown')
  await page.keyboard.press('Control+d')
  await expect.poll(async () => (await rows(page.request, t.id)).slice(0, 3).map((r) => r.cells[colId(t, 'Weight')])).toEqual(['80', '80', '80'])
  // ACT row.move @web.table: Alt+↓ in the manual order.
  await cell(page, 0, 0).click()
  await page.keyboard.press('Alt+ArrowDown')
  await expect.poll(async () => (await rows(page.request, t.id)).map((r) => r.cells[colId(t, 'Item')])).toEqual(['Thu', 'Mon', 'Fri', 'Sat'])
  // Shift+Enter adds a row below.
  await page.keyboard.press('Shift+Enter')
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(5)
  // The new row shows below Mon with the focus on it.
  await expect(cell(page, 2, 0)).toHaveClass(/focused/)
  await expect(cell(page, 3, 0)).toHaveText('Fri')
  // ACT row.bulk_delete @web.table: x selects rows, Ctrl+Shift+Backspace deletes them, one undo brings them back.
  await cell(page, 3, 0).click()
  await page.keyboard.press('x')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('x')
  // Both rows show as selected before the delete: under load the keys outran the redraw and the
  // delete took one row (ship run of 8 Oct 2026).
  await expect(page.locator('.tb-row.selected')).toHaveCount(2)
  await page.keyboard.press('Control+Shift+Backspace')
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(3)
  await page.getByRole('button', { name: 'Undo', exact: true }).last().click()
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(5)
  // ACT row.duplicate @web.table, ACT row.delete @web.table: the cell menu.
  await cell(page, 0, 0).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Duplicate row' }).click()
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(6)
  await cell(page, 0, 0).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Delete row' }).click()
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(5)
})

test('the focus stays on its row while a move waits for the refetch, and when rows come in above it', async ({ page }) => {
  const t = await workout(page.request)
  await openTable(page, t.id)
  const item = colId(t, 'Item')
  // The refetch after the move is held back: the grid still draws Mon, Tue, Wed while the server
  // has Tue, Mon, Wed. A Shift+Enter in that window once added the row below Tue, the row the
  // focus position pointed at (tables-app e2e, integ run of 10 Oct 2026).
  let release = () => {}
  const held = new Promise<void>((resolve) => { release = resolve })
  let hold = false
  await page.route((u) => u.pathname === `/api/v1/tables/${t.id}/rows`, async (route) => {
    if (hold && route.request().method() === 'GET') await held
    await route.fallback()
  })
  await cell(page, 0, 0).click()
  hold = true
  const moved = page.waitForResponse((r) => r.url().endsWith('/move') && r.request().method() === 'POST')
  await page.keyboard.press('Alt+ArrowDown')
  expect((await moved).ok()).toBeTruthy()
  await expect.poll(async () => (await rows(page.request, t.id)).map((r) => r.cells[item])).toEqual(['Tue', 'Mon', 'Wed'])
  // ACT row.move @web.table, ACT row.add @web.table: the focus is still on Mon, where the grid draws it.
  await expect(cell(page, 0, 0)).toHaveText('Mon')
  await expect(cell(page, 0, 0)).toHaveClass(/focused/)
  const added = page.waitForResponse((r) => /\/rows$/.test(r.url()) && r.request().method() === 'POST')
  await page.keyboard.press('Shift+Enter')
  expect((await added).ok()).toBeTruthy()
  hold = false
  release()
  await expect.poll(async () => (await rows(page.request, t.id)).map((r) => r.cells[item] ?? '')).toEqual(['Tue', 'Mon', '', 'Wed'])
  await expect(cell(page, 1, 0)).toHaveText('Mon')
  await expect(cell(page, 2, 0)).toHaveClass(/focused/)
  // Another writer puts a row at the top (the change stream reloads the grid): the focus moves
  // down with Tue.
  await cell(page, 0, 0).click()
  await expect(cell(page, 0, 0)).toHaveText('Tue')
  const first = (await rows(page.request, t.id))[0].id
  const r = await page.request.post(`/api/v1/tables/${t.id}/rows`, { data: { cells: { [item]: 'Sun' }, before: first } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await expect(cell(page, 0, 0)).toHaveText('Sun')
  await expect(cell(page, 1, 0)).toHaveText('Tue')
  await expect(cell(page, 1, 0)).toHaveClass(/focused/)
  // The other writer deletes the focused row: the row now in its place takes the focus.
  const tue = (await rows(page.request, t.id))[1].id
  expect((await page.request.delete(`/api/v1/tables/${t.id}/rows/${tue}`)).ok()).toBeTruthy()
  await expect(cell(page, 1, 0)).toHaveText('Mon')
  await expect(cell(page, 1, 0)).toHaveClass(/focused/)
  await page.keyboard.press('ArrowDown')
  await expect(cell(page, 2, 0)).toHaveClass(/focused/)
})

test('views: sort, filter chips, group, footers, save as view, delete view', async ({ page }) => {
  const t = await workout(page.request)
  await openTable(page, t.id)
  // ACT table.view_edit @web.table: the column menu sorts; the change is unsaved until Save.
  await page.locator('.tb-head-cell').filter({ hasText: 'Weight' }).getByRole('button', { name: /menu/ }).click()
  await page.getByRole('menuitem', { name: 'Sort descending' }).click()
  await expect.poll(async () => values(page, 0)).toEqual(['Wed', 'Mon', 'Tue'])
  await expect(page.getByText('Unsaved changes')).toBeVisible()
  // A filter from the view bar shows as a chip; × clears it.
  await page.getByTestId('view-filter').click()
  await page.getByRole('button', { name: 'Add a filter' }).click()
  await page.getByRole('combobox', { name: 'Column' }).last().selectOption({ label: 'Exercise' })
  await page.locator('.tb-opt-pick').filter({ hasText: 'Squat' }).click()
  await page.keyboard.press('Escape')
  await expect(page.locator('.tb-fchip')).toContainText('Exercise is Squat')
  await expect.poll(async () => values(page, 0)).toEqual(['Wed', 'Mon'])
  // ACT table.stats @web.table: a footer sum.
  await page.getByRole('button', { name: 'Footer of Weight' }).click()
  await page.getByRole('menuitemradio', { name: 'Sum' }).click()
  await expect(page.getByRole('button', { name: 'Footer of Weight' })).toContainText('Sum 165')
  // ACT table.view_save @web.table: Save as view, then the view is a tab.
  await page.getByRole('button', { name: 'Save as view' }).click()
  await page.getByRole('textbox', { name: 'View name' }).fill('Squats')
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Squats' })).toHaveAttribute('aria-selected', 'true')
  const saved = (await getTable(page.request, t.id)).views.find((v) => v.name === 'Squats')!
  expect(saved.spec.footers).toEqual({ [colId(t, 'Weight')]: 'sum' })
  // Group by the select column.
  await page.getByRole('tab', { name: 'All' }).click()
  await page.getByRole('button', { name: 'Group' }).click()
  await page.getByRole('menuitemradio', { name: 'Exercise' }).click()
  await expect(page.locator('.tb-group')).toHaveCount(2)
  await page.getByRole('button', { name: 'Reset' }).click()
  // ACT table.view_delete @web.table
  await page.getByRole('tab', { name: 'Squats' }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Delete view' }).click()
  await expect.poll(async () => (await getTable(page.request, t.id)).views.map((v) => v.name)).toEqual(['All'])
  // A view with no matching rows says so.
  await page.request.post(`/api/v1/tables/${t.id}/views`, { data: { name: 'None', spec: { filters: [{ col: colId(t, 'Item'), op: 'is', value: 'nobody' }] } } })
  await page.reload()
  await page.getByRole('tab', { name: 'None' }).click()
  await expect(page.getByText('No rows match these filters')).toBeVisible()
})

test('columns: add, rename, retype with the preview, options, settings, reorder, primary, delete and restore', async ({ page }) => {
  const t = await workout(page.request)
  await openTable(page, t.id)
  // ACT column.add @web.table
  await page.getByRole('button', { name: 'Add a column' }).click()
  await page.getByRole('textbox', { name: 'Column name' }).fill('Note')
  await page.getByTestId('column-dialog-save').click()
  await expect(page.locator('.tb-head-cell').filter({ hasText: 'Note' })).toBeVisible()
  const menu = (name: string) => page.locator('.tb-head-cell').filter({ hasText: name }).getByRole('button', { name: /menu/ })
  // ACT column.rename @web.table
  await menu('Note').click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await page.getByRole('textbox', { name: 'Column name' }).fill('Comment')
  await page.getByTestId('column-dialog-save').click()
  await expect.poll(async () => (await getTable(page.request, t.id)).columns.map((c) => c.name)).toContain('Comment')
  // ACT column.retype @web.table: Weight to text and back shows the preview.
  await menu('Weight').click()
  await page.getByRole('menuitem', { name: 'Change type' }).click()
  await page.getByRole('radio', { name: 'Text' }).click()
  await expect(page.getByTestId('retype-preview')).toContainText('3 cells convert, 0 do not')
  await page.getByTestId('column-dialog-save').click()
  await expect.poll(async () => (await getTable(page.request, t.id)).columns.find((c) => c.name === 'Weight')!.type).toBe('text')
  await page.getByRole('button', { name: 'Undo', exact: true }).last().click()
  await expect.poll(async () => (await getTable(page.request, t.id)).columns.find((c) => c.name === 'Weight')!.type).toBe('number')
  // The grid shows the restored type once its refetch lands; the settings follow the shown column.
  await expect(cell(page, 0, 2)).toHaveClass(/tb-c-number/)
  // ACT column.config @web.table
  await menu('Weight').click()
  await page.getByRole('menuitem', { name: 'Settings' }).click()
  await page.getByRole('combobox', { name: 'Decimals' }).selectOption('1')
  await page.getByTestId('column-dialog-save').click()
  await expect(cell(page, 0, 2)).toHaveText('80.0')
  // ACT column.reorder @web.table (drag a header onto another one)
  await page.locator('.tb-head-cell').filter({ hasText: 'Done' }).dragTo(page.locator('.tb-head-cell').filter({ hasText: 'Exercise' }), { targetPosition: { x: 4, y: 10 } })
  await expect.poll(async () => (await getTable(page.request, t.id)).columns.map((c) => c.name).slice(0, 3)).toEqual(['Item', 'Done', 'Exercise'])
  // ACT column.set_primary @web.table
  await menu('Exercise').click()
  await page.getByRole('menuitem', { name: 'Make primary' }).click()
  await expect.poll(async () => (await getTable(page.request, t.id)).primary_column).toBe(colId(t, 'Exercise'))
  // ACT column.delete @web.table, ACT column.restore @web.table
  await menu('Comment').click()
  await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
  await page.getByRole('button', { name: 'Delete column' }).click()
  await expect(page.locator('.tb-head-cell').filter({ hasText: 'Comment' })).toHaveCount(0)
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Deleted columns' }).click()
  await page.getByTestId('deleted-columns').getByRole('button', { name: 'Restore' }).click()
  await expect(page.locator('.tb-head-cell').filter({ hasText: 'Comment' })).toBeVisible()
})

for (const theme of THEMES) {
  test(`the row form in the ${theme} theme: fields, history, earlier values, restore`, async ({ page }) => {
    const t = await workout(page.request)
    const r0 = (await rows(page.request, t.id))[0]
    await page.request.patch(`/api/v1/tables/${t.id}/rows/${r0.id}`, { data: { cells: { [colId(t, 'Weight')]: '82' } } })
    await openTable(page, t.id, theme)
    // ACT row.history @web.table: Ctrl+Enter opens the row form where the theme opens details.
    await cell(page, 0, 0).click()
    await page.keyboard.press('Control+Enter')
    const form = page.getByTestId('row-form')
    await expect(form).toBeVisible()
    await expect(page).toHaveURL(new RegExp(`/tables/t/${t.id}/r/${r0.id}$`))
    // A field saves on its own.
    await form.locator('#f-' + colId(t, 'Item')).fill('Monday')
    await form.locator('#f-' + colId(t, 'Item')).press('Enter')
    await expect.poll(async () => (await rows(page.request, t.id))[0].cells[colId(t, 'Item')]).toBe('Monday')
    await form.getByRole('tab', { name: 'History' }).click()
    await expect(page.getByTestId('row-history')).toContainText('Weight 80 → 82')
    // ACT row.restore_version @web.table: Earlier values → Use this value.
    await form.getByRole('button', { name: 'Weight' }).click()
    await expect(page.getByTestId('earlier-values')).toContainText('80')
    await page.getByTestId('earlier-values').getByRole('button', { name: 'Use this value' }).first().click()
    await expect.poll(async () => (await rows(page.request, t.id))[0].cells[colId(t, 'Weight')]).toBe('80')
    await form.getByRole('button', { name: 'Close' }).click()
    await expect(form).toHaveCount(0)
  })
}

test('a row form field is editable only once it shows the stored value', async ({ page }) => {
  const t = await workout(page.request)
  const r0 = (await rows(page.request, t.id))[0]
  await openTable(page, t.id, 'focus')
  // The fields are read-only until the row loads. The render that makes one editable must already
  // show the stored text: when it came a step later (an effect), a fill or the first keys at that
  // moment landed before it ("Mon" + "Monday" = "MonMonday" under load). The row answers late here.
  await page.route(`**/api/v1/tables/${t.id}/rows/${r0.id}*`, async (route) => {
    if (route.request().method() === 'GET') await new Promise((r) => setTimeout(r, 1500))
    await route.fallback()
  })
  await page.evaluate((id) => {
    const w = window as unknown as { __seen: string[] }
    w.__seen = []
    // A mutation observer runs right after React's commit, before its passive effects: the state
    // a fill that arrives at that moment sees.
    new MutationObserver(() => {
      const el = document.getElementById(id) as HTMLInputElement | null
      if (el) w.__seen.push(`${el.disabled ? 'off' : 'on'}:${el.value}`)
    }).observe(document.body, { subtree: true, attributes: true, childList: true })
  }, 'f-' + colId(t, 'Item'))
  await cell(page, 0, 0).click()
  await page.keyboard.press('Control+Enter')
  const form = page.getByTestId('row-form')
  await expect(form.locator('#f-' + colId(t, 'Item'))).toHaveValue('Mon')
  const seen = await page.evaluate(() => (window as unknown as { __seen: string[] }).__seen)
  expect([...new Set(seen)]).not.toContain('on:')
})

test('history, Deleted rows and restore to a point in time', async ({ page }) => {
  const t = await workout(page.request)
  const all = await rows(page.request, t.id)
  await page.request.delete(`/api/v1/tables/${t.id}/rows/${all[1].id}`)
  await page.request.patch(`/api/v1/tables/${t.id}/rows/${all[0].id}`, { data: { cells: { [colId(t, 'Weight')]: '100' } } })
  await openTable(page, t.id)
  // ACT row.restore @web.table
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Deleted rows' }).click()
  await page.getByTestId('deleted-rows').getByText('Tue').click()
  await page.getByRole('button', { name: /^Restore/ }).click()
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(3)
  await page.getByRole('button', { name: 'Close', exact: true }).last().click()
  // ACT table.history @web.table: sessions, newest first.
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'History' }).click()
  await expect(page.getByTestId('table-history')).toContainText('restored 1 row')
  // ACT table.restore_point @web.table: back to before the first edit, with the preview.
  await page.getByTestId('table-history').getByRole('button', { name: 'Show the changes' }).first().click()
  await page.getByTestId('table-history').locator('li li').filter({ hasText: 'Weight 80 → 100' }).getByRole('button', { name: 'Restore to before this' }).click()
  await expect(page.getByTestId('restore-point-preview')).toContainText('1 row changes')
  await page.getByTestId('restore-point-confirm').click()
  await expect.poll(async () => (await rows(page.request, t.id)).find((r) => r.id === all[0].id)!.cells[colId(t, 'Weight')]).toBe('80')
})

test('CSV: import into a new table with the detected types, append, export the view', async ({ page }) => {
  await page.goto('/notes/tables')
  await page.getByRole('button', { name: 'New table', exact: true }).click()
  await page.getByRole('button', { name: 'Import CSV' }).click()
  // ACT table.import_csv @web.notes
  await page.getByRole('textbox', { name: 'CSV text' }).fill('Date,Item,Amount\n2026-10-01,Coffee,$3.50\n2026-10-02,Lunch,$12\n2026-10-03,Snack,about 2\n')
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('import-preview')).toBeVisible()
  await expect(page.getByTestId('import-type').nth(2)).toHaveText('Currency · 67%')
  await page.getByRole('textbox', { name: 'Table name' }).fill('E2E Spending')
  await page.getByTestId('import-run').click()
  await expect(page.getByText('Imported 3 rows · 1 cell kept as text')).toBeVisible()
  await expect(page.getByTestId('table-grid')).toBeVisible()
  const id = new URL(page.url()).pathname.split('/')[3]
  const t = await getTable(page.request, id)
  expect(t.columns.map((c) => c.type)).toEqual(['date', 'text', 'currency'])
  // ACT table.import_csv @web.table: append maps columns by name.
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Import CSV' }).click()
  await page.getByRole('textbox', { name: 'CSV text' }).fill('item,amount\nTea,2\n')
  await page.getByRole('button', { name: 'Preview' }).click()
  await page.getByTestId('import-run').click()
  await expect.poll(async () => (await rows(page.request, id)).length).toBe(4)
  // ACT table.export_csv @web.table
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Export CSV' }).click()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('E2E Spending.csv')
  // ACT table.export_xlsx @web.table
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Export to Excel (.xlsx)' }).click()
  await expect(page.getByRole('dialog', { name: 'Export to Excel' })).toBeVisible()
  const xlsx = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  const file = await xlsx
  expect(file.suggestedFilename()).toBe('E2E Spending.xlsx')
  // An .xlsx file is a zip archive: it starts with "PK".
  const bytes = await readFile(await file.path())
  expect(bytes.subarray(0, 2).toString()).toBe('PK')
})

test('header: rename, colour, link to an email, duplicate, delete with a CSV copy offered', async ({ page, seeded }) => {
  const t = await workout(page.request)
  await openTable(page, t.id)
  // ACT table.rename @web.table
  await page.getByRole('textbox', { name: 'Table name' }).fill('E2E Lifts')
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await getTable(page.request, t.id)).name).toBe('E2E Lifts')
  // ACT table.color @web.table
  await page.getByRole('button', { name: 'Colour' }).click()
  await page.getByRole('radio', { name: 'Sage' }).click()
  await expect.poll(async () => (await getTable(page.request, t.id)).color).toBe('sage')
  // ACT table.link @web.table, ACT table.unlink @web.table
  const cid = seeded.conversations['E2E Read me']
  await page.getByRole('button', { name: 'Add a link' }).click()
  // The picker searches; a pasted mailr address is its first result.
  await page.getByRole('combobox', { name: 'Search for something to link' }).fill(`http://localhost/mail/inbox/${cid}`)
  await page.getByRole('combobox', { name: 'Search for something to link' }).press('Enter')
  await expect(page.locator('.nt-link-chip')).toContainText('E2E Read me')
  await page.locator('.nt-link-chip').getByRole('button', { name: /Remove link/ }).click()
  await expect(page.locator('.nt-link-chip')).toHaveCount(0)
  // ACT table.duplicate @web.table
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Duplicate' }).click()
  await expect(page.getByRole('textbox', { name: 'Table name' })).toHaveValue('E2E Lifts (copy)')
  // ACT table.delete @web.table
  await page.getByTestId('table-more').click()
  await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save a CSV copy first' })).toBeVisible()
  await page.getByRole('button', { name: 'Move to Trash' }).click()
  await expect(page).toHaveURL(/\/notes\/tables$/)
})

test('a phone: the card list, a new row with Save and add another, the full-screen row form', async ({ page }) => {
  const t = await workout(page.request)
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto(`/tables/t/${t.id}`)
  const list = page.getByTestId('table-phone')
  await expect(list.locator('.tb-phone-row')).toHaveCount(3)
  await expect(list.locator('.tb-phone-row').first()).toContainText('Mon')
  // ACT row.add @web.table: + opens the empty form; Save and add another keeps it open.
  await page.getByRole('button', { name: 'New row' }).click()
  const form = page.getByTestId('row-form')
  await form.locator('#f-' + colId(t, 'Item')).fill('Thu')
  await form.locator('#f-' + colId(t, 'Item')).blur()
  await form.getByRole('button', { name: 'Save and add another' }).click()
  await expect.poll(async () => (await rows(page.request, t.id)).length).toBe(4)
  await expect(form).toBeVisible()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await list.locator('.tb-phone-row').filter({ hasText: 'Tue' }).click()
  await expect(page.getByTestId('row-form')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('a conflict on a cell shows Keep theirs and Use mine', async ({ page }) => {
  const t = await workout(page.request)
  // No live events: this tab keeps the version it read, as a tab that has not heard of the change yet.
  await page.route('**/api/v1/events**', (r) => r.abort())
  await openTable(page, t.id)
  const r0 = (await rows(page.request, t.id))[0]
  // Someone else changes the cell after this tab read it.
  await page.request.patch(`/api/v1/tables/${t.id}/rows/${r0.id}`, { data: { cells: { [colId(t, 'Weight')]: '99' }, base_version: r0.version } })
  await cell(page, 0, 2).click()
  await page.keyboard.type('70')
  await page.keyboard.press('Enter')
  await expect(page.locator('.tb-conflict')).toContainText('Changed by someone else: 99')
  await page.getByRole('button', { name: 'Use mine' }).click()
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[colId(t, 'Weight')]).toBe('70')
})
