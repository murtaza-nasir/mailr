// ACT-SURFACES: web.notes web.note web.panel web.palette web.list web.reader web.settings
// The Notes app (web.notes, web.note) against the seeded user's own notes. Each
// test checks the result through the API.
// Covers: ACT note.create (capture bar, palette, side panel), ACT note.edit (autosave), ACT note.to_checklist,
// ACT note.to_text, ACT note.color, ACT note.pin, ACT note.archive, ACT note.label, ACT notelabel.create,
// ACT notelabel.rename, ACT notelabel.delete, ACT note.delete, ACT note.restore, ACT note.purge,
// ACT note.duplicate, ACT note.history, ACT note.restore_version, ACT note.add_items, ACT checkitem.add,
// ACT checkitem.check, ACT checkitem.uncheck, ACT checkitem.edit, ACT checkitem.delete, ACT checkitem.move,
// ACT checksection.create, ACT checksection.rename, ACT checksection.order_mode, ACT checksection.reorder,
// ACT checksection.delete, ACT note.clear_checked, ACT note.uncheck_all, ACT checkitem.usual_add,
// ACT checkitem.usual_dismiss, ACT checkitem.to_task, ACT note.search, ACT note.from_email, ACT note.export,
// ACT note.link, ACT note.unlink, ACT note.reorder.
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { THEMES } from './matrix'
import { getNote, mkNote, openApp } from './helpers'

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'

interface Item { id: string; text: string; checked: boolean; section_id: string | null; qty: { amount: string; unit: string | null } | null; version?: number; task_uid?: string | null }
interface Note {
  id: string; kind: 'text' | 'checklist'; title: string; body: string; color: string | null; version: number; deleted_at: string | null
  state: { pinned: boolean; archived: boolean; labels: string[] }
  sections?: { id: string; name: string; order_mode: string }[]; items?: Item[]; links?: { id: number; target_kind: string }[]
}

const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkNote<Note>(request, body)
const get = (request: APIRequestContext, id: string) => getNote<Note>(request, id)
async function list(request: APIRequestContext, view = 'all'): Promise<Note[]> {
  return ((await (await request.get('/api/v1/notes', { params: { view } })).json()) as { notes: Note[] }).notes
}
const item = (n: Note, text: string) => n.items?.find((i) => i.text === text)

const open = (page: Page, path = '/notes', theme: Theme = 'classic') => openApp(page, path, { theme, ready: page.getByTestId('notes-app') })
const card = (page: Page, text: string) => page.locator('.nt-card').filter({ hasText: text })
const editor = (page: Page) => page.getByTestId('note-editor')
const row = (page: Page, text: string) => editor(page).locator('.nt-row').filter({ has: page.locator(`input.nt-item-input[value="${text}"]`) })
/** An item's text field by the item's id (its value changes while the test types). */
async function field(page: Page, text: string) {
  const id = await row(page, text).first().getAttribute('data-item')
  return editor(page).locator(`.nt-row[data-item="${id}"] input.nt-item-input`)
}

