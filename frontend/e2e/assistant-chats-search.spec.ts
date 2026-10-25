// ACT-SURFACES: web.panel
// Chats: search and rename, against the real server (features/rail/assistant/ChatList.tsx,
// useRenameChat.ts; backend agent/chat_search.py). The seed's extras hold three chats: "Dinner plans"
// (newest), "Plan the team offsite" and "Which invoices are still unpaid?" (a day old).
// ACT agent.chat_search: a word that is only in an answer finds its chat, with the text around it and
// the word marked; Esc clears; a search with no match says so. ACT agent.chat_rename: from a row's
// menu (a field in the row, Enter saves, Undo in the toast) and by double-clicking the title in the
// panel's header; the keyboard reaches all of it (/, Ctrl+F, arrows, F2, Esc).
import fs from 'node:fs'
import type { Page } from '@playwright/test'
import { apiSettled, expect, test } from './fixtures'

const SHOTS = process.env.CHATS_SHOTS
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true })

test.use({ extras: true })

async function openChats(page: Page, theme = 'classic') {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await expect(page.locator('.side-panel .chat')).toBeVisible()
  await page.locator('.side-panel-head').getByRole('button', { name: 'Chats' }).click()
  await expect(page.getByTestId('chats').locator('[data-chat-row]')).toHaveCount(3)
}

const rows = (page: Page) => page.getByTestId('chats').locator('[data-chat-row]')
const search = (page: Page) => page.getByRole('searchbox', { name: 'Search chats' })

test('search finds a chat by a word in an answer, marks it, and Esc clears', async ({ page }) => {
  await openChats(page)
  await search(page).fill('harbour')
  await expect(rows(page)).toHaveCount(1)
  const row = rows(page).first()
  await expect(row).toContainText('Plan the team offsite')
  const snippet = row.getByTestId('chat-snippet')
  await expect(snippet).toContainText('The harbour venue holds 40 people')
  await expect(snippet.locator('.chats-hit')).toHaveText(['harbour'])
  // Screen readers hear how many were found.
  await expect(page.getByTestId('chats-found')).toHaveText('1 chat found')
  if (SHOTS) await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-search-light-classic.png` })

  // The last word is a prefix; Markdown and citation marks are not in the snippet.
  await search(page).fill('northw')
  await expect(rows(page)).toHaveCount(2) // the invoices answer and the dinner answer
  await expect(rows(page).last().getByTestId('chat-snippet')).toContainText('Two: Northwind Hosting for September and the Acme print run.')
  await expect(rows(page).last().getByTestId('chat-snippet')).not.toContainText('**')

  await search(page).fill('zebracorn')
  await expect(page.getByTestId('chats-no-match')).toContainText('No chats match “zebracorn”.')

  // Esc in the box clears it (the list stays); Esc again goes back to the chat.
  await search(page).press('Escape')
  await expect(search(page)).toHaveValue('')
  await expect(rows(page)).toHaveCount(3)
  await search(page).press('Escape')
  await expect(page.getByTestId('chats')).toHaveCount(0)
})

test('rename from the row menu; Undo puts the title back', async ({ page }) => {
  await openChats(page)
  const item = page.getByTestId('chats').locator('.chats-item', { hasText: 'Which invoices are still unpaid?' })
  await item.hover()
  await item.getByRole('button', { name: 'More options for Which invoices are still unpaid?' }).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const field = page.getByRole('textbox', { name: 'Chat title' })
  await expect(field).toBeFocused()
  await expect(field).toHaveValue('Which invoices are still unpaid?')
  if (SHOTS) await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-rename-light-classic.png` })
  await field.fill('Unpaid invoices')
  await field.press('Enter')
  await expect(rows(page).nth(2)).toContainText('Unpaid invoices')
  await expect(rows(page).nth(2)).toBeFocused() // the focus is back on the row
  const toastEl = page.locator('.toast', { hasText: 'Chat renamed.' })
  await expect(toastEl).toBeVisible()
  await apiSettled(page, /agent\/sessions/)

  // The server kept it, and the search finds the new title. The chat keeps its place.
  await search(page).fill('unpaid')
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page).first().locator('.chats-title .chats-hit')).toHaveText(['Unpaid'])
  await search(page).fill('')
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).nth(2)).toContainText('Unpaid invoices')

  await toastEl.getByRole('button', { name: 'Undo' }).click()
  await expect(rows(page).nth(2)).toContainText('Which invoices are still unpaid?')
  await apiSettled(page, /agent\/sessions/)
  // Read again from the server: the first question is the title again.
  await page.getByTestId('chats-back').click()
  await page.locator('.side-panel-head').getByRole('button', { name: 'Chats' }).click()
  await expect(rows(page).nth(2)).toContainText('Which invoices are still unpaid?')
})

