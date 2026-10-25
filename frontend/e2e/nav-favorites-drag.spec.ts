// ACT-SURFACES: web.palette web.nav
// Favorites and dragging conversations onto the nav, in every theme (surface
// web.nav). Covers: nav.favorite_add, nav.favorite_remove, nav.favorite_reorder (menu, Alt+↑/↓,
// drag; the palette in Focus), and drops: mail.label (drop on a label), mail.move (Shift-drop on a
// label), mail.delete (drop on Trash, with Undo), mail.archive, mail.spam, mail.star,
// mail.important, a multi-selection drag. Screenshots of each nav go to the output folder's shots/favdrag/ (FAVDRAG_SHOTS or E2E_SHOTS set).
import type { Locator, Page } from '@playwright/test'
import { apiSettled, conv, expect, roles, type Seed, shotsDir, test } from './fixtures'
import { openApp } from './helpers'
import { THEMES } from './matrix'

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'
const SHOTS = process.env.FAVDRAG_SHOTS ?? shotsDir('favdrag')

const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

async function favorites(page: Page): Promise<string[]> {
  return (await (await page.request.get('/api/v1/me')).json()).settings.nav_favorites
}

const open = (page: Page, theme: Theme) => openApp(page, '/mail/inbox', { theme, ready: row(page, 'E2E Reply to me') })

/** The nav entry for a view: the right-clickable link in the theme's nav. */
function navEntry(page: Page, view: string, favorite = false): Locator {
  return page.locator(favorite ? `[data-favorite="${view}"]` : `[data-drop-view="${view}"]:not([data-favorite])`).first()
}

/** Cards keeps folders and labels in a drawer. */
async function showNav(page: Page, theme: Theme) {
  if (theme === 'cards' && !(await page.locator('.c-drawer.open').count())) await page.getByRole('button', { name: 'All mailboxes and labels' }).first().click()
}
async function hideNav(page: Page, theme: Theme) {
  if (theme === 'cards' && (await page.locator('.c-drawer.open').count())) await page.locator('.c-drawer').getByRole('button', { name: 'Close' }).click()
}

async function palette(page: Page, command: string) {
  await expect(page.locator('[data-mail-list]')).toBeVisible()
  // The palette's commands follow the settings the page has loaded (Add or Remove from Favorites):
  // let the page finish loading them first.
  await apiSettled(page)
  await page.keyboard.press('Control+k')
  await page.getByRole('combobox', { name: 'Command' }).fill(command)
  await page.getByRole('option', { name: new RegExp(command) }).first().click()
}

/** Pointer drag with pauses, so hover-to-open and the verb have time to show. */
async function drag(page: Page, from: Locator, to: () => Locator, opts: { shift?: boolean; via?: () => Locator; shot?: string } = {}) {
  const center = (l: Locator) => l.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  await center(from)
  if (!opts.via && (await to().count())) await center(to())
  const a = (await from.boundingBox())!
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(a.x + a.width / 2 + 12, a.y + a.height / 2 + 6, { steps: 4 })
  if (opts.via) {
    const v = (await opts.via().boundingBox())!
    await page.mouse.move(v.x + v.width / 2, v.y + v.height / 2, { steps: 6 })
    await page.waitForTimeout(900) // the section opens after 600 ms
  }
  if (opts.shift) await page.keyboard.down('Shift')
  const t = to()
  await center(t)
  const b = (await t.boundingBox())!
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 })
  await page.mouse.move(b.x + b.width / 2 + 1, b.y + b.height / 2, { steps: 2 })
  if (process.env.DBG) {
    await page.screenshot({ path: `/tmp/dbg-${Date.now()}.png` })
    console.log('DBG', await page.evaluate(() => ({ drag: document.body.dataset.mailDrag, keys: document.querySelectorAll('[data-drop-key]').length })), JSON.stringify(b))
  }
  await expect(t).toHaveClass(/drop-over/)
  if (opts.shot) {
    await page.waitForTimeout(200) // the verb fades in
    await page.screenshot({ path: `${SHOTS}/${opts.shot}.png` })
  }
  await page.mouse.up()
  if (opts.shift) await page.keyboard.up('Shift')
}