/** Paste text into the focused field, as the browser does (a ClipboardEvent with text/plain). */
async function paste(page: Page, text: string) {
  await page.evaluate((t) => {
    const dt = new DataTransfer()
    dt.setData('text/plain', t)
    document.activeElement?.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, text)
}

test('the capture bar takes a note and a list; the editor autosaves; convert both ways', async ({ page }) => {
  await open(page)
  await expect(page.getByTestId('notes-empty')).toContainText('Notes you add show here')
  // ACT note.create @web.notes: "Take a note…", then a click outside saves.
  await page.getByRole('button', { name: 'Take a note…' }).click()
  await page.getByRole('textbox', { name: 'Title' }).fill('E2E Plan')
  await page.getByRole('textbox', { name: 'Note' }).fill('First line\nSecond line')
  await page.getByRole('heading', { name: 'Notes', exact: true }).click()
  await expect(card(page, 'E2E Plan')).toContainText('Second line')
  // New list: one item per line, read by the item grammar.
  await page.getByRole('button', { name: 'New list' }).click()
  await page.getByRole('textbox', { name: 'Title' }).fill('E2E Groceries')
  await page.getByRole('textbox', { name: 'Items' }).fill('2 milk, 500 g flour')
  await page.keyboard.press('Escape')
  await expect(card(page, 'E2E Groceries')).toContainText('flour')
  const groceries = (await list(page.request)).find((n) => n.title === 'E2E Groceries')!
  expect(groceries.kind).toBe('checklist')
  const g = await get(page.request, groceries.id)
  expect(item(g, 'milk')?.qty).toEqual(expect.objectContaining({ amount: '2', unit: null }))
  expect(item(g, 'flour')?.qty).toEqual(expect.objectContaining({ amount: '500', unit: 'g' }))

  // ACT note.edit @web.note: the editor saves 1.5 s after typing stops.
  await card(page, 'E2E Plan').locator('.nt-card-title').click()
  await expect(editor(page)).toBeVisible()
  await editor(page).getByRole('textbox', { name: 'Title' }).fill('E2E Plan B')
  await expect(editor(page).locator('.nt-meta')).toHaveText(/Saved/)
  const plan = (await list(page.request)).find((n) => n.title === 'E2E Plan B')!
  expect(plan).toBeTruthy()

  // ACT note.to_checklist @web.note: Ctrl+Shift+8 makes each line an item; and back to text.
  await page.keyboard.press('Control+Shift+8')
  await expect(row(page, 'Second line')).toBeVisible()
  expect((await get(page.request, plan.id)).kind).toBe('checklist')
  await page.keyboard.press('Control+Shift+8')
  await expect(editor(page).getByRole('textbox', { name: 'Note' })).toContainText('Second line')
  const back = await get(page.request, plan.id)
  expect(back.kind).toBe('text')
  expect(back.body).toContain('Second line')
})

test('checklist editing: add with the grammar, sections by paste, check into the fold, undo, clear', async ({ page }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Shopping', shopping: true, lines: 'Costco:\npaper towels' })
  await open(page, `/notes/n/${n.id}`)
  const add = editor(page).getByRole('textbox', { name: 'Add item' }).first()
  // ACT checkitem.add @web.note: the chips show what the line adds; Enter adds it.
  await add.fill('3 avocados')
  await expect(editor(page).locator('.nt-chip-qty')).toContainText('Quantity: 3')
  await add.press('Enter')
  await expect(row(page, 'avocados').locator('.nt-qty')).toHaveText('3')
  // A duplicate merges its quantity ("avocados 3 → 5").
  await add.fill('2 avocados')
  await add.press('Enter')
  await expect(row(page, 'avocados').locator('.nt-qty')).toHaveText('5')

  // ACT note.add_items @web.note: a paste of several lines asks first, with the sections it names.
  await add.click()
  await paste(page, 'Corner market:\neggs x12\nbread\nCostco: batteries')
  await expect(editor(page).locator('.nt-paste')).toContainText('Add 3 items to E2E Shopping (Corner market 2, Costco 1)?')
  await add.press('Enter')
  await expect(editor(page).locator('.nt-section-head')).toContainText(['Costco', 'Corner market'])
  const after = await get(page.request, n.id)
  const market = after.sections!.find((s) => s.name === 'Corner market')!
  expect(item(after, 'eggs')).toEqual(expect.objectContaining({ section_id: market.id, qty: expect.objectContaining({ amount: '12' }) }))
  expect(item(after, 'batteries')?.section_id).toBe(after.sections!.find((s) => s.name === 'Costco')!.id)

  // ACT checkitem.check @web.note: the row strikes, then moves into the "n checked" fold.
  await row(page, 'bread').getByRole('checkbox').click()
  await expect(editor(page).getByTestId('checked-fold')).toContainText('1 checked')
  expect(item(await get(page.request, n.id), 'bread')?.checked).toBe(true)
  // Undo: the toast's Undo unchecks it again.
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
  await expect.poll(async () => item(await get(page.request, n.id), 'bread')?.checked).toBe(false)

  // ACT checkitem.edit @web.note: a typed x2 becomes the quantity chip (the grammar in edit mode).
  const bread = await field(page, 'bread')
  await bread.fill('bread x2')
  await bread.press('Tab')
  await expect.poll(async () => item(await get(page.request, n.id), 'bread')?.qty?.amount).toBe('2')

  // Ctrl+Enter checks the focused item; ACT note.clear_checked @web.note clears the fold after a confirmation.
  await row(page, 'paper towels').locator('input.nt-item-input').focus()
  await page.keyboard.press('Control+Enter')
  await expect(editor(page).getByTestId('checked-fold')).toContainText('1 checked')
  await editor(page).getByRole('button', { name: 'Clear checked' }).click()
  await page.getByRole('dialog', { name: 'Clear checked items?' }).getByRole('button', { name: 'Clear' }).click()
  await expect(editor(page).getByTestId('checked-fold')).toHaveCount(0)
  expect(item(await get(page.request, n.id), 'paper towels')).toBeUndefined()

  // ACT checkitem.move @web.note: Alt+↓ moves an item down; Alt+Shift+↑ to the previous section.
  const eggs = row(page, 'eggs').locator('input.nt-item-input')
  await eggs.focus()
  await page.keyboard.press('Alt+Shift+ArrowUp')
  await expect.poll(async () => item(await get(page.request, n.id), 'eggs')?.section_id).toBe(after.sections!.find((s) => s.name === 'Costco')!.id)

  // ACT checkitem.delete @web.note: Backspace in an emptied item deletes it.
  const bat = await field(page, 'batteries')
  await bat.fill('')
  await bat.press('Backspace')
  await expect.poll(async () => item(await get(page.request, n.id), 'batteries')).toBeUndefined()
})

test('sections: create, rename, order mode, move and delete keeping the items', async ({ page }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Sections', lines: 'Bakery:\nbread\nDeli:\nham' })
  await open(page, `/notes/n/${n.id}`)
  // ACT checksection.create @web.note
  await editor(page).getByRole('button', { name: 'Add section' }).click()
  await page.getByRole('dialog', { name: 'New section' }).getByRole('textbox').fill('Pharmacy')
  await page.getByRole('dialog', { name: 'New section' }).getByRole('button', { name: 'Save' }).click()
  await expect(editor(page).locator('.nt-section-name')).toHaveText(['Bakery', 'Deli', 'Pharmacy'])
  // ACT checksection.rename @web.note
  await editor(page).getByRole('button', { name: 'Section Deli menu' }).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await page.getByRole('dialog', { name: 'Rename section' }).getByRole('textbox').fill('Butcher')
  await page.getByRole('dialog', { name: 'Rename section' }).getByRole('button', { name: 'Save' }).click()
  await expect(editor(page).locator('.nt-section-name')).toHaveText(['Bakery', 'Butcher', 'Pharmacy'])
  // ACT checksection.order_mode @web.note
  await editor(page).getByRole('button', { name: 'Section Butcher menu' }).click()
  await page.getByRole('menuitemradio', { name: 'Learned' }).click()
  await expect.poll(async () => (await get(page.request, n.id)).sections!.find((s) => s.name === 'Butcher')?.order_mode).toBe('learned')
  // ACT checksection.reorder @web.note: Alt+↑ on the focused header.
  await editor(page).locator('.nt-section-head', { hasText: 'Pharmacy' }).focus()
  await page.keyboard.press('Alt+ArrowUp')
  await expect(editor(page).locator('.nt-section-name')).toHaveText(['Bakery', 'Pharmacy', 'Butcher'])
  // ACT checksection.delete @web.note: "Keep the items" moves them to the top group.
  await editor(page).getByRole('button', { name: 'Section Butcher menu' }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await page.getByRole('dialog', { name: 'Delete section Butcher?' }).getByRole('button', { name: 'Delete section' }).click()
  await expect(editor(page).locator('.nt-section-name')).toHaveText(['Bakery', 'Pharmacy'])
  const done = await get(page.request, n.id)
  expect(item(done, 'ham')?.section_id).toBeNull()
})

// The new-list hint promises store sections; "Costco: 2 milk" in a new list makes the section and keeps
// the quantity. A section added later offers to move the items typed with its name in front.
test('a new list makes store sections from "Costco:"; a new section offers to move such items in', async ({ page }) => {
  await open(page)
  // ACT note.create @web.notes
  await page.getByRole('button', { name: 'New list' }).click()
  await page.getByRole('textbox', { name: 'Title' }).fill('E2E Stores')
  await page.getByRole('textbox', { name: 'Items' }).fill('Costco: 2 milk, eggs, towels')
  await page.keyboard.press('Escape')
  await expect(card(page, 'E2E Stores')).toContainText('towels')
  // The create toast offers Open and Undo.
  const made_t = page.locator('.toast').filter({ hasText: 'List added' })
  await expect(made_t.getByRole('button', { name: 'Open' })).toBeVisible()
  await expect(made_t.getByRole('button', { name: 'Undo' })).toBeVisible()
  const made = (await list(page.request)).find((n) => n.title === 'E2E Stores')!
  const got = await get(page.request, made.id)
  expect(got.sections!.map((x) => x.name)).toEqual(['Costco'])
  expect(item(got, 'milk')?.qty).toEqual(expect.objectContaining({ amount: '2', unit: null }))
  expect(item(got, 'milk')?.section_id).toBe(got.sections![0].id)

  const old = await mk(page.request, { kind: 'checklist', title: 'E2E Typed', items: [{ text: 'Costco: 2 milk' }, { text: 'costco: eggs' }, { text: 'bread' }] })
  await open(page, `/notes/n/${old.id}`)
  // ACT checksection.create @web.note
  await editor(page).getByRole('button', { name: 'Add section' }).click()
  await page.getByRole('dialog', { name: 'New section' }).getByRole('textbox').fill('Costco')
  await page.getByRole('dialog', { name: 'New section' }).getByRole('button', { name: 'Save' }).click()
  const offer = page.locator('.toast').filter({ hasText: '2 items start with “Costco:”' })
  await expect(offer).toBeVisible()
  // ACT checkitem.move @web.note
  await offer.getByRole('button', { name: 'Move them in' }).click()
  await expect(page.locator('.toast').filter({ hasText: '2 items moved into Costco' })).toBeVisible()
  await expect.poll(async () => {
    const n = await get(page.request, old.id)
    const sec = n.sections!.find((x) => x.name === 'Costco')!.id
    return [item(n, 'milk')?.section_id === sec, item(n, 'milk')?.qty?.amount, item(n, 'eggs')?.section_id === sec, item(n, 'bread')?.section_id === sec]
  }).toEqual([true, '2', true, false])
})

test('usual items: a chip adds the item to its last section; × hides it', async ({ page }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Usual', shopping: true, lines: 'coffee\ntea' })
  const ids = Object.fromEntries((n.items ?? []).map((i) => [i.text, i.id]))
  // Checked on three days a week apart, and checked now: due again after 0.8 of the interval.
  const day = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString()
  for (const key of ['coffee', 'tea'])
    for (const [d, c] of [[21, true], [15, false], [14, true], [8, false], [7, true]] as const) {
      const r = await page.request.patch(`/api/v1/notes/${n.id}/items/${ids[key]}`, { data: { checked: c, device_at: day(d) } })
      expect(r.ok(), await r.text()).toBeTruthy()
    }
  await page.request.post(`/api/v1/notes/${n.id}/items/clear-checked`, { data: {} })
  await open(page, `/notes/n/${n.id}`)
  const strip = editor(page).getByTestId('usual-strip')
  await expect(strip).toContainText('coffee')
  // ACT checkitem.usual_add @web.note
  await strip.getByRole('button', { name: 'coffee', exact: true }).click()
  await expect(row(page, 'coffee')).toBeVisible()
  expect(item(await get(page.request, n.id), 'coffee')?.checked).toBe(false)
  // ACT checkitem.usual_dismiss @web.note
  await strip.getByRole('button', { name: 'Hide tea for now' }).click()
  await expect(editor(page).getByTestId('usual-strip')).toHaveCount(0)
  const usual = (await (await page.request.get(`/api/v1/notes/${n.id}/usual`)).json()) as { items: unknown[]; hidden: { key: string }[] }
  expect(usual.hidden.map((h) => h.key)).toContain('tea')
})

test('version history: restore an earlier version, and undo the restore', async ({ page }) => {
  const n = await mk(page.request, { kind: 'text', title: 'E2E History', body: 'The first text' })
  // The editing session ends (the editor closed), so the next edit is a revision of its own.
  await page.request.post(`/api/v1/notes/${n.id}/revisions/checkpoint`, { data: {} })
  const r = await page.request.patch(`/api/v1/notes/${n.id}`, { data: { body: 'The second text', base_version: n.version } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await open(page, `/notes/n/${n.id}`)
  await expect(editor(page)).toContainText('The second text')
  // ACT note.history @web.note
  await editor(page).getByRole('button', { name: 'Version history' }).click()
  const hist = editor(page).getByTestId('note-history')
  await expect(hist.locator('.nt-history-rev').first()).toBeVisible()
  await hist.locator('.nt-history-rev').last().click()
  await expect(hist.locator('.nt-history-view')).toContainText('The first text')
  // ACT note.restore_version @web.note
  await hist.getByRole('button', { name: 'Restore this version' }).click()
  await expect.poll(async () => (await get(page.request, n.id)).body).toBe('The first text')
  await expect(editor(page).locator('.nt-body')).toContainText('The first text')
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
  await expect.poll(async () => (await get(page.request, n.id)).body).toBe('The second text')
})

test('pin, colour, labels, archive, Trash, restore and delete forever from the grid', async ({ page }) => {
  const a = await mk(page.request, { kind: 'text', title: 'E2E Alpha', body: 'a' })
  await mk(page.request, { kind: 'text', title: 'E2E Beta', body: 'b' })
  await open(page)
  // ACT note.pin @web.notes
  await card(page, 'E2E Alpha').hover()
  await card(page, 'E2E Alpha').getByRole('button', { name: 'Pin' }).click()
  await expect(page.getByRole('region', { name: 'Pinned' })).toContainText('E2E Alpha')
  // ACT note.color @web.notes: the swatches have names.
  await card(page, 'E2E Alpha').hover()
  await card(page, 'E2E Alpha').getByRole('button', { name: 'Colour' }).click()
  await page.getByRole('radio', { name: 'Sage' }).click()
  await expect(card(page, 'E2E Alpha')).toHaveClass(/nt-c-sage/)
  // ACT note.label @web.notes, ACT notelabel.create @web.notes: the labels menu creates a new one.
  await card(page, 'E2E Alpha').hover()
  await card(page, 'E2E Alpha').getByRole('button', { name: 'Labels' }).click()
  await page.getByRole('textbox', { name: 'Label name' }).fill('E2E Home')
  await page.getByRole('menuitem', { name: 'Create “E2E Home”' }).click()
  await page.keyboard.press('Escape')
  await expect(card(page, 'E2E Alpha').locator('.nt-label-chip')).toHaveText('E2E Home')
  await page.getByTestId('notes-sidebar').getByRole('button', { name: /E2E Home/ }).click()
  await expect(page).toHaveURL(/\/notes\/label\/E2E%20Home/)
  await expect(card(page, 'E2E Beta')).toHaveCount(0)
  const st = (await get(page.request, a.id)).state
  expect(st).toEqual(expect.objectContaining({ pinned: true, labels: ['E2E Home'] }))
  expect((await get(page.request, a.id)).color).toBe('sage')

  // ACT note.archive @web.notes, then ACT note.delete @web.notes with the keys.
  await page.getByTestId('notes-sidebar').getByRole('button', { name: 'Notes', exact: true }).click()
  await card(page, 'E2E Beta').hover()
  await card(page, 'E2E Beta').getByRole('button', { name: 'Archive' }).click()
  await expect(card(page, 'E2E Beta')).toHaveCount(0)
  expect((await get(page.request, a.id)).state.archived).toBe(false)
  await card(page, 'E2E Alpha').focus()
  await page.keyboard.press('#')
  await expect(card(page, 'E2E Alpha')).toHaveCount(0)
  // ACT note.restore @web.notes: Trash says how long a note stays, and restores it.
  await page.getByTestId('notes-nav-trash').click()
  await expect(card(page, 'E2E Alpha')).toContainText(/days left/)
  await card(page, 'E2E Alpha').hover()
  await card(page, 'E2E Alpha').getByRole('button', { name: 'Restore' }).click()
  await expect(card(page, 'E2E Alpha')).toHaveCount(0)
  expect((await get(page.request, a.id)).deleted_at).toBeNull()
  // ACT note.purge @web.notes: Delete forever confirms first.
  await page.request.delete(`/api/v1/notes/${a.id}`)
  await page.reload()
  await card(page, 'E2E Alpha').hover()
  await card(page, 'E2E Alpha').getByRole('button', { name: 'Delete forever' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete forever' }).click()
  await expect(page.getByTestId('notes-empty')).toContainText('No notes in Trash')
  expect((await page.request.get(`/api/v1/notes/${a.id}`)).status()).toBe(404)
})

test('Edit labels: rename, colour, reorder and delete a note label', async ({ page }) => {
  const n = await mk(page.request, { kind: 'text', title: 'E2E Labelled', body: 'x', labels: ['E2E One', 'E2E Two'] })
  await open(page)
  await page.getByRole('button', { name: 'Edit labels' }).click()
  const dlg = page.getByRole('dialog', { name: 'Edit labels' })
  // ACT notelabel.rename @web.notes: the new name is on every note that had the label.
  const name = dlg.getByRole('textbox', { name: 'Name of E2E One' })
  await name.fill('E2E Uno')
  await name.press('Enter')
  await expect.poll(async () => (await get(page.request, n.id)).state.labels.sort()).toEqual(['E2E Two', 'E2E Uno'])
  // ACT notelabel.color @web.notes
  await dlg.getByRole('button', { name: /Colour of E2E Two/ }).click()
  await page.getByRole('radio', { name: 'Peacock' }).click()
  await expect.poll(async () => ((await (await page.request.get('/api/v1/notes/labels')).json()) as { labels: { name: string; color: string | null }[] }).labels.find((l) => l.name === 'E2E Two')?.color).toBe('peacock')
  // ACT notelabel.reorder @web.notes
  await dlg.getByRole('button', { name: 'Move E2E Two up' }).click()
  await expect.poll(async () => ((await (await page.request.get('/api/v1/notes/labels')).json()) as { labels: { name: string; order: string }[] }).labels.sort((a, b) => (a.order < b.order ? -1 : 1)).map((l) => l.name)).toEqual(['E2E Two', 'E2E Uno'])
  // ACT notelabel.delete @web.notes: the label comes off the note; the note stays.
  await dlg.getByRole('button', { name: 'Delete E2E Uno' }).click()
  await page.getByRole('dialog', { name: /Delete the label/ }).getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => (await get(page.request, n.id)).state.labels).toEqual(['E2E Two'])
})

test('keys: g n, c and l, j and k, f, z; ? lists the Notes keys', async ({ page }) => {
  await mk(page.request, { kind: 'text', title: 'E2E Key one', body: '1' })
  await mk(page.request, { kind: 'text', title: 'E2E Key two', body: '2' })
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list]').first()).toBeVisible()
  await page.keyboard.press('g')
  await page.keyboard.press('n')
  await expect(page.getByTestId('notes-app')).toBeVisible()
  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Notes shortcuts' })).toContainText('Make it a list / make it text')
  await page.keyboard.press('Escape')
  // j moves among the cards the page shows: wait for them to load.
  await expect(page.locator('.nt-card').filter({ hasText: 'E2E Key two' })).toBeVisible()
  await page.keyboard.press('j')
  await expect(page.locator('.nt-card.focused')).toHaveCount(1)
  // ACT note.pin @web.notes with f, undone with z.
  const focused = await page.locator('.nt-card.focused').getAttribute('data-note')
  await page.keyboard.press('f')
  await expect.poll(async () => (await get(page.request, focused!)).state.pinned).toBe(true)
  // z undoes what the toast offers; under load the server had the pin before the undo was registered.
  await expect(page.getByRole('button', { name: 'Undo', exact: true }).last()).toBeVisible()
  await page.keyboard.press('z')
  await expect.poll(async () => (await get(page.request, focused!)).state.pinned).toBe(false)
  // l opens the capture bar for a list; Esc closes it again.
  await page.keyboard.press('l')
  await expect(page.getByTestId('notes-capture')).toHaveAttribute('data-kind', 'list')
  await page.keyboard.press('Escape')
  await page.keyboard.press('g')
  await page.keyboard.press('l')
  await expect(page).toHaveURL(/\/notes\/lists/)
})

test('the palette, the side panel and mail search reach notes', async ({ page }) => {
  const shop = await mk(page.request, { kind: 'checklist', title: 'E2E Errands', lines: 'stamps', pinned: true })
  await mk(page.request, { kind: 'text', title: 'E2E Quarterly plan', body: 'Budget review' })
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list]').first()).toBeVisible()
  // ACT note.add_items @web.palette: "Add to <list>: …" with the item grammar.
  await page.keyboard.press('Control+k')
  const input = page.getByRole('combobox', { name: 'Command' })
  await input.fill('Add to E2E Errands: 2 envelopes')
  await expect(page.locator('.palette-item.active')).toContainText('Add to E2E Errands')
  await input.press('Enter')
  await expect.poll(async () => item(await get(page.request, shop.id), 'envelopes')?.qty?.amount).toBe('2')
  // ACT note.create @web.palette
  await page.keyboard.press('Control+k')
  await input.fill('New note: E2E From the palette')
  await input.press('Enter')
  await expect.poll(async () => (await list(page.request)).some((n) => n.title === 'E2E From the palette')).toBe(true)

  // ACT checkitem.check @web.panel: the side panel shows pinned lists and checks in place.
  const rail = page.getByRole('complementary', { name: 'Open beside' })
  await rail.getByRole('button', { name: 'Notes beside' }).click()
  const panel = page.getByTestId('notes-panel')
  await expect(panel).toContainText('stamps')
  // The panel lists unchecked items only, so a checked item leaves the list at once (optimistic).
  // click(), not check(): check() reads the box's state again after the click, and with the box
  // gone it waits for it until the test times out (the flake of 7 Oct 2026).
  const stamps = panel.getByRole('checkbox', { name: 'Check stamps' })
  await stamps.click()
  await expect(stamps).toHaveCount(0)
  await expect.poll(async () => item(await get(page.request, shop.id), 'stamps')?.checked).toBe(true)
  // ACT note.add_items @web.panel: the quick-add line is bound to the chosen list.
  await panel.getByRole('textbox', { name: 'Add items' }).fill('glue')
  await panel.getByRole('textbox', { name: 'Add items' }).press('Enter')
  await expect.poll(async () => !!item(await get(page.request, shop.id), 'glue')).toBe(true)
  await page.getByRole('button', { name: 'Close panel' }).click()

  // ACT note.search @web.list: mail search shows the Notes section above the mail results.
  await page.goto('/mail/search?q=Quarterly')
  await expect(page.getByTestId('search-notes')).toContainText('E2E Quarterly plan')
  await page.getByTestId('search-notes').getByRole('link', { name: 'See all in Notes' }).click()
  await expect(page).toHaveURL(/\/notes\/search\?q=Quarterly/)
  await expect(card(page, 'E2E Quarterly plan')).toBeVisible()
})

test('a note from an email links back to it; links show as chips', async ({ page }) => {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Read me' }).first().click()
  await page.getByRole('article').last().getByRole('button', { name: 'More', exact: true }).click()
  // ACT note.from_email @web.reader
  await page.getByRole('menuitem', { name: 'Save to a note' }).click()
  await page.locator('.toast').getByRole('button', { name: 'Open' }).click()
  await expect(editor(page)).toBeVisible()
  await expect(editor(page).locator('.nt-link-chip')).toHaveCount(1)
  const n = (await list(page.request)).find((x) => x.title.includes('E2E Read me'))!
  expect((await get(page.request, n.id)).links?.[0]?.target_kind).toBe('conversation')
  // ACT note.unlink @web.note
  await editor(page).locator('.nt-link-chip').getByRole('button', { name: /Remove link/ }).click()
  await expect(editor(page).locator('.nt-link-chip')).toHaveCount(0)
})

test('Settings offers the export of all notes', async ({ page }) => {
  await mk(page.request, { kind: 'text', title: 'E2E Export', body: 'x' })
  await page.goto('/settings/notes')
  // ACT note.export @web.settings
  const link = page.getByTestId('notes-export')
  await expect(link).toHaveAttribute('href', '/api/v1/notes/export')
  const r = await page.request.get('/api/v1/notes/export')
  expect(r.ok()).toBeTruthy()
  expect(r.headers()['content-type']).toContain('zip')
})

// ---------------------------------------------------------------- the frame in every theme

async function navToNotes(page: Page, theme: Theme) {
  switch (theme) {
    case 'classic':
      await page.locator('.sidebar [data-app-nav="notes"]').click()
      break
    case 'office':
      await page.locator('.o-appbar [data-app-nav="notes"]').click()
      break
    case 'focus': {
      await page.locator('.f-where').click()
      const input = page.getByRole('combobox', { name: 'Command' })
      await input.fill('Go to Notes')
      await input.press('Enter')
      break
    }
    case 'desk':
      await page.locator('.d-spaces [data-space="notes"]').click()
      break
    case 'cards':
      await page.locator('.c-rail [data-app-nav="notes"]').click()
      break
  }
}

const PLACEMENT: Record<Theme, string> = { classic: 'popover', office: 'pane', focus: 'card', desk: 'below', cards: 'sheet' }

for (const theme of THEMES) {
  test(`${theme}: the nav entry opens Notes; a note opens where the theme puts it; check and undo`, async ({ page }) => {
    const n = await mk(page.request, { kind: 'checklist', title: `E2E ${theme} list`, lines: 'apples\npears' })
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/mail/inbox')
    await expect(page.locator('[data-mail-list]').first()).toBeVisible()
    await navToNotes(page, theme)
    await expect(page.getByTestId('notes-app')).toBeVisible()
    await card(page, `E2E ${theme} list`).locator('.nt-card-title').click()
    await expect(page.getByTestId('note-pane')).toHaveClass(new RegExp(`nt-pane-${PLACEMENT[theme]}`))
    await row(page, 'apples').getByRole('checkbox').click()
    await expect.poll(async () => item(await get(page.request, n.id), 'apples')?.checked).toBe(true)
    await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
    await expect.poll(async () => item(await get(page.request, n.id), 'apples')?.checked).toBe(false)
    await editor(page).getByRole('button', { name: 'Close' }).click()
    await expect(page.getByTestId('note-pane')).toHaveCount(0)
  })
}
