// ACT-SURFACES: web.list
// The mail list's actions (surface web.list): hover buttons on a row, the
// selection toolbar, and the row's star. Each test checks the result through the API.
// Covers: mail.archive, mail.delete, mail.read, mail.unread, mail.star, mail.spam, mail.not_spam,
// mail.snooze, mail.label, mail.move, mail.mute, mail.important, mail.untrash, mail.delete_forever. Also the selection rules: Shift ranges, and actions keep rows selected meanwhile.
import { conv, expect, roles, test } from './fixtures'

const row = (page: import('@playwright/test').Page, subject: string) =>
  page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

async function select(page: import('@playwright/test').Page, subject: string) {
  const r = row(page, subject)
  await r.hover()
  await r.locator('[aria-label="Select"]').first().click()
}

test.beforeEach(async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Reply to me')).toBeVisible()
})

test('mail.archive and mail.delete from a row', async ({ page }) => {
  const r = await roles(page.request)
  await row(page, 'E2E Archive me').hover()
  await row(page, 'E2E Archive me').getByRole('button', { name: 'Archive' }).click()
  await expect(row(page, 'E2E Archive me')).toHaveCount(0)
  // The row leaves at once; the server answers when it is done.
  await expect.poll(async () => (await conv(page.request, 'E2E Archive me')).label_ids).not.toContain(r.inbox)

  await row(page, 'E2E Delete me').hover()
  await row(page, 'E2E Delete me').getByRole('button', { name: 'Delete' }).click()
  await expect(row(page, 'E2E Delete me')).toHaveCount(0)
  await expect.poll(async () => (await conv(page.request, 'E2E Delete me')).label_ids).toContain(r.trash)
})

test('mail.read and mail.unread from a row, mail.star from the star', async ({ page }) => {
  await row(page, 'E2E Read me').hover()
  await row(page, 'E2E Read me').getByRole('button', { name: 'Mark as read' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Read me')).unread_count).toBe(0)
  await row(page, 'E2E Read me').hover()
  await row(page, 'E2E Read me').getByRole('button', { name: 'Mark as unread' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Read me')).unread_count).toBe(1)

  await row(page, 'E2E Star me').getByRole('button', { name: 'Star', exact: true, pressed: false }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Star me')).starred).toBe(true)
})

// Acting quickly: the second row is selected while the spam action is still running. The snooze
// must act on it (it used to run on nothing: completion cleared the whole selection).
test('mail.spam, mail.snooze and mail.label from the selection toolbar', async ({ page }) => {
  const r = await roles(page.request)
  await select(page, 'E2E Spam me')
  await page.getByRole('button', { name: 'Report spam' }).first().click()
  await expect.poll(async () => (await conv(page.request, 'E2E Spam me')).label_ids).toContain(r.junk)

  await select(page, 'E2E Snooze me')
  await page.getByRole('toolbar', { name: 'Conversation actions' }).getByRole('button', { name: 'Snooze' }).click()
  await page.getByRole('menuitem', { name: /Tomorrow/ }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Snooze me')).snoozed_until).not.toBeNull()

  await select(page, 'E2E Label me')
  await page.getByRole('button', { name: 'Labels' }).first().click()
  await page.getByRole('menuitemcheckbox', { name: 'E2E Label' }).or(page.getByRole('menuitem', { name: 'E2E Label' })).first().click()
  await page.keyboard.press('Escape')
  await expect.poll(async () => (await conv(page.request, 'E2E Label me')).label_ids.length).toBeGreaterThan(1)
})

test('mail.move, mail.mute and mail.important from the selection toolbar', async ({ page, seeded }) => {
  const r = await roles(page.request)
  const toolbar = page.getByRole('toolbar', { name: 'Conversation actions' })
  await select(page, 'E2E Move me')
  await toolbar.getByRole('button', { name: 'Move to folder' }).click()
  await page.getByRole('menuitem', { name: 'E2E Other' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Move me')).label_ids).toContain(seeded.labels['E2E Other'])
  expect((await conv(page.request, 'E2E Move me')).label_ids).not.toContain(r.inbox)

  await select(page, 'E2E Mute me')
  await toolbar.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Mute' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Mute me')).muted).toBe(true)

  await select(page, 'E2E Important me')
  await toolbar.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Mark as important' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Important me')).important).toBe(true)
})

test('mail.not_spam from the selection toolbar in Spam', async ({ page }) => {
  const r = await roles(page.request)
  await page.goto('/mail/spam')
  await select(page, 'E2E Not spam me')
  await page.getByRole('toolbar', { name: 'Conversation actions' }).getByRole('button', { name: 'Not spam' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Not spam me')).label_ids).toContain(r.inbox)
  expect((await conv(page.request, 'E2E Not spam me')).label_ids).not.toContain(r.junk)
})

// Trash's own actions. A row's Restore (and the toolbar's Move to Inbox) takes it back; Delete
// forever asks first, then the conversation is gone from every view.
test('mail.untrash and mail.delete_forever in Trash', async ({ page }) => {
  const r = await roles(page.request)
  for (const subject of ['E2E Delete me', 'E2E Archive me']) {
    await row(page, subject).hover()
    await row(page, subject).getByRole('button', { name: 'Delete' }).click()
    await expect.poll(async () => (await conv(page.request, subject)).label_ids).toContain(r.trash)
  }
  await page.goto('/mail/trash')
  await row(page, 'E2E Delete me').hover()
  await row(page, 'E2E Delete me').getByRole('button', { name: 'Restore' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Delete me')).label_ids).toContain(r.inbox)
  expect((await conv(page.request, 'E2E Delete me')).label_ids).not.toContain(r.trash)

  await select(page, 'E2E Archive me')
  await page.getByRole('toolbar', { name: 'Conversation actions' }).getByRole('button', { name: 'Delete forever' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete forever' }).click()
  await expect(row(page, 'E2E Archive me')).toHaveCount(0)
  await expect.poll(async () => {
    try {
      await conv(page.request, 'E2E Archive me')
      return 'still there'
    } catch {
      return 'gone'
    }
  }).toBe('gone')
})

// Desktop selection: Shift-click applies the clicked row's new state to the range from the anchor,
// so it can also clear a range, including after "select everything on the page".
test('shift-click selects and clears ranges', async ({ page }) => {
  const checked = async () => (await page.locator('[data-mail-list] [role="row"][aria-selected="true"]').allTextContents()).length
  await row(page, 'E2E Mute me').locator('[aria-label="Select"]').click()
  await row(page, 'E2E Label me').click({ modifiers: ['Shift'], position: { x: 600, y: 10 } })
  await expect.poll(checked).toBe(3) // Mute, Move, Label (newest first: Important, Mute, Move, Label…)
  await row(page, 'E2E Move me').click({ modifiers: ['Shift'], position: { x: 600, y: 10 } })
  await expect.poll(checked).toBe(1) // Move was checked, so Label..Move is cleared; Mute stays
  // With some rows selected the master checkbox first clears (as in Gmail); a second click selects all.
  await page.getByRole('checkbox', { name: /Select all|Deselect all/ }).click()
  if ((await checked()) <= 3) await page.getByRole('checkbox', { name: /Select all|Deselect all/ }).click()
  await expect.poll(checked).toBeGreaterThan(3)
  const all = await checked()
  await row(page, 'E2E Mute me').click({ modifiers: ['Control'], position: { x: 600, y: 10 } })
  await row(page, 'E2E Label me').click({ modifiers: ['Shift'], position: { x: 600, y: 10 } })
  await expect.poll(checked).toBe(all - 3) // Mute..Label cleared, not everything reselected
})
