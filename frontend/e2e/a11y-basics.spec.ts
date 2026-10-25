// Accessibility basics from the 6 Oct 2026 review: a skip link,
// a page title per app, no grid roles that lack their rows and cells in Calendar and Tasks, 24 px
// checkbox targets in lists and note cards, and a readable "Draft" on Desk's focused row.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { dayOf, openIn, seedWeek } from './calendar-data'

/** Every element with a grid, row or cell role whose required parent or children are missing. */
async function brokenGridRoles(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = []
    const role = (e: Element | null) => e?.getAttribute('role') ?? ''
    const up = (e: Element, want: string[]) => {
      for (let p = e.parentElement; p; p = p.parentElement) if (want.includes(role(p))) return true
      return false
    }
    for (const e of document.querySelectorAll('[role="row"]')) if (!up(e, ['grid', 'table', 'treegrid', 'rowgroup'])) bad.push(`row ${e.className}`)
    for (const e of document.querySelectorAll('[role="gridcell"], [role="columnheader"], [role="rowheader"]')) if (!up(e, ['row'])) bad.push(`${role(e)} ${e.className}`)
    for (const e of document.querySelectorAll('[role="grid"]')) if (!e.querySelector('[role="row"]')) bad.push(`grid ${e.className}`)
    for (const e of document.querySelectorAll('[role="list"]')) if ([...e.children].some((c) => role(c) !== 'listitem' && c.tagName !== 'LI')) bad.push(`list ${e.className}`)
    return bad
  })
}

function luminance(rgb: string): number {
  const [r, g, b] = (rgb.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a: string, b: string) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

test('a skip link is the first Tab stop and moves the focus to the main region', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list]')).toBeVisible()
  // The first thing the Tab key reaches in the page, in document order.
  const first = await page.evaluate(() => {
    const all = document.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')
    const shown = [...all].find((e) => !e.hasAttribute('disabled') && (e.offsetParent !== null || getComputedStyle(e).position === 'fixed'))
    return shown?.textContent ?? ''
  })
  expect(first).toBe('Skip to content')
  const skip = page.getByRole('link', { name: 'Skip to content' })
  await expect(skip).not.toBeInViewport()
  await skip.focus()
  await expect(skip).toBeInViewport()
  await page.keyboard.press('Enter')
  await expect(page.locator('#main')).toBeFocused()
})

test('each app sets the page title', async ({ page }) => {
  await page.goto('/calendar')
  await expect(page).toHaveTitle('Calendar - Mailr')
  await page.goto('/tasks/today')
  await expect(page).toHaveTitle('Today - Tasks - Mailr')
  await page.goto('/notes')
  await expect(page).toHaveTitle('Notes - Mailr')
  const n = await (await page.request.post('/api/v1/notes', { data: { kind: 'checklist', title: 'Groceries', lines: 'milk' } })).json()
  await page.goto(`/notes/n/${n.note.id}`)
  await expect(page).toHaveTitle('Groceries - Notes - Mailr')
})

for (const theme of ['classic', 'desk'] as const) {
  test(`${theme}: Calendar and Tasks use no grid roles without their rows and cells`, async ({ page, seeded }) => {
    await seedWeek(page.request, seeded)
    await openIn(page, theme, `/calendar/week/${dayOf(3)}`)
    await expect(page.getByTestId('cal-event').first()).toBeVisible()
    expect(await brokenGridRoles(page)).toEqual([])
    await openIn(page, theme, `/calendar/month/${dayOf(3)}`)
    await expect(page.getByTestId('cal-month')).toBeVisible()
    expect(await brokenGridRoles(page)).toEqual([])
    await openIn(page, theme, '/tasks/today')
    await expect(page.getByTestId('tasks-sidebar')).toBeVisible()
    expect(await brokenGridRoles(page)).toEqual([])
  })

  test(`${theme}: list and note-card checkboxes are 24 px targets`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    const n = await (await page.request.post('/api/v1/notes', { data: { kind: 'checklist', title: 'Hit areas', lines: 'milk\neggs' } })).json()
    await page.goto('/notes')
    const card = page.locator('.nt-card').filter({ hasText: 'Hit areas' })
    await expect(card).toBeVisible()
    for (const box of await card.locator('input[type="checkbox"]').all()) {
      const r = (await box.boundingBox())!
      expect(Math.min(r.width, r.height)).toBeGreaterThanOrEqual(24)
    }
    await page.goto(`/notes/n/${n.note.id}`)
    const rows = page.getByTestId('note-editor').locator('.nt-row input[type="checkbox"]')
    await expect(rows.first()).toBeVisible()
    for (const box of await rows.all()) {
      const r = (await box.boundingBox())!
      expect(Math.min(r.width, r.height)).toBeGreaterThanOrEqual(24)
    }
  })
}

for (const scheme of ['light', 'dark'] as const) {
  test(`desk ${scheme}: "Draft" on the focused row reads at 4.5:1 or more`, async ({ page }) => {
    await page.request.post('/api/v1/drafts', { data: { payload: { to: [{ email: 'dana@example.org' }], subject: 'E2E draft row', html: '<p>x</p>' } } })
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'desk'))
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/mail/drafts')
    const row = page.locator('.d-row').filter({ hasText: 'E2E draft row' })
    await expect(row).toBeVisible()
    await page.keyboard.press('j')
    await expect(row).toHaveClass(/focused/)
    const [fg, bg] = await row.locator('.d-draft').evaluate((e) => {
      const row = e.closest('.d-row') as HTMLElement
      return [getComputedStyle(e).color, getComputedStyle(row).backgroundColor]
    })
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
}
