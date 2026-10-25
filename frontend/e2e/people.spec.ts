// ACT-SURFACES: web.editor web.calendar web.people
// People and contact groups (web.people is Settings → People & groups): the ranked
// list (person.find) and groups (group.list); group.create, group.add_members, group.rename,
// group.remove_members, group.delete in the People tab; a suggested group accepted with a name
// (group.accept_suggestion), another dismissed (group.dismiss_suggestion), and "Look again"
// (group.suggest); details from a signature accepted and dismissed (person.accept_detail,
// person.dismiss_detail); the address book read now (contacts.sync); group.expand in compose's
// To field and in the calendar guest field; and People addresses as location options
// (event.suggest_location).
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'

test.use({ extras: true })

interface G { id: number; name: string; state: string; members: { email: string }[] }
const groups = async (r: APIRequestContext, state = 'active') => (await (await r.get('/api/v1/groups', { params: { state } })).json()) as G[]

async function makeGroup(r: APIRequestContext, name: string, members: string[]) {
  const res = await r.post('/api/v1/groups', { data: { name, members } })
  expect(res.ok(), await res.text()).toBeTruthy()
  return (await res.json()) as G
}

async function people(page: Page) {
  await page.goto('/settings/people')
  await expect(page.getByRole('heading', { name: 'Groups', exact: true })).toBeVisible()
}

test('group.add_members: Enter acts on the suggestions for what is typed now, never on the last search', async ({ page }) => {
  await people(page)
  await page.getByRole('button', { name: 'New group' }).click()
  const dlg = page.getByRole('dialog', { name: 'New group' })
  await dlg.getByLabel('Group name').fill('E2E Late search')
  const add = dlg.getByLabel('Add member')
  await add.fill('priya')
  await expect(dlg.getByRole('option', { name: /priya@example\.org/ })).toBeVisible()
  // The search for the address answers late: before, Enter took Priya from the list still showing.
  await page.route(/\/api\/v1\/people\?.*q=late/, async (r) => {
    await new Promise((ok) => setTimeout(ok, 2500))
    await r.fallback()
  })
  await add.fill('late.person@example.net')
  await add.press('Enter')
  const members = dlg.getByRole('list', { name: 'Members' })
  await expect(members).toContainText('late.person@example.net')
  await expect(members).not.toContainText('priya@example.org')
  await expect(add).toHaveValue('')
})

test('groups in People: create, rename, remove a member, delete', async ({ page }) => {
  await people(page)
  await page.getByRole('button', { name: 'New group' }).click()
  const dlg = page.getByRole('dialog', { name: 'New group' })
  await dlg.getByLabel('Group name').fill('E2E Committee')
  const add = dlg.getByLabel('Add member')
  await add.fill('priya')
  await dlg.getByRole('option', { name: /priya@example\.org/ }).click()
  await add.fill('marco')
  await dlg.getByRole('option', { name: /marco@example\.org/ }).click()
  await add.fill('new.person@example.net')
  // Enter takes the first suggestion for what is typed now (no person matches: the address).
  await add.press('Enter')
  await dlg.getByRole('radio', { name: 'Sage' }).click()
  await dlg.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await groups(page.request)).map((g) => [g.name, g.members.map((m) => m.email).sort()]))
    .toEqual([['E2E Committee', ['marco@example.org', 'new.person@example.net', 'priya@example.org']]])
  const row = page.getByTestId('group-row').filter({ hasText: 'E2E Committee' })
  await expect(row).toContainText('3 people')

  await row.getByRole('button', { name: 'Edit E2E Committee' }).click()
  const ed = page.getByRole('dialog', { name: 'Edit “E2E Committee”' })
  await ed.getByLabel('Group name').fill('E2E Search committee')
  await ed.getByRole('button', { name: 'Remove new.person@example.net from the group' }).click()
  await ed.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await groups(page.request)).map((g) => [g.name, g.members.length])).toEqual([['E2E Search committee', 2]])

  await page.getByTestId('group-row').filter({ hasText: 'E2E Search committee' }).getByRole('button', { name: /^Edit/ }).click()
  await page.getByRole('dialog', { name: /Edit/ }).getByRole('button', { name: 'Delete' }).click()
  await page.getByRole('dialog', { name: 'Delete “E2E Search committee”?' }).getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => (await groups(page.request)).length).toBe(0)
})

