// ACT-SURFACES: web.reader
// The reader's header: the subject, the labels, the suggested
// action and the linked notes and tables flow as one run of chips; nothing linked means no links row.
// The toolbar's Notes menu links a note or table (ACT note.link, ACT table.link), makes a note or a
// checklist from the email (ACT note.from_email); × on a chip unlinks with undo (ACT note.unlink).
// At phone width the same menu is in ⋮. Every theme draws the same header.
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { THEMES } from './matrix'

async function linkedNotes(request: APIRequestContext, cid: number) {
  const r = await request.get('/api/v1/notes/linked', { params: { target_kind: 'conversation', conversation_id: cid } })
  return ((await r.json()) as { notes: { id: string; title: string; kind: string }[] }).notes
}
async function openConv(page: Page, cid: number, subject: string) {
  await page.goto(`/mail/inbox/${cid}`)
  await expect(page.getByRole('heading', { name: new RegExp(subject) })).toBeVisible()
}
const run = (page: Page) => page.locator('.conv-title-run')

test('the suggested action and a linked note sit on the subject line; × unlinks with undo', async ({ page, seeded }) => {
  const cid = seeded.conversations['E2E Track me']
  const r = await page.request.post('/api/v1/notes', { data: { kind: 'text', title: 'E2E Header note' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await openConv(page, cid, 'E2E Track me')
  // mail.primary_action: on the subject's run, after the labels, not on a line of its own.
  const chip = run(page).locator('.conv-primary-action').getByRole('link', { name: 'Track package' })
  await expect(chip).toBeVisible()
  // Nothing linked: no links row and no "Link a note or table" button under the subject.
  await expect(page.getByTestId('linked-panel')).toHaveCount(0)
  await expect(page.locator('.conv-scroll').getByRole('button', { name: 'Link a note or table' })).toHaveCount(0)

  // ACT note.link @web.reader: Create from email → Link to a note or table… → search → pick.
  await page.getByTestId('reader-create').click()
  await page.getByRole('menuitem', { name: 'Link to a note or table…' }).click()
  const picker = page.getByTestId('note-link-picker')
  await picker.getByRole('searchbox').fill('E2E Header note')
  await picker.getByRole('option').filter({ hasText: 'E2E Header note' }).click()
  const note = run(page).getByTestId('linked-panel').getByTestId('linked-note')
  await expect(note).toContainText('E2E Header note')
  await expect.poll(async () => (await linkedNotes(page.request, cid)).map((n) => n.title)).toEqual(['E2E Header note'])
  // One line: the chip's top is above the bottom of the subject heading's line.
  const h = (await page.getByRole('heading', { name: /E2E Track me/ }).boundingBox())!
  const c = (await note.boundingBox())!
  expect(c.y).toBeLessThan(h.y + h.height)

  // ACT note.unlink @web.reader: × with the undo toast.
  await page.getByRole('button', { name: 'Unlink E2E Header note' }).click()
  await expect(note).toHaveCount(0)
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).last().click()
  await expect(note).toContainText('E2E Header note')
})

test('Create from email makes a checklist and a note from the email, both linked', async ({ page, seeded }) => {
  const cid = seeded.conversations['E2E Read me']
  await openConv(page, cid, 'E2E Read me')
  // ACT note.from_email @web.reader: a checklist; the toast offers Open and Undo.
  await page.getByTestId('reader-create').click()
  await page.getByRole('menuitem', { name: 'Checklist', exact: true }).click()
  await expect(page.locator('.toast').filter({ hasText: 'Checklist created' })).toBeVisible()
  await expect.poll(async () => (await linkedNotes(page.request, cid)).map((n) => n.kind)).toEqual(['checklist'])
  await expect(run(page).getByTestId('linked-note')).toHaveCount(1)
  // A note: it opens in the editor, linked to the email.
  await page.getByTestId('reader-create').click()
  await page.getByRole('menuitem', { name: 'Note', exact: true }).click()
  await expect(page.getByTestId('note-editor')).toBeVisible()
  await expect.poll(async () => (await linkedNotes(page.request, cid)).length).toBe(2)
})

test('at phone width ⋮ holds Create from email, Report spam and Ask assistant, and the toolbar fits', async ({ page, seeded }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openConv(page, seeded.conversations['E2E Track me'], 'E2E Track me')
  const head = page.locator('.conv-view-head')
  const more = head.getByRole('button', { name: 'More' })
  await expect(more).toBeInViewport({ ratio: 1 })
  await expect(page.getByTestId('reader-create')).toBeHidden()
  await more.click()
  await expect(page.getByRole('menuitem', { name: 'Report spam' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Ask Ambrose' })).toBeVisible()
  await page.getByRole('menuitem', { name: 'Create from email' }).click()
  for (const name of ['Task', 'Event', 'Meeting with these people', 'Note', 'Checklist', 'Link to a note or table…']) {
    await expect(page.getByRole('menuitem', { name, exact: true })).toBeVisible()
  }
})

for (const theme of THEMES) {
  test(`${theme} theme: one header run and the Create from email button`, async ({ page, seeded }) => {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    const cid = seeded.conversations['E2E Track me']
    const r = await page.request.post('/api/v1/notes', { data: { kind: 'checklist', title: `E2E ${theme} list` } })
    const id = ((await r.json()) as { note: { id: string } }).note.id
    expect((await page.request.post(`/api/v1/notes/${id}/links`, { data: { target_kind: 'conversation', target_ref: { conversation_id: cid } } })).ok()).toBeTruthy()
    await openConv(page, cid, 'E2E Track me')
    await expect(run(page).locator('.conv-primary-action')).toBeVisible()
    await expect(run(page).getByTestId('linked-note')).toContainText(`E2E ${theme} list`)
    await expect(page.locator('.conv-view-head').getByTestId('reader-create')).toBeVisible()
    // The chips follow the subject as one run: the note chip is on the action's line or the next one
    // (a narrow reading pane wraps it), never on a links row of its own further down.
    const a = (await run(page).locator('.conv-primary-action').boundingBox())!
    const n = (await run(page).getByTestId('linked-note').boundingBox())!
    expect(n.y - a.y).toBeLessThan(40)
    const first = (await page.locator('[data-message-id]').first().boundingBox())!
    expect(n.y).toBeLessThan(first.y)
  })
}
