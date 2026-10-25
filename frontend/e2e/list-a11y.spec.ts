// The mail list for keyboard and screen-reader use.
// - the list has one tab stop (the first row until the cursor moves), and j/k move DOM focus
//     with the cursor, so a screen reader announces the row.
// - each list is a grid that holds only rows (group headings are rows too), with aria-rowcount
//     and aria-rowindex; the tabs, toolbar and pager sit outside it.
// Covers: web.focus, web.list.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { THEMES } from './matrix'

const grid = (page: Page) => page.locator('[data-mail-list] [role="grid"]').first()

for (const theme of THEMES) {
  test(`${theme}: one tab stop, j/k move focus, the grid holds only rows`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/mail/inbox')
    const g = grid(page)
    await expect(g.locator('[role="row"][data-id]').first()).toBeVisible()

    // Structure: only rows directly under the grid's row containers; counts and positions.
    const bad = await g.evaluate((el) => {
      const out: string[] = []
      const rows = [...el.querySelectorAll('[role="row"]')]
      for (const r of rows) if (r.parentElement?.closest('[role="row"]')) out.push('row in row')
      for (const n of el.querySelectorAll('button, [role="tab"], .list-footer, .row-skeleton'))
        if (!n.closest('[role="row"]')) out.push(`loose ${n.className || n.tagName}`)
      const count = Number(el.getAttribute('aria-rowcount'))
      if (!(count >= rows.length)) out.push(`rowcount ${count} < ${rows.length}`)
      rows.forEach((r, i) => {
        if (Number(r.getAttribute('aria-rowindex')) !== i + 1) out.push(`rowindex ${r.getAttribute('aria-rowindex')} at ${i + 1}`)
      })
      return out
    })
    expect(bad).toEqual([])
    await expect(page.locator('[data-mail-list] .list-scroll[role="grid"]')).toHaveCount(0)

    // One tab stop: the first conversation row.
    const tabStops = g.locator('[role="row"][data-id][tabindex="0"]')
    await expect(tabStops).toHaveCount(1)
    const first = g.locator('[role="row"][data-id]').first()
    await expect(tabStops).toHaveAttribute('data-id', (await first.getAttribute('data-id'))!)

    // j moves the cursor and the focus to the row; k moves both back.
    await page.locator('body').click({ position: { x: 1, y: 1 } })
    await page.keyboard.press('j')
    const focusedId = () => page.evaluate(() => (document.activeElement as HTMLElement | null)?.closest('[role="row"][data-id]')?.getAttribute('data-id') ?? null)
    await expect.poll(focusedId).not.toBeNull()
    const a = await focusedId()
    await page.keyboard.press('j')
    await expect.poll(focusedId).not.toBe(a)
    await page.keyboard.press('k')
    await expect.poll(focusedId).toBe(a)
    await expect(tabStops).toHaveCount(1)
    await expect(tabStops).toHaveAttribute('data-id', a!)

    // Enter on the focused row opens that conversation once.
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(new RegExp(`/mail/inbox/`))
  })
}
