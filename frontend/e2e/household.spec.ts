// ACT-SURFACES: web.settings web.setup web.notes web.calendar web.people
// Household onboarding and imports: an admin makes an invite (household.invite)
// and withdraws one (household.invite_revoke); the invited person chooses a password on /join and
// lands in the setup guide (household.join). Google Keep into Notes (note.import_keep), an .ics file
// (event.import_ics) and a .vcf file (contacts.import_vcf) each preview first and undo in one step
// (import.undo); the Calendar ⋮ offers a calendar link (calendar.subscribe; calendar.unsubscribe is
// in the calendar's own menu once one is there).
import { accessTest, connectCalendar, expect, signIn, test } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

accessTest('household.invite, household.join: an invite link leads to a password and the setup guide; household.invite_revoke', async ({ page, browser, access }) => {
  await signIn(page, access.admin)
  await page.goto('/settings/admin/users')
  await page.getByRole('button', { name: 'Invite someone' }).click()
  const d = page.getByRole('dialog', { name: 'Invite someone' })
  const email = `cleo-${Date.now()}@example.com`
  await d.getByLabel('Email', { exact: true }).fill(email)
  await d.getByLabel('Name', { exact: true }).fill('Cleo Example')
  await d.getByRole('button', { name: 'Create link' }).click()
  const made = page.getByTestId('invite-made')
  await expect(made.getByRole('img', { name: /QR code/ })).toBeVisible()
  const url = await made.getByRole('textbox', { name: 'Invite link' }).inputValue()
  expect(url).toMatch(/\/join\/[A-Za-z0-9_-]{20,}$/)
  await page.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByTestId('invites-list')).toContainText(email)

  // The member, in a browser of their own.
  const ctx = await browser.newContext()
  const member = await ctx.newPage()
  await member.goto(new URL(url).pathname)
  await expect(member.getByRole('heading', { name: 'Welcome, Cleo' })).toBeVisible()
  await member.getByLabel(/^Password/).fill('a-good-password-1')
  await member.getByLabel('Type it again').fill('a-good-password-1')
  await member.getByRole('button', { name: 'Start' }).click()
  await expect(member).toHaveURL(/\/setup/)
  await ctx.close()

  // Used now; a second invite can be withdrawn.
  await page.reload()
  await expect(page.getByTestId('invites-list')).toContainText('Joined')
  await page.getByRole('button', { name: 'Invite someone' }).click()
  await d.getByLabel('Email', { exact: true }).fill(`kid-${Date.now()}@example.com`)
  await d.getByRole('button', { name: 'Create link' }).click()
  await page.getByRole('button', { name: 'Done' }).click()
  await page.getByRole('button', { name: /Withdraw the invite for kid-/ }).click()
  await expect(page.getByText('Invite withdrawn. The link no longer works.')).toBeVisible()
})

const keep = (name: string, body: Record<string, unknown>) => ({ name, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(body)) })

test('note.import_keep, import.undo: Keep notes preview, import, and come back out with Undo', async ({ page }) => {
  await page.goto('/notes')
  await page.getByTestId('notes-more').click()
  await page.getByRole('menuitem', { name: /Import from Google Keep/ }).click()
  await page.getByTestId('import-file').setInputFiles([
    keep('Groceries.json', { title: 'E2E Keep groceries', listContent: [{ text: 'Milk', isChecked: false }], labels: [{ name: 'E2E Keep' }] }),
    keep('Idea.json', { title: 'E2E Keep idea', textContent: 'Paint the fence' }),
    keep('Old.json', { title: 'E2E Keep old', textContent: 'x', isTrashed: true }),
  ])
  const pv = page.getByTestId('import-preview')
  await expect(pv).toContainText('1 note')
  await expect(pv).toContainText('1 checklist')
  await expect(pv).toContainText("1 note from Keep's trash")
  await page.getByTestId('import-run').click()
  await expect(page.getByText('Imported 2 notes from Google Keep')).toBeVisible()
  await expect(page.getByText('E2E Keep groceries').first()).toBeVisible()
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('Import undone.')).toBeVisible()
  await expect(page.getByText('E2E Keep groceries')).toHaveCount(0)
})

test('contacts.import_vcf: a .vcf file previews its people from People ⋮', async ({ page }) => {
  await page.goto('/settings/people')
  await page.getByTestId('people-more').click()
  await page.getByRole('menuitem', { name: /Import contacts/ }).click()
  const vcf = 'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:E2E Vcf Person\r\nEMAIL:vcf-person@example.com\r\nEND:VCARD\r\n'
  await page.getByTestId('import-file').setInputFiles({ name: 'contacts.vcf', mimeType: 'text/vcard', buffer: Buffer.from(vcf) })
  await expect(page.getByTestId('import-preview')).toContainText('1 new person')
  await page.getByTestId('import-run').click()
  await expect(page.getByText('Imported 1 person')).toBeVisible()
})

test('event.import_ics, calendar.subscribe: the Calendar ⋮ holds the import and the calendar link', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.goto('/calendar')
  await page.getByTestId('cal-more').click()
  await expect(page.getByRole('menuitem', { name: /Import an \.ics file/ })).toBeVisible()
  await page.getByRole('menuitem', { name: /Subscribe to a calendar link/ }).click()
  const d = page.getByTestId('subscribe-dialog')
  await d.getByLabel(/Calendar link/).fill('ftp://example.com/x.ics')
  await page.getByTestId('subscribe-go').click()
  await expect(page.getByRole('alert')).toContainText('https://')
})