test('suggested groups: accept with a name, dismiss, look again; details from a signature: accept and dismiss', async ({ page }) => {
  await people(page)
  await expect(page.getByTestId('group-suggestion')).toHaveCount(2)
  const other = page.getByTestId('group-suggestion').filter({ hasText: 'Quarterly numbers' })
  await other.getByRole('button', { name: 'Dismiss' }).click()
  const card = page.getByTestId('group-suggestion')
  await expect(card).toHaveCount(1)
  await expect(card).toContainText('3 people')
  await expect(card.getByLabel('Name for the suggested group')).toHaveValue('E2E Reading group')
  await card.getByLabel('Name for the suggested group').fill('E2E Readers')
  await card.getByRole('button', { name: 'Accept' }).click()
  await expect.poll(async () => (await groups(page.request)).map((g) => g.name)).toEqual(['E2E Readers'])
  await expect(page.getByTestId('group-suggestion')).toHaveCount(0)
  expect(await groups(page.request, 'suggested')).toEqual([])
  // Looking again finds nothing new: the accepted set is a group, the dismissed one stays dismissed.
  await page.getByRole('button', { name: 'Look again' }).click()
  await expect(page.getByText('No suggestions right now.')).toBeVisible()

  // Priya's card shows what her signature said.
  await page.getByLabel('Search people').fill('priya')
  await page.getByRole('button', { name: /Priya Natarajan/ }).click()
  const pc = page.getByTestId('person-card')
  await expect(pc.getByTestId('detail-suggestion')).toHaveCount(2)
  await pc.getByRole('button', { name: 'Accept title Head of Research' }).click()
  await expect(pc.getByText('Head of Research', { exact: true })).toBeVisible()
  await expect(pc).toContainText('from signature')
  await pc.getByRole('button', { name: 'Dismiss phone +49 30 1234 5678' }).click()
  await expect(pc.getByTestId('detail-suggestion')).toHaveCount(0)
  const p = await (await page.request.get('/api/v1/people/priya@example.org')).json()
  expect(p.title).toBe('Head of Research')
  expect(p.phones).toEqual([])
  expect(p.suggestions).toEqual([])
})

test('group.expand in compose: the group adds its members under one chip; one member can go', async ({ page }) => {
  await makeGroup(page.request, 'E2E Team', ['priya@example.org', 'marco@example.org', 'lena@example.org'])
  await page.goto('/mail/inbox')
  await page.locator('.compose-btn').click()
  const win = page.locator('.compose-win')
  const to = win.getByLabel('To recipients')
  await to.fill('e2e te')
  await win.getByRole('option', { name: /E2E Team.*3 people/ }).click()
  const chip = win.getByTestId('group-chip')
  await expect(chip).toContainText('E2E Team · 3')
  await chip.getByRole('button', { name: 'Show the members of E2E Team' }).click()
  await chip.getByRole('button', { name: 'Remove marco@example.org' }).click()
  await expect(chip).toContainText('E2E Team · 2 of 3')
  // People autocomplete still ranks individuals; an address typed by hand sits outside the group.
  await to.fill('omar')
  await win.getByRole('option', { name: /omar@example\.net/ }).click()
  await expect(win.locator('.recip-chip').filter({ hasText: 'Omar Haddad' })).toHaveCount(1)
  await chip.getByRole('button', { name: 'Remove the group E2E Team' }).click()
  await expect(win.getByTestId('group-chip')).toHaveCount(0)
  await expect(win.getByRole('button', { name: /^Remove (priya|lena)@/ })).toHaveCount(0)
  await expect(win.getByRole('button', { name: 'Remove omar@example.net' })).toHaveCount(1)
})

