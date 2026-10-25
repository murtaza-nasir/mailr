// ACT-SURFACES: web.note web.notes web.palette web.table web.calendar
// Household lists (offline, presence, repeating lists, quick note; a table on the calendar, the
// add-a-row form) against the real server.
// Covers: ACT note.local_only (the switch, and the lock an editor sees), ACT note.repeat (the dialog,
// typed words, the line and the card mark), ACT note.quick (Shift+N and the palette's New note with
// #List), offline ticks and adds that wait and are sent (the dashed edge, the banner), ACT
// note.share_quick ("Share with …"), ACT note.presence ("… is in this list", her ticks by name),
// ACT table.local_only, ACT table.on_calendar (the menu and the chip on the month), ACT
// table.form_link (the form page, and the refusal for someone it is not shared with).
import type { APIRequestContext, Browser, Page } from '@playwright/test'
import { type AccessSeed, accessTest, connectCalendar, expect, type SeedAccount, shot, signIn, test } from './fixtures'
import { getNote, mkNote } from './helpers'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

const BASE = process.env.E2E_BASE ?? 'http://localhost:5175'

interface Item { id: string; text: string; checked: boolean; section_id: string | null }
interface Note { id: string; title: string; local_only: boolean; repeat?: { rule: string } | null; items?: Item[]; sections?: { id: string; name: string }[] }

const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkNote<Note>(request, body)
const get = (request: APIRequestContext, id: string) => getNote<Note>(request, id)
const editor = (page: Page) => page.getByTestId('note-editor')
const row = (page: Page, text: string) => editor(page).locator('.nt-row').filter({ has: page.locator(`input.nt-item-input[value="${text}"]`) })

async function openNote(page: Page, id: string) {
  await page.goto(`/notes/n/${id}`)
  await expect(editor(page)).toBeVisible()
  await expect(editor(page).locator('.nt-row').first()).toBeVisible()
}
async function more(page: Page) {
  await editor(page).getByRole('button', { name: 'More', exact: true }).click()
}

async function person(browser: Browser, who: SeedAccount): Promise<{ page: Page; close: () => Promise<void> }> {
  const ctx = await browser.newContext({ baseURL: BASE })
  const page = await ctx.newPage()
  await signIn(page, who)
  return { page, close: () => ctx.close() }
}
async function joinGroup(page: Page, access: AccessSeed) {
  const r = await page.request.put(`/api/v1/admin/users/${access.admin.id}/groups`, { data: { groups: [access.group] } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

test('a list repeats weekly on a day or every few days; the line and the card say so', async ({ page }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Chores', lines: 'dishes\nbins' })
  await openNote(page, n.id)
  await more(page)
  // ACT note.repeat @web.note
  await page.getByRole('menuitem', { name: 'Repeat…' }).click()
  const dlg = page.getByTestId('repeat-dialog')
  await expect(dlg).toBeVisible()
  await page.getByRole('radio', { name: 'Weekly' }).check()
  await page.getByRole('combobox', { name: 'Day of the week' }).selectOption('SU')
  await page.getByTestId('repeat-save').click()
  await expect.poll(async () => (await get(page.request, n.id)).repeat?.rule).toBe('FREQ=WEEKLY;BYDAY=SU')
  await expect(page.getByTestId('note-repeat-line')).toContainText('Repeats weekly on Sunday · next Sun')
  await page.screenshot({ path: shot('lists-repeat-line.png') })
  // Typed words, read with the task grammar.
  await more(page)
  await page.getByRole('menuitem', { name: /Repeat: weekly on sunday/i }).click()
  await dlg.getByPlaceholder('every sunday, every 3 days').fill('every 3 days')
  await expect(dlg.getByText('Every 3 days', { exact: true })).toBeVisible()
  await page.screenshot({ path: shot('lists-repeat-dialog.png') })
  await page.getByTestId('repeat-save').click()
  await expect.poll(async () => (await get(page.request, n.id)).repeat?.rule).toBe('FREQ=DAILY;INTERVAL=3')
  await page.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(page.locator('.nt-card').filter({ hasText: 'E2E Chores' }).getByLabel('Repeats every 3 days')).toBeVisible()
  // ACT note.repeat @web.notes: off again from the card's menu.
  const card = page.locator('.nt-card').filter({ hasText: 'E2E Chores' })
  await card.hover()
  await card.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: /Repeat: every 3 days/i }).click()
  await page.getByRole('radio', { name: 'Does not repeat' }).check()
  await page.getByTestId('repeat-save').click()
  await expect.poll(async () => (await get(page.request, n.id)).repeat ?? null).toBeNull()
})

test('Shift+N opens New note; #List adds to its section, anything else is a note', async ({ page }) => {
  const shop = await mk(page.request, { kind: 'checklist', title: 'Shopping', lines: '## Costco\npaper towels' })
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list]').first()).toBeVisible()
  // ACT note.quick @web.palette: Shift+N opens the palette on "New note: ".
  await page.keyboard.press('Shift+N')
  const input = page.getByRole('combobox', { name: 'Command' })
  await expect(input).toHaveValue('New note: ')
  await input.pressSequentially('#Shopping Costco: milk')
  await expect(page.locator('.palette-item.active')).toContainText('Add to Shopping: milk')
  await expect(page.locator('.palette-item.active')).toContainText('milk · Costco')
  await page.screenshot({ path: shot('lists-quick-palette.png') })
  await input.press('Enter')
  await expect.poll(async () => {
    const n = await get(page.request, shop.id)
    const costco = n.sections?.find((s) => s.name === 'Costco')?.id
    return n.items?.some((i) => i.text === 'milk' && i.section_id === costco)
  }).toBe(true)
  // A plain text note.
  await page.keyboard.press('Shift+N')
  await expect(input).toHaveValue('New note: ')
  await input.fill('New note: Call the plumber')
  await expect(page.locator('.palette-item.active')).toContainText('New note: Call the plumber')
  await input.press('Enter')
  await expect.poll(async () => ((await (await page.request.get('/api/v1/notes', { params: { view: 'all' } })).json()) as { notes: { title: string }[] }).notes.some((x) => x.title === 'Call the plumber')).toBe(true)
})

