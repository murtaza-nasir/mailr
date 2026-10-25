// ACT-SURFACES: web.editor web.people
// Text typed into an address field is never dropped by Save or Send, even when Enter is still
// waiting for the suggestions (the people search answers late here on purpose). An address
// becomes a guest, recipient or member; other text stops Save with a message on the field.
// Fields: the event editor's guests (Find a time reads the same list), compose To on Send and on
// Ctrl+Enter, and a contact group's "Add member". Notes sharing has no typed field: each person
// is added with their own button.
import type { Page } from '@playwright/test'
import { at, agenda, createEvent, dayOf, openIn } from './calendar-data'
import { apiSettled, connectCalendar, expect, outbox, test } from './fixtures'

/** The people and group searches answer after 2.5 s, so Enter is still waiting when Save runs. */
async function slowSearch(page: Page) {
  await page.route(/\/api\/v1\/(people|groups)\?/, async (r) => {
    await new Promise((ok) => setTimeout(ok, 2500))
    await r.fallback().catch(() => {})
  })
}

test('event editor: Save takes the typed guest at once; text that is not an address stops Save', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const ev = await createEvent(page.request, { summary: 'E2E Typed guest', start: at(2, 10), end: at(2, 11), tz })
  const guests = async () => (await agenda(page.request)).find((e) => e.uid === ev.uid)?.attendee_list.map((a) => a.email) ?? []
  await slowSearch(page)
  await openIn(page, 'classic', `/calendar/week/${dayOf(2)}`)
  const ed = page.getByTestId('cal-editor')
  // An event the user organises asks before mailing the new guest; answer "Don't send".
  const saved = async () => {
    const ask = page.getByRole('dialog', { name: /^Send / })
    await Promise.race([ask.waitFor().then(() => ask.getByRole('button', { name: "Don't send" }).click()), ed.waitFor({ state: 'detached' })])
    await expect(ed).toHaveCount(0)
  }
  const edit = async () => {
    // The page reloads the event after a save; an editor opened on the older copy would meet a
    // newer version on Save (the conflict prompt), so it opens once that reload is in.
    await apiSettled(page, /\/api\/v1\/calendar\//)
    await page.getByTestId('cal-event').filter({ hasText: 'E2E Typed guest' }).first().click()
    await page.getByTestId('cal-detail').getByRole('button', { name: 'Edit (e)' }).click()
    await expect(ed.getByRole('textbox', { name: 'Title' })).toHaveValue('E2E Typed guest')
  }

  // Enter, then Save straight away: Enter is still waiting for the suggestions.
  await edit()
  const add = ed.getByRole('combobox', { name: 'Add guests' })
  await add.fill('priya@example.org')
  await add.press('Enter')
  await ed.getByTestId('cal-editor-save').click()
  await saved()
  await expect.poll(guests).toContain('priya@example.org')

  // No Enter at all: Save still takes the address.
  await edit()
  await add.fill('sam.typed@example.net')
  await ed.getByTestId('cal-editor-save').click()
  await saved()
  await expect.poll(guests).toContain('sam.typed@example.net')

  // Not an address: Save stops and the field says why; nothing is written.
  await edit()
  await add.fill('somebody')
  await ed.getByTestId('cal-editor-save').click()
  await expect(ed.getByTestId('cal-guests-bad')).toContainText('“somebody” is not an email address')
  await expect(add).toHaveAttribute('aria-invalid', 'true')
  await expect(ed).toBeVisible()
  expect(await guests()).not.toContain('somebody')
})

async function newMessage(page: Page, subject: string) {
  await page.goto('/mail/inbox')
  await page.locator('.compose-btn').click()
  const win = page.locator('.compose-win')
  await expect(win).toBeVisible()
  await win.getByRole('textbox', { name: 'Subject' }).fill(subject)
  return win
}

async function sentTo(page: Page, subject: string, run: () => Promise<void>): Promise<string[]> {
  const req = page.waitForRequest((r) => r.method() === 'POST' && /\/api\/v1\/send$/.test(r.url()))
  await run()
  const body = (await req).postDataJSON() as { payload: { to: { email: string }[] } }
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === subject).length).toBe(1)
  const ob = (await outbox(page.request)).find((o) => o.subject === subject)!
  expect((await page.request.post(`/api/v1/outbox/${ob.id}/cancel`)).ok()).toBeTruthy()
  return body.payload.to.map((a) => a.email)
}

test('compose: Send and Ctrl+Enter take the address still typed in To', async ({ page }) => {
  await slowSearch(page)
  // Enter waiting for the suggestions, then Send.
  let win = await newMessage(page, 'E2E Typed send')
  let to = win.getByLabel('To recipients')
  await to.fill('pat.typed@example.net')
  await to.press('Enter')
  expect(await sentTo(page, 'E2E Typed send', () => win.getByRole('button', { name: 'Send', exact: true }).click()))
    .toEqual(['pat.typed@example.net'])

  // Ctrl+Enter in the To field, with the address not yet a chip.
  win = await newMessage(page, 'E2E Typed ctrl enter')
  to = win.getByLabel('To recipients')
  await to.fill('lee.typed@example.net')
  expect(await sentTo(page, 'E2E Typed ctrl enter', () => to.press('Control+Enter'))).toEqual(['lee.typed@example.net'])
})

test('contact group: Save keeps the member still typed; text that is not an address stops Save', async ({ page }) => {
  await slowSearch(page)
  await page.goto('/settings/people')
  await page.getByRole('button', { name: 'New group' }).click()
  const dlg = page.getByRole('dialog', { name: 'New group' })
  await dlg.getByLabel('Group name').fill('E2E Typed group')
  const add = dlg.getByLabel('Add member')

  await add.fill('nobody here')
  await dlg.getByRole('button', { name: 'Save' }).click()
  await expect(dlg.getByTestId('grp-add-bad')).toContainText('“nobody here” is not an email address')
  await expect(dlg).toBeVisible()

  await add.fill('kim.typed@example.net')
  await add.press('Enter')
  await dlg.getByRole('button', { name: 'Save' }).click()
  await expect(dlg).toHaveCount(0)
  await expect.poll(async () => {
    const gs = (await (await page.request.get('/api/v1/groups', { params: { state: 'active' } })).json()) as { name: string; members: { email: string }[] }[]
    return gs.find((g) => g.name === 'E2E Typed group')?.members.map((m) => m.email)
  }).toEqual(['kim.typed@example.net'])
})
