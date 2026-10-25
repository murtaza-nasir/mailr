// ACT-SURFACES: web.notes web.note web.table
// Sharing notes, lists and tables between two people on the server (backend notes/sharing.py),
// against the real server: an admin ("E2E Admin") and a user
// in a group ("E2E Family") from `mailr e2e-seed --access`.
// Covers: ACT note.share and ACT note.unshare (the people dialog from the editor and a card: find a
// person, add at a level, change it, add a group, stop), ACT note.share_notices (the notice line),
// Shared with me, "View only · shared by …", ACT note.leave (Remove from my notes, and Undo),
// ACT note.move_group ("New list in <group>", Move to group, Move out of the group), authors on items
// ("Added by …"), "… is editing", the "changed since you looked" dot, ACT table.share,
// ACT table.unshare, ACT table.leave and ACT table.move_group on the table screen and its card.
import type { APIRequestContext, Browser, Page } from '@playwright/test'
import { type AccessSeed, accessTest as test, expect, type SeedAccount, shot, signIn } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

const BASE = process.env.E2E_BASE ?? 'http://localhost:5175'

interface Note { id: string; title: string; level: string; items?: { id: string; text: string }[] }

/** A second person in their own browser context, signed in. */
async function person(browser: Browser, who: SeedAccount): Promise<{ page: Page; request: APIRequestContext; close: () => Promise<void> }> {
  const ctx = await browser.newContext({ baseURL: BASE })
  const page = await ctx.newPage()
  await signIn(page, who)
  return { page, request: page.request, close: () => ctx.close() }
}

async function mkNote(request: APIRequestContext, body: Record<string, unknown>): Promise<Note> {
  const r = await request.post('/api/v1/notes', { data: body })
  expect(r.ok(), await r.text()).toBeTruthy()
  return ((await r.json()) as { note: Note }).note
}

async function shares(request: APIRequestContext, kind: 'note' | 'table', id: string) {
  const r = await request.get(`/api/v1/${kind}s/${id}/shares`)
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as { people: { id: number; level: string }[]; groups: { name: string; level: string }[]; owner: { kind: string; name: string } }
}