test('offline, ticks and new items wait on the device and are sent when the line is back', async ({ page, context }) => {
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Offline', lines: 'milk\neggs' })
  await openNote(page, n.id)
  await context.setOffline(true)
  // One offline line, the app's, about Notes; the list says only its own state.
  await expect(page.locator('[data-connection="offline"]')).toContainText('List changes are kept on this device')
  await expect(page.locator('[data-connection="offline"]')).toHaveCount(1)
  await expect(page.locator('.nt-banner')).toHaveCount(0)
  // A tick and a new item, with no connection.
  await row(page, 'milk').getByRole('checkbox').check()
  const add = editor(page).getByPlaceholder('Add item').first()
  await add.fill('bread')
  await add.press('Enter')
  await expect(row(page, 'bread')).toHaveAttribute('data-pending', 'true')
  await expect(page.getByTestId('note-offline')).toContainText('2 pending changes on this device')
  await expect(page.locator('[data-connection="offline"]')).toContainText('2 pending changes')
  await page.screenshot({ path: shot('lists-offline.png') })
  // Things that need the connection say so.
  await expect(editor(page).getByRole('button', { name: 'Colour' })).toBeDisabled()
  await context.setOffline(false)
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await expect.poll(async () => {
    const x = await get(page.request, n.id)
    return [x.items?.find((i) => i.text === 'milk')?.checked, x.items?.some((i) => i.text === 'bread')]
  }, { timeout: 30_000 }).toEqual([true, true])
  await expect(page.getByTestId('note-offline')).toHaveCount(0)
  await expect(row(page, 'bread')).not.toHaveAttribute('data-pending', 'true')
})

accessTest('share with a household member in one step; her presence and her ticks show; the local-only lock', async ({ page, access, browser }) => {
  await signIn(page, access.admin)
  await joinGroup(page, access)
  const n = await mk(page.request, { kind: 'checklist', title: 'E2E Groceries', lines: 'milk\neggs' })
  await openNote(page, n.id)
  await more(page)
  // ACT note.share_quick @web.note
  await page.getByRole('menuitem', { name: /^Share with E2E/ }).click()
  await expect(page.getByText(/^Shared with E2E/)).toBeVisible()
  await expect(page.getByTestId('note-shared-line')).toContainText('Shared with 1 person')
  // ACT note.local_only @web.note: the owner turns it on.
  await more(page)
  await page.getByRole('menuitemcheckbox', { name: 'Local models only' }).click()
  await expect.poll(async () => (await get(page.request, n.id)).local_only).toBe(true)

  const cleo = await person(browser, access.family)
  try {
    await openNote(cleo.page, n.id)
    // ACT note.presence @web.note: each side sees the other.
    await expect(page.getByTestId('note-presence')).toContainText(/is in this list/)
    await expect(cleo.page.getByTestId('note-presence')).toContainText(/is in this list/)
    await page.screenshot({ path: shot('lists-presence.png') })
    // Her tick shows on the owner's list, with her name beside the row for a moment.
    await row(cleo.page, 'eggs').getByRole('checkbox').check()
    await expect(page.getByTestId('item-touched')).toBeVisible()
    await expect.poll(async () => (await get(page.request, n.id)).items?.find((i) => i.text === 'eggs')?.checked).toBe(true)
    // The editor sees the switch on and locked, with the reason.
    await editor(cleo.page).getByRole('button', { name: 'More', exact: true }).click()
    await expect(cleo.page.getByRole('menuitemcheckbox', { name: 'Local models only' })).toBeDisabled()
    await expect(cleo.page.getByTestId('local-only-lock')).toHaveText('Only the owner can turn this off.')
    await cleo.page.screenshot({ path: shot('lists-local-only-locked.png') })
    await cleo.page.keyboard.press('Escape')
    // She closes the list: the owner's line goes.
    await cleo.page.goto('/notes')
    await expect(page.getByTestId('note-presence')).toHaveCount(0, { timeout: 15_000 })
  } finally {
    await cleo.close()
  }
})

