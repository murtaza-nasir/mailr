// ACT-SURFACES: web.list web.calendar web.tasks web.notes
// One search box with a scope chip: "Search" with All · Mail ·
// Calendar · Tasks · Notes. It starts on the app you are in and Esc returns it to All. The per-app
// boxes are gone; "/" in an app focuses this box on that app (event.search, task.search,
// note.search). All shows the groups in the order of the app searched from; when only another group
// has hits, the mail part is one "No mail matches" line and the mail chips step aside. Mail with no
// hits says when other apps have some, with a button to All.
// Screenshots in five themes, desktop and phone, light and dark.
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'
import { schemes, THEMES } from './matrix'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-search-scope-shots')

const box = (page: Page) => page.getByTestId('search-input')
const chip = (page: Page) => page.getByTestId('search-scope')

async function pick(page: Page, scope: string) {
  await chip(page).click()
  await page.getByRole('menu', { name: 'Search in' }).getByRole('menuitemcheckbox', { name: scope }).or(
    page.getByRole('menu', { name: 'Search in' }).getByRole('menuitem', { name: scope })).first().click()
}

test('the chip starts on the app, Esc returns it to All; the app boxes are gone (event.search, task.search, note.search)', async ({ page }) => {
  for (const [url, scope] of [['/mail/inbox', 'Mail'], ['/calendar', 'Calendar'], ['/tasks/all', 'Tasks'], ['/notes', 'Notes']] as const) {
    await page.goto(url)
    await expect(chip(page)).toHaveText(scope)
    await expect(box(page)).toHaveAttribute('placeholder', 'Search')
  }
  await expect(page.getByRole('searchbox', { name: 'Search notes' })).toHaveCount(0)
  await page.goto('/calendar')
  await expect(page.getByTestId('calendar-app')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Search the calendar' })).toHaveCount(0)
  await box(page).click()
  await box(page).press('Escape')
  await expect(chip(page)).toHaveText('All')
  // The menu offers every scope.
  await chip(page).click()
  const menu = page.getByRole('menu', { name: 'Search in' })
  for (const s of ['All', 'Mail', 'Calendar', 'Tasks', 'Notes']) await expect(menu.getByText(s, { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
})

test('All: only a note matches, so the mail part is one line and the mail chips step aside; Mail: mail only', async ({ page }) => {
  const r = await page.request.post('/api/v1/notes', { data: { kind: 'text', title: 'E2E Groceries', body: 'zucchini and basil' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await page.goto('/mail/inbox')
  await expect(chip(page)).toHaveText('Mail')
  await pick(page, 'All')
  await box(page).fill('zucchini')
  await box(page).press('Enter')
  await expect(page).toHaveURL(/\/mail\/search\?q=zucchini/)
  await expect(page.getByTestId('search-notes')).toContainText('E2E Groceries')
  await expect(page.getByTestId('no-mail-matches')).toHaveText('No mail matches')
  await expect(page.getByText('No messages matched your search')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Refine search' })).toHaveCount(0)
  await expect(chip(page)).toHaveText('All')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(150)
    await page.screenshot({ path: path.join(SHOTS, `all-notes-only-${scheme}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
  // The chip moves the search: Mail shows mail only, with its full empty state.
  await pick(page, 'Mail')
  await expect(page.getByText('No messages matched your search')).toBeVisible()
  await expect(page.getByTestId('search-notes')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Refine search' })).toBeVisible()
  // ... and says that other apps match, with the way to their matches.
  await expect(page.getByText('Your notes, calendar or tasks have matches.')).toBeVisible()
  await page.getByTestId('search-all-apps').click()
  await expect(chip(page)).toHaveText('All')
  await expect(page.getByTestId('search-notes')).toContainText('E2E Groceries')
})

test('All from Tasks puts Tasks first; from Notes, Notes first; Notes searches as you type', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const t = await page.request.post('/api/v1/tasks', { data: { summary: 'E2E Buy zucchini seeds' } })
  expect(t.ok(), await t.text()).toBeTruthy()
  const n = await page.request.post('/api/v1/notes', { data: { kind: 'text', title: 'E2E Garden', body: 'zucchini bed' } })
  expect(n.ok(), await n.text()).toBeTruthy()
  const first = page.locator('.sg .sg-col').first()

  await page.goto('/tasks/all')
  await pick(page, 'All')
  await box(page).fill('zucchini')
  await box(page).press('Enter')
  await expect(page).toHaveURL(/\/mail\/search\?q=zucchini/)
  await expect(first).toContainText('Tasks')

  await page.goto('/notes')
  await expect(chip(page)).toHaveText('Notes')
  await box(page).fill('zucchini')
  // Notes searches as you type, in Notes.
  await expect(page).toHaveURL(/\/notes\/search\?q=zucchini/)
  await expect(page.getByTestId('note-card').filter({ hasText: 'E2E Garden' }).or(page.getByText('E2E Garden'))).toBeVisible()
  await pick(page, 'All')
  await expect(page).toHaveURL(/\/mail\/search\?q=zucchini/)
  await expect(first).toContainText('Notes')
})

for (const theme of THEMES) {
  test(`the search box and its chip in ${theme}, desktop and phone, light and dark`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    for (const [w, h, where] of [[1440, 900, 'desktop'], [390, 844, 'phone']] as const) {
      await page.setViewportSize({ width: w, height: h })
      await page.goto('/calendar')
      await expect(page.getByTestId('cal-header')).toBeVisible()
      await expect(box(page)).toBeVisible()
      // On a phone the chip shows while you search.
      if (where === 'phone') {
        await expect(chip(page)).toBeHidden()
        await box(page).click()
      }
      await expect(chip(page)).toHaveText('Calendar')
      await expect(chip(page)).toBeInViewport()
      for (const scheme of schemes(theme)) {
        await page.emulateMedia({ colorScheme: scheme })
        await page.waitForTimeout(150)
        await page.screenshot({ path: path.join(SHOTS, `chip-${theme}-${where}-${scheme}.png`) })
      }
    }
  })
}
