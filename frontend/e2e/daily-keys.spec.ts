// Keys and focus in daily use.
// - Opening Ambrose (its button or Ctrl + .) puts the focus in the message box, so typed words
//   never run mail shortcuts; closing it puts the focus back where it was.
// - Inside a compose window letters are never app keys, and Tab goes To → Subject → text
//   (Cc and Bcc are out of the Tab path; Ctrl + Shift + C and B add them).
// - Back from a conversation (u), the list cursor and the focus are on the row that was open.
// Covers: web.shortcuts, web.focus, web.compose.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const rows = (page: Page) => page.locator('[data-mail-list] [role="row"]')
// The window's name follows the subject, so it is found by its class.
const composeWin = (page: Page) => page.locator('.compose-win')

test('Ambrose: the button and Ctrl + . focus the message box; typed letters stay there', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  const before = await rows(page).count()
  const btn = page.locator('.ambrose-btn')
  await btn.click()
  const box = page.getByLabel('Message Ambrose')
  await expect(box).toBeFocused()
  await page.keyboard.type('e#c')
  await expect(box).toHaveValue('e#c')
  await expect(composeWin(page)).toHaveCount(0)
  await expect(rows(page)).toHaveCount(before)
  // Closing gives the focus back to the button that opened it.
  await page.getByRole('button', { name: 'Close panel' }).click()
  await expect(btn).toBeFocused()

  // Ctrl + . from the page: the box again; Ctrl + . in the box closes the panel.
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('Control+.')
  await expect(box).toBeFocused()
  await page.keyboard.press('Control+.')
  await expect(box).toHaveCount(0)
  await page.keyboard.press('j')
  await expect(rows(page).first()).toHaveAttribute('tabindex', '0')
})

test('Compose: Tab goes To, Subject, text; letters on a compose control never open another window', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('c')
  const win = composeWin(page)
  await expect(win).toHaveCount(1)
  const to = win.getByLabel('To recipients')
  await expect(to).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(win.getByLabel('Subject')).toBeFocused()
  await page.keyboard.type('checking in')
  await expect(win.getByLabel('Subject')).toHaveValue('checking in')
  await expect(win.locator('[data-testid="recip-chip"]')).toHaveCount(0)
  await page.keyboard.press('Tab')
  await expect(win.locator('[contenteditable="true"]').first()).toBeFocused()

  // A compose button with the focus: c, e and # do nothing to the app.
  const attach = win.locator('button').filter({ hasNot: page.locator('[disabled]') }).last()
  await attach.focus()
  await page.keyboard.press('c')
  await page.keyboard.press('#')
  await expect(composeWin(page)).toHaveCount(1)

  // Cc and Bcc stay reachable from the keyboard.
  await to.focus()
  await page.keyboard.press('Control+Shift+C')
  await expect(win.getByLabel('Cc recipients')).toBeFocused()
  await page.keyboard.press('Control+Shift+B')
  await expect(win.getByLabel('Bcc recipients')).toBeFocused()
})

test('Back from a conversation (u): the cursor and the focus are on the row that was open', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('j')
  await page.keyboard.press('j')
  const second = rows(page).nth(1)
  await expect(second).toHaveAttribute('tabindex', '0')
  const id = await second.getAttribute('data-id')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/mail\/inbox\/c[0-9A-Za-z]+/)
  await page.keyboard.press('u')
  await expect(page).toHaveURL(/\/mail\/inbox$/)
  const back = rows(page).nth(1)
  await expect(back).toBeFocused()
  expect(await back.getAttribute('data-id')).toBe(id)
  await page.keyboard.press('j')
  await expect(rows(page).nth(2)).toHaveAttribute('tabindex', '0')
})