test('a table shows its dates on your calendar and has an Add-a-row form', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const r = await page.request.post('/api/v1/tables', { data: { name: 'E2E Birthdays' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const t = (await r.json()).table as { id: string; columns: { id: string }[] }
  const first = t.columns[0].id
  const c = await page.request.post(`/api/v1/tables/${t.id}/columns`, { data: { name: 'Day', type: 'date' } })
  const day = (await c.json()).column.id as string
  const today = new Date()
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const when = iso(new Date(today.getFullYear(), today.getMonth(), 15))
  await page.request.post(`/api/v1/tables/${t.id}/rows`, { data: { cells: { [first]: 'Ana', [day]: when } } })
  await page.goto(`/tables/t/${t.id}`)
  await expect(page.getByTestId('table-app')).toBeVisible()
  await page.getByTestId('table-more').click()
  // ACT table.local_only @web.table, ACT table.on_calendar @web.table
  await expect(page.getByRole('menuitemcheckbox', { name: 'Local models only' })).toBeVisible()
  await page.screenshot({ path: shot('lists-table-menu.png') })
  await page.getByRole('menuitemradio', { name: 'Show Day dates' }).click()
  await expect.poll(async () => ((await (await page.request.get(`/api/v1/tables/${t.id}`)).json()) as { state: { calendar_column: string | null } }).state.calendar_column).toBe(day)
  // ACT table.on_calendar @web.calendar: the chip on the month, and a tap opens the row.
  await page.goto(`/calendar/month/${when}`)
  const chip = page.getByTestId('cal-table-date').filter({ hasText: 'Ana' })
  await expect(chip).toBeVisible()
  await page.screenshot({ path: shot('lists-table-calendar.png') })
  await chip.getByRole('button').click()
  await expect(page.getByTestId('row-form')).toBeVisible()

  // ACT table.form_link @web.table: the form on its own.
  await page.goto(`/tables/t/${t.id}/add`)
  await expect(page.getByTestId('add-row-page')).toBeVisible()
  await page.getByTestId('row-form').getByRole('textbox').first().fill('Ben')
  await page.getByTestId('row-form').getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByTestId('add-row-done')).toContainText('Row added')
  await page.screenshot({ path: shot('lists-add-row-form.png') })
  await expect.poll(async () => ((await (await page.request.get(`/api/v1/tables/${t.id}/rows`)).json()) as { total: number }).total).toBe(2)
})

accessTest('the Add-a-row form is for the people who share the table only', async ({ page, access, browser }) => {
  await signIn(page, access.admin)
  const r = await page.request.post('/api/v1/tables', { data: { name: 'E2E Chores done' } })
  const t = (await r.json()).table as { id: string }
  // ACT table.form_link @web.table: someone it is not shared with is told so; a viewer cannot add.
  const other = await person(browser, access.family)
  try {
    await other.page.goto(`/tables/t/${t.id}/add`)
    await expect(other.page.getByText('This form is not available to you')).toBeVisible()
    const s = await page.request.put(`/api/v1/tables/${t.id}/shares/${access.family.id}`, { data: { level: 'view' } })
    expect(s.ok(), await s.text()).toBeTruthy()
    await other.page.reload()
    await expect(other.page.getByTestId('add-row-refused')).toContainText('You can view this table, but not add rows')
  } finally {
    await other.close()
  }
})
