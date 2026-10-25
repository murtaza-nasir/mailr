// ACT-SURFACES: web.note
// Undo and redo in the note editor: typing in groups, a
// paste that replaces the text, a big removal with its toast, checklist changes, a colour, and the
// result saved as a version that survives a reload. Each step is checked through the API.
// Covers: ACT note.undo_edit, ACT note.redo_edit.
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { getNote, mkNote, openApp } from './helpers'

interface Item { id: string; text: string; checked: boolean }
interface Note { id: string; kind: string; title: string; body: string; color: string | null; version: number; items?: Item[] }

const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkNote<Note>(request, body)
const get = (request: APIRequestContext, id: string) => getNote<Note>(request, id)
const editor = (page: Page) => page.getByTestId('note-editor')
const undoBtn = (page: Page) => editor(page).getByRole('toolbar').getByRole('button', { name: 'Undo', exact: true })
const redoBtn = (page: Page) => editor(page).getByRole('toolbar').getByRole('button', { name: 'Redo', exact: true })
const row = (page: Page, text: string) => editor(page).locator('.nt-row').filter({ has: page.locator(`input.nt-item-input[value="${text}"]`) })

const open = (page: Page, id: string) => openApp(page, `/notes/n/${id}`, { theme: 'classic', ready: editor(page) })

test('text: typing undoes a word at a time, a paste that replaced everything comes back, redo, and the result is saved', async ({ page, context }) => {
  const n = await mk(page.request, { kind: 'text', title: 'E2E Undo', body: '' })
  await open(page, n.id)
  await expect(undoBtn(page)).toBeDisabled()
  await expect(redoBtn(page)).toBeDisabled()

  const body = editor(page).getByRole('textbox', { name: 'Note' })
  await body.click()
  await page.keyboard.type('Buy milk')
  await expect(undoBtn(page)).toBeEnabled()
  // ACT note.undo_edit @web.note: Ctrl+Z takes back the last word, then the one before.
  await page.keyboard.press('Control+z')
  await expect(body).toHaveValue('Buy ')
  await expect(redoBtn(page)).toBeEnabled()
  // ACT note.redo_edit @web.note: Ctrl+Shift+Z puts it back.
  await page.keyboard.press('Control+Shift+z')
  await expect(body).toHaveValue('Buy milk')

  // A paste over the selected text is one step, and one undo brings the text back.
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.evaluate(() => navigator.clipboard.writeText('Something else entirely'))
  await body.press('Control+a')
  await body.press('Control+v')
  await expect(body).toHaveValue('Something else entirely')
  await page.keyboard.press('Control+z')
  await expect(body).toHaveValue('Buy milk')
  await page.keyboard.press('Control+y')
  await expect(body).toHaveValue('Something else entirely')
  // The toolbar's buttons do the same.
  await undoBtn(page).click()
  await expect(body).toHaveValue('Buy milk')

  // An undo is an edit like any other: it autosaves as a new version, and a reload shows it.
  await expect(editor(page).locator('.nt-meta')).toHaveText(/Saved/)
  await expect.poll(async () => (await get(page.request, n.id)).body).toBe('Buy milk')
  await page.reload()
  await expect(editor(page).locator('.nt-body')).toContainText('Buy milk')
})

test('text: removing more than three lines says so, and its Undo brings them back', async ({ page }) => {
  const lines = 'one\ntwo\nthree\nfour\nfive'
  const n = await mk(page.request, { kind: 'text', title: 'E2E Lines', body: lines })
  await open(page, n.id)
  await editor(page).locator('.nt-body').click()
  const body = editor(page).getByRole('textbox', { name: 'Note' })
  await body.press('Control+a')
  await body.press('Delete')
  await expect(body).toHaveValue('')
  const toast = page.locator('.toast').filter({ hasText: 'Text removed' })
  await expect(toast).toBeVisible()
  await toast.getByRole('button', { name: 'Undo' }).click()
  await expect(body).toHaveValue(lines)
  await expect.poll(async () => (await get(page.request, n.id)).body).toBe(lines)
})

test('checklist: a tick, a deleted item and a colour undo and redo through the editor history', async ({ page }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Undo list', lines: 'milk\neggs\nbread' })
  await open(page, n.id)

  // A tick (its toast also offers Undo; the editor's history holds the same step).
  await row(page, 'milk').getByRole('checkbox').click()
  await expect.poll(async () => (await get(page.request, n.id)).items?.find((i) => i.text === 'milk')?.checked).toBe(true)
  // An item deleted from its menu.
  await row(page, 'eggs').getByRole('button', { name: 'More for eggs' }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await expect.poll(async () => (await get(page.request, n.id)).items?.some((i) => i.text === 'eggs')).toBe(false)
  // A colour.
  await editor(page).getByRole('toolbar').getByRole('button', { name: 'Colour' }).click()
  await page.getByRole('radio', { name: 'Sage' }).click()
  await expect.poll(async () => (await get(page.request, n.id)).color).toBe('sage')

  // Undo goes back through them, newest first.
  await undoBtn(page).click()
  await expect.poll(async () => (await get(page.request, n.id)).color).toBe(null)
  await undoBtn(page).click()
  await expect(row(page, 'eggs')).toBeVisible()
  await expect.poll(async () => (await get(page.request, n.id)).items?.some((i) => i.text === 'eggs')).toBe(true)
  // Ctrl+Z with the focus on the list (not in a text field) uses the same history.
  await row(page, 'bread').getByRole('checkbox').focus()
  await page.keyboard.press('Control+z')
  await expect.poll(async () => (await get(page.request, n.id)).items?.find((i) => i.text === 'milk')?.checked).toBe(false)
  await expect(undoBtn(page)).toBeDisabled()

  // Redo replays them in order.
  await redoBtn(page).click()
  await expect.poll(async () => (await get(page.request, n.id)).items?.find((i) => i.text === 'milk')?.checked).toBe(true)
  await redoBtn(page).click()
  await expect.poll(async () => (await get(page.request, n.id)).items?.some((i) => i.text === 'eggs')).toBe(false)
  await redoBtn(page).click()
  await expect.poll(async () => (await get(page.request, n.id)).color).toBe('sage')
  await expect(redoBtn(page)).toBeDisabled()

  // A new change after an undo drops what could be redone.
  await undoBtn(page).click()
  await expect(redoBtn(page)).toBeEnabled()
  await row(page, 'bread').getByRole('checkbox').click()
  await expect(redoBtn(page)).toBeDisabled()
})

test('checklist: the title undoes as text, and the add line keeps its own undo while it holds text', async ({ page }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E List', lines: 'apples' })
  await open(page, n.id)
  const title = editor(page).getByRole('textbox', { name: 'Title' })
  await title.click()
  await page.keyboard.press('End')
  await page.keyboard.type(' two')
  await expect(title).toHaveValue('E2E List two')
  await page.keyboard.press('Control+z')
  await expect(title).toHaveValue('E2E List ')
  // Text typed in the add line and not yet added: Ctrl+Z there is the browser's, for that text.
  const add = editor(page).getByRole('textbox', { name: 'Add item' }).first()
  await add.click()
  await page.keyboard.type('pears')
  await page.keyboard.press('Control+z')
  await expect(title).toHaveValue('E2E List ')
  await expect(add).not.toHaveValue('pears')
})