/** The admin joins the family group, so both people share a group. */
async function joinGroup(page: Page, access: AccessSeed) {
  const r = await page.request.put(`/api/v1/admin/users/${access.admin.id}/groups`, { data: { groups: [access.group] } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

const card = (page: Page, text: string) => page.locator('.nt-card').filter({ hasText: text })
const dialog = (page: Page) => page.getByTestId('note-share-dialog')

test('the people dialog shares a list with a person and a group, changes the level and stops', async ({ page, access }) => {
  await signIn(page, access.admin)
  await joinGroup(page, access)
  const n = await mkNote(page.request, { kind: 'checklist', title: 'E2E Camping', lines: 'tent\nstove' })
  await page.goto(`/notes/n/${n.id}`)
  await expect(page.getByTestId('note-editor')).toBeVisible()
  // ACT note.share @web.note: the Share button of the editor's toolbar.
  await page.getByTestId('note-editor').getByRole('button', { name: 'Share', exact: true }).click()
  await expect(dialog(page)).toBeVisible()
  await expect(dialog(page).getByTestId('share-owner')).toHaveText('You own this list.')
  await expect(dialog(page).getByTestId('share-none')).toContainText('Only you see this list')
  await dialog(page).getByTestId('share-search').fill(access.family.email)
  await dialog(page).getByTestId('share-new-level').selectOption('edit')
  await dialog(page).getByRole('button', { name: 'Share with E2E Family' }).click()
  await expect(dialog(page).getByTestId('share-person')).toHaveCount(1)
  await expect.poll(async () => (await shares(page.request, 'note', n.id)).people.map((p) => p.level)).toEqual(['edit'])
  // Change the level, then add the group.
  await dialog(page).getByRole('combobox', { name: 'Level for E2E Family' }).selectOption('view')
  await expect.poll(async () => (await shares(page.request, 'note', n.id)).people.map((p) => p.level)).toEqual(['view'])
  await dialog(page).getByTestId('share-search').fill(access.group)
  await dialog(page).getByRole('button', { name: `Share with the ${access.group} group` }).click()
  await expect(dialog(page).getByTestId('share-person')).toHaveCount(2)
  await page.screenshot({ path: shot('notes-share-dialog.png') })
  // ACT note.unshare @web.note: stop sharing with the group.
  await dialog(page).getByRole('button', { name: `Stop sharing with the ${access.group} group` }).click()
  await expect(dialog(page).getByTestId('share-person')).toHaveCount(1)
  await expect.poll(async () => (await shares(page.request, 'note', n.id)).groups).toEqual([])
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  // The meta line and the card say who else sees it.
  await expect(page.getByTestId('note-shared-line')).toContainText('Shared with 1 person')
  await page.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(card(page, 'E2E Camping').getByTestId('note-people')).toBeVisible()
  // ACT note.share @web.notes, ACT note.unshare @web.notes: the same dialog from a card's ⋮ menu.
  await card(page, 'E2E Camping').hover()
  await card(page, 'E2E Camping').getByRole('button', { name: 'More' }).click()
  await page.getByRole('menuitem', { name: 'Share…' }).click()
  await dialog(page).getByRole('button', { name: 'Stop sharing with E2E Family' }).click()
  await expect(dialog(page).getByTestId('share-none')).toBeVisible()
})

test('a sharee sees the notice, Shared with me and view only, and removes the list from their notes', async ({ page, browser, access }) => {
  await signIn(page, access.admin)
  const n = await mkNote(page.request, { kind: 'checklist', title: 'E2E Packing', lines: 'boots' })
  const r = await page.request.put(`/api/v1/notes/${n.id}/shares/${access.family.id}`, { data: { level: 'view' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const dana = await person(browser, access.family)
  try {
    const p = dana.page
    await p.goto('/notes')
    // ACT note.share_notices @web.notes: the notice above the left column's views.
    await expect(p.getByTestId('notes-share-notices')).toContainText('E2E Admin shared “E2E Packing” with you.')
    await expect(card(p, 'E2E Packing').getByTestId('note-changed')).toBeVisible()
    await p.getByTestId('notes-sidebar').getByRole('button', { name: 'Shared with me' }).click()
    await expect(card(p, 'E2E Packing')).toBeVisible()
    await p.screenshot({ path: shot('notes-shared-with-me.png') })
    await card(p, 'E2E Packing').click()
    await expect(p.getByTestId('note-view-only')).toHaveText('View only · shared by E2E Admin')
    await expect(p.getByTestId('note-editor').getByRole('checkbox', { name: 'Check boots' })).toBeDisabled()
    await p.getByRole('button', { name: 'Close', exact: true }).click()
    // Opening it took the dot away.
    await expect(card(p, 'E2E Packing').getByTestId('note-changed')).toHaveCount(0)
    await p.getByTestId('notes-share-notices').getByRole('button', { name: 'Dismiss' }).click()
    await expect(p.getByTestId('notes-share-notices')).toHaveCount(0)
    // ACT note.leave @web.notes: ⋮ → Remove from my notes; Undo brings it back.
    await card(p, 'E2E Packing').hover()
    await card(p, 'E2E Packing').getByRole('button', { name: 'More' }).click()
    await p.getByRole('menuitem', { name: 'Remove from my notes' }).click()
    await expect(card(p, 'E2E Packing')).toHaveCount(0)
    await p.getByRole('button', { name: 'Undo' }).click()
    await expect(card(p, 'E2E Packing')).toBeVisible()
    // ACT note.leave @web.note: from the people dialog of the open note.
    await card(p, 'E2E Packing').click()
    await p.getByTestId('note-editor').getByRole('button', { name: 'People' }).click()
    await expect(dialog(p).getByTestId('share-owner')).toHaveText('E2E Admin owns this list.')
    await dialog(p).getByTestId('share-leave').click()
    await expect(card(p, 'E2E Packing')).toHaveCount(0)
    expect((await dana.request.get(`/api/v1/notes/${n.id}`)).status()).toBe(404)
    // The owner keeps it.
    expect((await page.request.get(`/api/v1/notes/${n.id}`)).ok()).toBeTruthy()
  } finally {
    await dana.close()
  }
})

test('a group list: every member edits; the other person shows as editing and as the author', async ({ page, browser, access }) => {
  await signIn(page, access.admin)
  await joinGroup(page, access)
  await page.goto('/notes')
  // ACT note.move_group @web.note: "New list in <group>" from the capture bar.
  await page.getByTestId('notes-new-group-list').click()
  await page.getByRole('menuitem', { name: `New list in ${access.group}` }).click()
  await expect(page.getByTestId('capture-group')).toHaveText(access.group)
  await page.getByRole('textbox', { name: 'Title' }).fill('E2E House list')
  await page.getByRole('textbox', { name: 'Items' }).fill('soap')
  await page.keyboard.press('Escape')
  await expect(card(page, 'E2E House list')).toBeVisible()
  const notes = (await (await page.request.get('/api/v1/notes')).json()).notes as (Note & { owner: { kind: string; name: string } })[]
  const house = notes.find((x) => x.title === 'E2E House list')!
  expect(house.owner).toEqual({ kind: 'group', name: access.group })
  await card(page, 'E2E House list').click()
  await expect(page.getByTestId('note-shared-line')).toContainText(`In the ${access.group} group`)
  // The other member writes; the open list shows them editing and names them on the item.
  const dana = await person(browser, access.family)
  try {
    const r = await dana.request.post(`/api/v1/notes/${house.id}/items`, { data: { lines: 'towels' } })
    expect(r.ok(), await r.text()).toBeTruthy()
    await expect(page.getByTestId('note-presence')).toHaveText('E2E Family is editing', { timeout: 8_000 })
    const towels = page.getByTestId('note-editor').locator('.nt-row').filter({ has: page.locator('input.nt-item-input[value="towels"]') })
    await expect(towels.getByTestId('item-author')).toHaveText('Added by E2E Family')
    await page.screenshot({ path: shot('notes-presence.png') })
    // A member cannot delete a group list; Delete is not offered to them.
    await dana.page.goto(`/notes/n/${house.id}`)
    await dana.page.getByTestId('note-editor').getByRole('button', { name: 'More', exact: true }).click()
    await expect(dana.page.getByRole('menuitem', { name: 'Delete' })).toHaveCount(0)
    await expect(dana.page.getByRole('menuitem', { name: 'Remove from my notes' })).toHaveCount(0)
  } finally {
    await dana.close()
  }
  // ACT note.move_group @web.note: the creator moves it out of the group again.
  await page.getByTestId('note-editor').getByRole('button', { name: 'Share', exact: true }).click()
  await dialog(page).getByTestId('share-move-out').click()
  await expect(dialog(page).getByTestId('share-owner')).toHaveText('You own this list.')
  // …and back in, with Move to group.
  await dialog(page).getByRole('combobox', { name: 'Group' }).selectOption(access.group)
  await dialog(page).getByRole('button', { name: 'Move', exact: true }).click()
  await expect(dialog(page).getByTestId('share-owner')).toContainText(`belongs to the ${access.group} group`)
})

test('a table: share from the table screen and its card, view only for the sharee, leave', async ({ page, browser, access }) => {
  await signIn(page, access.admin)
  await joinGroup(page, access)
  const r = await page.request.post('/api/v1/tables', { data: { name: 'E2E Trip costs', columns: [{ name: 'Item', type: 'text' }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const t = (await r.json()).table as { id: string }
  await page.goto(`/tables/t/${t.id}`)
  // ACT table.share @web.table, ACT table.unshare @web.table
  await page.getByTestId('table-share').click()
  await dialog(page).getByTestId('share-search').fill(access.family.email)
  await dialog(page).getByTestId('share-new-level').selectOption('view')
  await dialog(page).getByRole('button', { name: 'Share with E2E Family' }).click()
  await expect.poll(async () => (await shares(page.request, 'table', t.id)).people.map((p) => p.level)).toEqual(['view'])
  await dialog(page).getByRole('combobox', { name: 'Level for E2E Family' }).selectOption('edit')
  await expect.poll(async () => (await shares(page.request, 'table', t.id)).people.map((p) => p.level)).toEqual(['edit'])
  await dialog(page).getByRole('combobox', { name: 'Level for E2E Family' }).selectOption('view')
  // ACT table.move_group @web.table: Move to group, and out again.
  await dialog(page).getByRole('combobox', { name: 'Group' }).selectOption(access.group)
  await dialog(page).getByRole('button', { name: 'Move', exact: true }).click()
  await expect(dialog(page).getByTestId('share-owner')).toContainText(`belongs to the ${access.group} group`)
  await dialog(page).getByTestId('share-move-out').click()
  await expect(dialog(page).getByTestId('share-owner')).toHaveText('You own this table.')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  const dana = await person(browser, access.family)
  try {
    const p = dana.page
    await p.goto(`/tables/t/${t.id}`)
    await expect(p.getByTestId('table-view-only')).toHaveText('View only · shared by E2E Admin')
    // ACT table.leave @web.table
    await p.getByTestId('table-more').click()
    await p.getByRole('menuitem', { name: 'Remove from my notes' }).click()
    await expect(p.getByTestId('notes-app')).toBeVisible()
    expect((await dana.request.get(`/api/v1/tables/${t.id}`)).status()).toBe(404)
    await p.getByRole('button', { name: 'Undo' }).click()
    await expect.poll(async () => (await dana.request.get(`/api/v1/tables/${t.id}`)).status()).toBe(200)
    // ACT table.share @web.notes, ACT table.leave @web.notes: the table card's ⋮ menu.
    await p.goto('/notes/shared')
    const tc = card(p, 'E2E Trip costs')
    await tc.hover()
    await tc.getByRole('button', { name: 'More' }).click()
    await p.getByRole('menuitem', { name: 'People…' }).click()
    await expect(dialog(p).getByTestId('share-owner')).toHaveText('E2E Admin owns this table.')
    await p.getByRole('button', { name: 'Done', exact: true }).click()
    await tc.hover()
    await tc.getByRole('button', { name: 'More' }).click()
    await p.getByRole('menuitem', { name: 'Remove from my notes' }).click()
    await expect(card(p, 'E2E Trip costs')).toHaveCount(0)
  } finally {
    await dana.close()
  }
  // ACT table.unshare @web.notes: the owner stops sharing from the table's card.
  await page.goto('/notes/tables')
  const own = card(page, 'E2E Trip costs')
  await own.hover()
  await own.getByRole('button', { name: 'More' }).click()
  await page.getByRole('menuitem', { name: 'Share…' }).click()
  // E2E Family removed it from their notes, so the list is empty; share again, then stop.
  await expect(dialog(page).getByTestId('share-none')).toBeVisible()
  await dialog(page).getByTestId('share-search').fill(access.family.email)
  await dialog(page).getByRole('button', { name: 'Share with E2E Family' }).click()
  await dialog(page).getByRole('button', { name: 'Stop sharing with E2E Family' }).click()
  await expect(dialog(page).getByTestId('share-none')).toBeVisible()
})
