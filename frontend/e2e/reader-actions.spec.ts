// ACT-SURFACES: web.reader
// The reader's actions (surface web.reader): the toolbar above an open
// conversation. Each test opens one seeded email, acts, and checks the result through the API.
// Covers: mail.archive, mail.delete, mail.spam, mail.not_spam, mail.read, mail.unread, mail.star,
// mail.snooze, mail.label, mail.move, mail.mute, mail.important, mail.reply, mail.forward.
import { conv, expect, roles, test } from './fixtures'
import { openConversation } from './helpers'

const open = (page: import('@playwright/test').Page, subject: string, view = 'inbox') => openConversation(page, subject, { view })
const bar = (page: import('@playwright/test').Page) => page.locator('.conv-toolbar, .conv-view').first()

test('mail.archive, mail.delete and mail.spam from the reader', async ({ page }) => {
  const r = await roles(page.request)
  await open(page, 'E2E Archive me')
  await bar(page).getByRole('button', { name: 'Archive' }).first().click()
  await expect.poll(async () => (await conv(page.request, 'E2E Archive me')).label_ids).not.toContain(r.inbox)

  await open(page, 'E2E Delete me')
  await bar(page).getByRole('button', { name: 'Delete' }).first().click()
  await expect.poll(async () => (await conv(page.request, 'E2E Delete me')).label_ids).toContain(r.trash)

  await open(page, 'E2E Spam me')
  await bar(page).getByRole('button', { name: 'Report spam' }).first().click()
  await expect.poll(async () => (await conv(page.request, 'E2E Spam me')).label_ids).toContain(r.junk)
})

test('mail.unread, mail.snooze and mail.label from the reader', async ({ page }) => {
  await open(page, 'E2E Unread me')
  await expect.poll(async () => (await conv(page.request, 'E2E Unread me')).unread_count).toBe(0) // opening reads it
  await bar(page).getByRole('button', { name: 'Mark as unread' }).first().click()
  await expect.poll(async () => (await conv(page.request, 'E2E Unread me')).unread_count).toBe(1)

  await open(page, 'E2E Snooze me')
  await bar(page).getByRole('button', { name: 'Snooze' }).first().click()
  await page.getByRole('menuitem', { name: /Tomorrow/ }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Snooze me')).snoozed_until).not.toBeNull()

  await open(page, 'E2E Label me')
  const before = (await conv(page.request, 'E2E Label me')).label_ids.length
  await bar(page).getByRole('button', { name: 'Labels' }).first().click()
  await page.getByRole('menuitemcheckbox', { name: 'E2E Label' }).click()
  await page.keyboard.press('Escape')
  await expect.poll(async () => (await conv(page.request, 'E2E Label me')).label_ids.length).toBeGreaterThan(before)
})

test('mail.move, mail.mute and mail.important from the reader', async ({ page, seeded }) => {
  const r = await roles(page.request)
  await open(page, 'E2E Move me')
  await bar(page).getByRole('button', { name: 'Move to folder' }).first().click()
  await page.getByRole('menuitem', { name: 'E2E Other' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Move me')).label_ids).toContain(seeded.labels['E2E Other'])
  expect((await conv(page.request, 'E2E Move me')).label_ids).not.toContain(r.inbox)

  await open(page, 'E2E Mute me')
  await bar(page).getByRole('button', { name: 'More', exact: true }).first().click()
  await page.getByRole('menuitem', { name: /^Mute/ }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Mute me')).muted).toBe(true)

  await open(page, 'E2E Important me')
  await page.getByRole('button', { name: /Mark (as )?important|Not important/ }).first().click()
  await expect.poll(async () => (await conv(page.request, 'E2E Important me')).important).toBe(true)
})

test('mail.reply opens an inline reply addressed to the sender', async ({ page }) => {
  await open(page, 'E2E Reply to me')
  await page.getByRole('button', { name: 'Reply' }).last().click()
  const reply = page.locator('[data-inline-reply]')
  await expect(reply).toBeVisible()
  await expect(reply).toContainText('Dana Tester')
})

test('mail.not_spam, mail.read and mail.star from the reader', async ({ page }) => {
  const r = await roles(page.request)
  await open(page, 'E2E Not spam me', 'spam')
  await bar(page).getByRole('button', { name: 'Not spam' }).first().click()
  await expect.poll(async () => (await conv(page.request, 'E2E Not spam me')).label_ids).toContain(r.inbox)
  expect((await conv(page.request, 'E2E Not spam me')).label_ids).not.toContain(r.junk)

  // Opening an unread conversation reads it.
  expect((await conv(page.request, 'E2E Read me')).unread_count).toBe(1)
  await open(page, 'E2E Read me')
  await expect.poll(async () => (await conv(page.request, 'E2E Read me')).unread_count).toBe(0)

  await open(page, 'E2E Star me')
  await page.getByRole('article').getByRole('button', { name: 'Star', exact: true, pressed: false }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Star me')).starred).toBe(true)
})

test('mail.forward opens an inline forward that carries the original', async ({ page }) => {
  await open(page, 'E2E Forward me')
  await page.locator('.reply-bar').getByRole('button', { name: 'Forward' }).click()
  const fwd = page.locator('[data-inline-reply]')
  await expect(fwd).toBeVisible()
  await expect(fwd).toContainText('Forwarded message')
  await expect(fwd).toContainText('Body of E2E Forward me.')
})
