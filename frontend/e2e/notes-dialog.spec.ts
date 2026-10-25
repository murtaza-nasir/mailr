// ACT-SURFACES: web.note
// The note editor as a dialog. Over the notes (Classic, Focus and Cards on a desktop, every
// theme on a phone) it is a modal dialog: role="dialog" with aria-modal and the note's name, the
// focus moves in and Tab stays inside, the notes behind are inert, Escape closes, and the focus
// goes back to the card. Beside or below the notes (Office, Desk) it is a labelled region and
// the notes stay usable. Escape in a list item still leaves only the item.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { THEMES, themes } from './matrix'
const OVERLAY = new Set(['classic', 'focus', 'cards'])

const inside = (page: Page) => page.evaluate(() => !!document.activeElement?.closest('[data-testid="note-pane"]'))
const behindInert = (page: Page) => page.evaluate(() => !!document.querySelector('.nt-center')?.closest('[inert]'))

// On a phone every theme opens the same modal: nightly every theme, otherwise Classic and Cards.
for (const phone of [false, true]) {
  for (const theme of phone ? themes({ release: ['classic', 'cards'] }) : THEMES) {
    test(`${theme}${phone ? ' on a phone' : ''}: the note editor is ${!phone && !OVERLAY.has(theme) ? 'a region beside the notes' : 'a modal dialog'}`, async ({ page }) => {
      if (phone) await page.setViewportSize({ width: 390, height: 844 })
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
      await page.request.post('/api/v1/notes', { data: { kind: 'checklist', title: 'Dialog list', lines: 'milk\neggs' } })
      await page.goto('/notes')
      const card = page.locator('.nt-card').filter({ hasText: 'Dialog list' })
      await card.focus()
      await page.keyboard.press('Enter')
      const modal = phone || OVERLAY.has(theme)

      if (!modal) {
        const region = page.getByRole('region', { name: 'Dialog list' })
        await expect(region).toBeVisible()
        await expect(page.getByRole('dialog')).toHaveCount(0)
        expect(await behindInert(page)).toBe(false)
        return
      }
      const dlg = page.getByRole('dialog', { name: 'Dialog list' })
      await expect(dlg).toBeVisible()
      await expect(dlg).toHaveAttribute('aria-modal', 'true')
      await expect.poll(() => inside(page)).toBe(true)
      expect(await behindInert(page)).toBe(true)
      // The toasts stay usable (an Undo while the note is open).
      expect(await page.evaluate(() => !!document.querySelector('.toasts')?.closest('[inert]'))).toBe(false)

      // Tab and Shift+Tab stay inside.
      for (let i = 0; i < 25; i++) {
        await page.keyboard.press('Tab')
        expect(await inside(page)).toBe(true)
      }
      for (let i = 0; i < 5; i++) {
        await page.keyboard.press('Shift+Tab')
        expect(await inside(page)).toBe(true)
      }

      // Escape while editing an item leaves the item; the note stays open.
      const item = dlg.getByRole('textbox', { name: 'Item' }).first()
      await item.click()
      await expect(item).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(dlg).toBeVisible()

      // Escape again closes the note, the notes are usable again and the focus is on the card.
      await page.keyboard.press('Escape')
      await expect(dlg).toHaveCount(0)
      expect(await behindInert(page)).toBe(false)
      await expect(card).toBeFocused()
    })
  }
}