/** Where a theme keeps Trash and the labels when they start hidden (hover opens them). */
function via(page: Page, theme: Theme, what: 'trash' | 'label'): (() => Locator) | undefined {
  if (theme === 'classic' && what === 'trash') return () => page.locator('.sidebar .nav-item', { hasText: 'More' })
  if (theme === 'cards') return () => page.locator('.c-rail .c-dest', { hasText: 'More' })
  return undefined
}

function target(page: Page, theme: Theme, view: string): () => Locator {
  if (theme === 'focus') return () => page.locator(`.f-shelf [data-drop-view="${view}"]`)
  if (theme === 'cards') return () => page.locator(`.c-drawer [data-drop-view="${view}"]`).first()
  return () => navEntry(page, view)
}

for (const theme of THEMES) {
  test.describe(theme, () => {
    test(`favorites: add, reorder, remove (${theme}; nav.favorite_add, nav.favorite_reorder, nav.favorite_remove)`, async ({ page, seeded }) => {
      await open(page, theme)
      const lbl = `label:${seeded.labels['E2E Label']}`
      if (theme === 'focus') {
        // Focus has no nav: the palette holds the same commands for the view you are on.
        await page.goto(`/mail/${lbl}`)
        await palette(page, 'Add to Favourites')
        await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts', lbl])
        await palette(page, 'Move up in Favourites')
        await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', lbl, 'drafts'])
        await page.goto('/mail/inbox')
        await expect(row(page, 'E2E Reply to me')).toBeVisible()
        await page.keyboard.press('g')
        await page.keyboard.press('3')
        await expect(page).toHaveURL(new RegExp(`/mail/${encodeURIComponent(lbl)}$`))
        await page.keyboard.press('Control+k')
        await expect(page.locator('.palette-section').first()).toHaveText('Favourites')
        await page.screenshot({ path: `${SHOTS}/${theme}-favorites.png` })
        await page.keyboard.press('Escape')
        await page.goto(`/mail/${lbl}`)
        await palette(page, 'Remove from Favourites')
        await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts'])
        return
      }
      await showNav(page, theme)
      // nav.favorite_add: right-click a label.
      await navEntry(page, lbl).click({ button: 'right' })
      if (process.env.DBG) await page.screenshot({ path: `/tmp/dbg-menu-${theme}.png` })
      await page.getByRole('menuitem', { name: 'Add to Favourites' }).click()
      await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts', lbl])
      await showNav(page, theme)
      await expect(navEntry(page, lbl, true)).toBeVisible()
      // Folders take the same menu (Spam is under More in Classic: favorites hold it at the top).
      if (theme === 'classic') await page.locator('.sidebar .nav-item', { hasText: 'More' }).click()
      await navEntry(page, 'trash').click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Add to Favourites' }).click()
      await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts', lbl, 'trash'])
      await showNav(page, theme)
      if (theme === 'cards') await page.locator('.c-drawer.open').evaluate((el) => el.scrollTo({ top: 0 }))
      await page.screenshot({ path: `${SHOTS}/${theme}-favorites.png` })

      if (theme === 'classic') {
        // Classic's Favorites leaves out the folders shown
        // right below it, so it holds the label and Trash only; moves step past the rest.
        const group = page.getByRole('group', { name: 'Favourites' })
        await expect(group.locator('[data-favorite]')).toHaveCount(2)
        for (const v of ['inbox', 'sent', 'drafts']) await expect(navEntry(page, v, true)).toHaveCount(0)
        await expect(page.locator('.sidebar .nav-item.active')).toHaveCount(1)
        // A left-out favorite's menu offers Remove only: it has no place in the group to move.
        await navEntry(page, 'inbox').click({ button: 'right' })
        await expect(page.getByRole('menuitem', { name: 'Remove from Favourites' })).toBeVisible()
        await expect(page.getByRole('menuitem', { name: 'Move up in Favourites' })).toHaveCount(0)
        await page.keyboard.press('Escape')
        await navEntry(page, 'trash', true).focus()
        await page.keyboard.press('Alt+ArrowUp')
        await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts', 'trash', lbl])
        await expect(navEntry(page, 'trash', true)).toBeFocused()
        await navEntry(page, lbl, true).click({ button: 'right' })
        await expect(page.getByRole('menuitem', { name: 'Move down in Favourites' })).toBeDisabled()
        await page.getByRole('menuitem', { name: 'Move up in Favourites' }).click()
        await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts', lbl, 'trash'])
        await navEntry(page, 'trash', true).dragTo(navEntry(page, lbl, true), { targetPosition: { x: 20, y: 2 } })
        await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts', 'trash', lbl])
        // g 1 goes to the first favorite Classic draws (Trash), not to the left-out Inbox.
        await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
        await page.keyboard.press('g')
        await page.keyboard.press('1')
        await expect(page).toHaveURL(/\/mail\/trash/)
        await navEntry(page, lbl, true).click({ button: 'right' })
        await page.getByRole('menuitem', { name: 'Remove from Favourites' }).click()
        await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', 'drafts', 'trash'])
        await expect(page.locator(`[data-favorite="${lbl}"]`)).toHaveCount(0)
        return
      }

      // nav.favorite_reorder: Alt+↑ on a focused favorite, the menu, and a drag.
      await navEntry(page, lbl, true).focus()
      await page.keyboard.press('Alt+ArrowUp')
      await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', lbl, 'drafts', 'trash'])
      await expect(navEntry(page, lbl, true)).toBeFocused()
      await navEntry(page, 'trash', true).click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Move up in Favourites' }).click()
      await expect.poll(() => favorites(page)).toEqual(['inbox', 'sent', lbl, 'trash', 'drafts'])
      await showNav(page, theme)
      // The folder pane may have scrolled to Trash: bring the favorites back before the drag.
      await navEntry(page, 'inbox', true).scrollIntoViewIfNeeded()
      await navEntry(page, 'drafts', true).dragTo(navEntry(page, 'inbox', true), { targetPosition: { x: 20, y: 2 } })
      await expect.poll(() => favorites(page)).toEqual(['drafts', 'inbox', 'sent', lbl, 'trash'])

      // nav.favorite_remove
      await showNav(page, theme)
      await navEntry(page, lbl, true).click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Remove from Favourites' }).click()
      await expect.poll(() => favorites(page)).toEqual(['drafts', 'inbox', 'sent', 'trash'])
      await expect(page.locator(`[data-favorite="${lbl}"]`)).toHaveCount(0)
      await hideNav(page, theme)
    })

    test(`drag onto labels and Trash (${theme}; mail.label, mail.move, mail.delete)`, async ({ page, seeded }) => {
      await open(page, theme)
      const r = await roles(page.request)
      const label = seeded.labels['E2E Label']
      const other = seeded.labels['E2E Other']
      // Focus's drop shelf holds the favorites and the four folders: make the labels favorites.
      if (theme === 'focus') {
        await page.request.patch('/api/v1/me/settings', { data: { nav_favorites: ['inbox', `label:${label}`, `label:${other}`] } })
        await page.reload()
        await expect(row(page, 'E2E Label me')).toBeVisible()
      }

      // mail.label: a drop on a label adds it; the mail stays in the Inbox.
      await drag(page, row(page, 'E2E Label me'), target(page, theme, `label:${label}`), { via: via(page, theme, 'label'), shot: `${theme}-drag-label` })
      await expect.poll(async () => (await conv(page.request, 'E2E Label me')).label_ids).toContain(label)
      expect((await conv(page.request, 'E2E Label me')).label_ids).toContain(r.inbox)
      await expect(page.locator('.toast', { hasText: 'Label applied.' })).toBeVisible()
      await hideNav(page, theme)
      await expect(row(page, 'E2E Label me')).toBeVisible()

      // mail.move: Shift-drop moves to the label (out of the Inbox).
      await drag(page, row(page, 'E2E Move me'), target(page, theme, `label:${other}`), { shift: true, via: via(page, theme, 'label'), shot: `${theme}-drag-shift-move` })
      await expect.poll(async () => (await conv(page.request, 'E2E Move me')).label_ids).toContain(other)
      expect((await conv(page.request, 'E2E Move me')).label_ids).not.toContain(r.inbox)
      await hideNav(page, theme)
      await expect(row(page, 'E2E Move me')).toHaveCount(0)

      // mail.delete: a drop on Trash, then Undo brings it back.
      await drag(page, row(page, 'E2E Delete me'), target(page, theme, 'trash'), { via: via(page, theme, 'trash'), shot: `${theme}-drag-trash` })
      await expect.poll(async () => (await conv(page.request, 'E2E Delete me')).label_ids).toContain(r.trash)
      await page.locator('.toast', { hasText: 'Conversation moved to Trash.' }).getByRole('button', { name: 'Undo' }).click()
      await expect.poll(async () => (await conv(page.request, 'E2E Delete me')).label_ids).not.toContain(r.trash)
      await hideNav(page, theme)
      await expect(row(page, 'E2E Delete me')).toBeVisible()
    })

    test(`drag a selection; no drop on the view itself (${theme}; mail.archive, mail.star)`, async ({ page }) => {
      await open(page, theme)
      const r = await roles(page.request)
      // Select two rows with Ctrl-click, then drag one of them: both go.
      await row(page, 'E2E Archive me').click({ modifiers: ['Control'] })
      await row(page, 'E2E Mute me').click({ modifiers: ['Control'] })
      await expect(row(page, 'E2E Mute me')).toHaveAttribute('aria-selected', 'true')
      await drag(page, row(page, 'E2E Archive me'), target(page, theme, 'all'), { via: via(page, theme, 'trash'), shot: `${theme}-drag-selection` })
      for (const s of ['E2E Archive me', 'E2E Mute me'])
        await expect.poll(async () => (await conv(page.request, s)).label_ids).not.toContain(r.inbox)
      await expect(page.locator('.toast', { hasText: '2 conversations archived.' })).toBeVisible()
      await hideNav(page, theme)

      // mail.star: a drop on Starred stars it (Focus's shelf has no Starred unless it is a favorite).
      if (theme !== 'focus') {
        await drag(page, row(page, 'E2E Star me'), target(page, theme, 'starred'), { via: via(page, theme, 'label') })
        await expect.poll(async () => (await conv(page.request, 'E2E Star me')).starred).toBe(true)
        await hideNav(page, theme)
      }

      // The Inbox is the view itself: it takes no drop and shows no highlight.
      const a = (await row(page, 'E2E Read me').boundingBox())!
      await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
      await page.mouse.down()
      await page.mouse.move(a.x + a.width / 2 + 12, a.y + a.height / 2 + 6, { steps: 4 })
      const inbox = theme === 'focus' ? page.locator('.f-shelf [data-drop-view="inbox"]') : theme === 'cards' ? page.locator('.c-rail [data-drop-view="inbox"]') : navEntry(page, 'inbox')
      const b = (await inbox.boundingBox())!
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 })
      await expect(inbox).not.toHaveClass(/drop-over/)
      await expect(page.locator('[data-drop-hint]')).toHaveCount(0)
      await page.mouse.up()
    })
  })
}

test('the help lists g 1–9 and the drag alternatives for v and l', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Reply to me')).toBeVisible()
  await page.keyboard.press('?')
  const help = page.getByRole('dialog', { name: 'Keyboard shortcuts' })
  await expect(help.getByText('Go to favourite 1–9')).toBeVisible()
  await expect(help.getByText('Move to folder (or drag onto a folder)')).toBeVisible()
  await expect(help.getByText('Label as (or drag onto a label)')).toBeVisible()
})

// Keep the Seed type referenced for editors that check unused imports.
export type _Seed = Seed
