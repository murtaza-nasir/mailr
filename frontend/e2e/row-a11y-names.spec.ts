// What a screen reader hears for a mail row: rows did not say
// Unread, ran the message count into the name ("Jordan Lee2") and began with the names of three
// buttons; the Select button held a checkbox (axe nested-interactive, about 30 a page). Now a row's
// name is "Unread, <sender>, <subject>, <n> messages, <date>", and its select box is one control,
// role checkbox.
// Covers: web.list, web.a11y.
import { expect, test } from './fixtures'
import { openApp } from './helpers'
import { THEMES } from './matrix'

for (const theme of THEMES) {
  test(`${theme}: rows say Unread and their count apart, and hold no control inside a control`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const rows = page.locator('[data-mail-list] [role="row"]')
    await openApp(page, '/mail/inbox', { theme, ready: rows.first() })
    const unread = page.locator('[data-mail-list] [role="row"].unread, [data-mail-list] [role="row"].c-card.unread').first()
    await expect(unread).toHaveAttribute('aria-label', /^Unread, /)
    for (const name of await rows.evaluateAll((els) => els.map((e) => e.getAttribute('aria-label') ?? ''))) {
      expect(name).not.toMatch(/^(Select|Star)/)
      expect(name).not.toMatch(/[A-Za-z]\d+,/) // no "Lee2,"
    }
    // No input, button or link inside a button anywhere in the list.
    expect(await page.locator('[data-mail-list] button :is(input, button, a[href])').count()).toBe(0)
    if (theme !== 'desk') {
      // Some themes show the box on hover only.
      const row = page.locator('[data-mail-list] [role="row"][data-id]').first()
      await row.hover()
      const box = row.locator('[role="checkbox"][aria-label="Select"]')
      await expect(box).toHaveAttribute('aria-checked', 'false')
      await box.click()
      await expect(box).toHaveAttribute('aria-checked', 'true')
    }
  })
}