test('person.find in compose: Enter takes the suggestion for what is typed now, never the last search\'s', async ({ page }) => {
  await page.goto('/mail/inbox')
  await page.locator('.compose-btn').click()
  const win = page.locator('.compose-win')
  const to = win.getByLabel('To recipients')
  await to.fill('priya')
  await expect(win.getByRole('option', { name: /priya@example\.org/ })).toBeVisible()
  // The search for what comes next answers late; Enter meanwhile waits for it.
  await page.route(/\/api\/v1\/(people|groups)\?.*q=omar/, async (r) => {
    await new Promise((ok) => setTimeout(ok, 2500))
    await r.fallback()
  })
  await to.fill('omar')
  await to.press('Enter')
  await expect(win.locator('.recip-chip').filter({ hasText: 'Omar Haddad' })).toHaveCount(1)
  await expect(win.getByRole('button', { name: 'Remove priya@example.org' })).toHaveCount(0)
})

test('person.find in the guest field: Enter takes the suggestion for what is typed now', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.goto('/calendar/week')
  await expect(page.locator('.cal-tg')).toBeVisible()
  await page.keyboard.press('c')
  await page.getByRole('textbox', { name: 'Event, in one line' }).fill('Lab meeting tomorrow 3pm')
  await page.keyboard.press('Control+Enter')
  const ed = page.getByTestId('cal-editor')
  const add = ed.getByRole('combobox', { name: 'Add guests' })
  await add.fill('priya')
  await expect(ed.getByRole('option', { name: /priya@example\.org/ })).toBeVisible()
  await page.route(/\/api\/v1\/(people|groups)\?.*q=omar/, async (r) => {
    await new Promise((ok) => setTimeout(ok, 2500))
    await r.fallback()
  })
  await add.fill('omar')
  await add.press('Enter')
  await expect(ed.getByText('omar@example.net').or(ed.getByText('Omar Haddad')).first()).toBeVisible()
  await expect(ed.getByText('priya@example.org')).toHaveCount(0)
})

test('group.expand in the guest field, and People addresses as location options', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await makeGroup(page.request, 'E2E Lab', ['lena@example.org', 'sam@example.org'])
  await page.goto('/calendar/week')
  await expect(page.locator('.cal-tg')).toBeVisible()
  await page.keyboard.press('c')
  await page.getByRole('textbox', { name: 'Event, in one line' }).fill('Lab meeting tomorrow 3pm')
  await page.keyboard.press('Control+Enter')
  const ed = page.getByTestId('cal-editor')
  const add = ed.getByRole('combobox', { name: 'Add guests' })
  await add.fill('lab')
  await ed.getByRole('option', { name: /E2E Lab.*2 people/ }).click()
  const chip = ed.getByTestId('group-chip')
  await expect(chip).toContainText('E2E Lab · 2')
  await chip.getByRole('button', { name: 'Show the members of E2E Lab' }).click()
  await expect(ed.getByTestId('cal-guest')).toHaveCount(2)
  await chip.getByRole('button', { name: 'Remove Sam Guest' }).click()
  await expect(chip).toContainText('E2E Lab · 1 of 2')
  // Lena (a guest) has a postal address in People: it is offered for the location.
  await expect(ed.locator('#cal-places option[data-testid="place-option"]')).toHaveAttribute('value', '12 Lindenstrasse, 10115 Berlin')
})

test('contacts.sync: Read now reads the calendar server\'s address book (read-only)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await people(page)
  const book = page.getByTestId('address-book')
  await expect(book).toContainText('Not read yet')
  await page.getByRole('button', { name: 'Read now' }).click()
  await expect(book).toContainText('0 contacts · 0 groups')
})