test('keyboard: / and Ctrl+F reach the box, arrows the rows, F2 renames, Esc cancels', async ({ page }) => {
  await openChats(page)
  await expect(rows(page).first()).toBeFocused()
  await page.keyboard.press('/')
  await expect(search(page)).toBeFocused()
  await page.keyboard.type('offsite')
  await expect(rows(page)).toHaveCount(1)
  await page.keyboard.press('ArrowDown')
  await expect(rows(page).first()).toBeFocused()
  await page.keyboard.press('Control+f')
  await expect(search(page)).toBeFocused()
  await page.keyboard.press('ArrowDown')

  await page.keyboard.press('F2')
  const field = page.getByRole('textbox', { name: 'Chat title' })
  await expect(field).toBeFocused()
  await page.keyboard.type('Ignored')
  await page.keyboard.press('Escape') // cancels the rename, nothing else
  await expect(field).toHaveCount(0)
  await expect(rows(page).first()).toContainText('Plan the team offsite')
  await expect(rows(page).first()).toBeFocused()
  await expect(page.getByTestId('chats')).toBeVisible()

  // ArrowUp from the first row goes back to the box.
  await page.keyboard.press('ArrowUp')
  await expect(search(page)).toBeFocused()
})

test('double-click the title in the header to rename the open chat', async ({ page }) => {
  await openChats(page)
  await rows(page).filter({ hasText: 'Plan the team offsite' }).click()
  await expect(page.getByTestId('chats')).toHaveCount(0)
  const head = page.locator('.side-panel-head')
  const title = head.getByTestId('chat-title')
  await expect(title).toHaveText('Plan the team offsite')
  await expect(title).toHaveAccessibleName('Rename “Plan the team offsite”')
  if (SHOTS) await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-header-light-classic.png` })
  await title.dblclick()
  const field = head.getByRole('textbox', { name: 'Chat title' })
  await expect(field).toBeFocused()
  await field.fill('November offsite')
  await field.press('Enter')
  await expect(title).toHaveText('November offsite')
  await expect(page.locator('.toast', { hasText: 'Chat renamed.' })).toBeVisible()
  await apiSettled(page, /agent\/sessions/)

  // The list shows the new title; the chat is marked Open now.
  await page.locator('.side-panel-head').getByRole('button', { name: 'Chats' }).click()
  await expect(page.getByTestId('chats').locator('.chats-item.current')).toContainText('November offsite')
  await page.keyboard.press('Escape')

  // Undo from the toast; the header follows.
  await page.locator('.toast', { hasText: 'Chat renamed.' }).getByRole('button', { name: 'Undo' }).click()
  await expect(title).toHaveText('Plan the team offsite')

  // Keyboard: Enter on the focused title renames too; Esc cancels and keeps the panel open.
  await title.focus()
  await page.keyboard.press('Enter')
  await expect(field).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(title).toHaveText('Plan the team offsite')
  await expect(page.locator('.side-panel .chat')).toBeVisible()
})

for (const [scheme, theme] of [['dark', 'classic'], ['light', 'cards'], ['dark', 'office']] as const) {
  test(`Chats search screenshot: ${theme}, ${scheme}`, async ({ page }) => {
    test.skip(!SHOTS, 'CHATS_SHOTS not set')
    await page.emulateMedia({ colorScheme: scheme })
    await openChats(page, theme)
    await search(page).fill('north')
    await expect(rows(page)).toHaveCount(2)
    await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-search-${scheme}-${theme}.png` })
    await rows(page).first().click()
    await expect(page.locator('.side-panel-head').getByTestId('chat-title')).toBeVisible()
    await page.locator('.side-panel').screenshot({ path: `${SHOTS}/chats-header-${scheme}-${theme}.png` })
  })
}
