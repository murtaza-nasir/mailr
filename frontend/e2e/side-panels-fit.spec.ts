// ACT-SURFACES: web.panel
// The side panels beside the page (Notes, Tasks, Calendar) fit their width: the Notes panel's add line once ran past the panel's edge and a long checklist item
// pushed its section ("· Important") to the edge, where it was cut ("· Importan").
// - Nothing in a panel reaches past the panel's edges, in any theme, light or dark, left to right
//   or right to left, at the narrowest and widest panel the themes give (--panel-w 300 to 420 px).
// - No text is cut, except the list picker and the add line's placeholder, which end in an ellipsis.
// - A checklist item's section follows its text on the last line, and the checkbox stays level
//   with the first line.
// With PANEL_SHOTS=1 every combination is saved under the Playwright output folder (shots/side-panels).
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, expect, shotsDir, test } from './fixtures'
import { looks, widths } from './matrix'

const WIDTHS = [300, 330, 360, 420]
const SHOTS = process.env.PANEL_SHOTS ? shotsDir('side-panels') : null
const LONG_ITEM = 'check harddrives and put some stuff in bank'
const LONG_TITLE = 'Reddit TODO and a few more words for a list name that cannot fit on one line'

async function post(request: APIRequestContext, url: string, data: Record<string, unknown>) {
  const r = await request.post(url, { data })
  expect(r.ok(), await r.text()).toBeTruthy()
  return r.json()
}

/** A realistic list: sections after short and long items, and a long list name in the picker. */
async function seedNotes(request: APIRequestContext) {
  await post(request, '/api/v1/notes', {
    kind: 'checklist', title: LONG_TITLE, pinned: true,
    lines: `Important:\n${LONG_ITEM}\nImmediate:\npay rent\nCostco:\n3 paper towels\nan_item_with_no_place_to_break_at_all_in_its_very_long_text`,
  })
}

async function open(page: Page, panel: 'notes' | 'tasks' | 'calendar', theme: string, opts: { dark?: boolean; rtl?: boolean; width?: number } = {}) {
  await page.addInitScript(([t, p, rtl, w]) => {
    localStorage.setItem('mailr.uiTheme', t as string)
    localStorage.setItem('mailr.panel', p as string)
    if (rtl) document.addEventListener('DOMContentLoaded', () => { document.documentElement.dir = 'rtl' })
    if (w) document.addEventListener('DOMContentLoaded', () => { document.documentElement.style.setProperty('--panel-w', `${w}px`) })
  }, [theme, panel, !!opts.rtl, opts.width ?? 0] as const)
  await page.emulateMedia({ colorScheme: opts.dark ? 'dark' : 'light', reducedMotion: 'reduce' })
  await page.goto('/mail/inbox')
  if (opts.rtl) await page.evaluate(() => { document.documentElement.dir = 'rtl' })
  if (opts.width) await page.evaluate((w) => document.documentElement.style.setProperty('--panel-w', `${w}px`), opts.width)
  await expect(page.locator('.side-panel')).toBeVisible()
}

/** What sticks out of the side panel or is cut inside it. */
function problems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = []
    const panel = document.querySelector('.side-panel')!
    const box = panel.getBoundingClientRect()
    const shown = (el: Element) => {
      const r = el.getBoundingClientRect()
      return r.width > 0.5 && r.height > 0.5 && getComputedStyle(el).visibility !== 'hidden'
    }
    const name = (el: Element) => `${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]} "${(el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 30)}"`
    // Elements an ellipsis may shorten on purpose.
    const ellipsis = (el: Element) => getComputedStyle(el).textOverflow === 'ellipsis' || el.matches('select, input')
    for (const el of panel.querySelectorAll('*')) {
      if (!shown(el) || el.closest('[popover], [role="menu"], [role="dialog"], .tooltip')) continue
      const r = el.getBoundingClientRect()
      if (r.right > box.right + 1 || r.left < box.left - 1) out.push(`${name(el)} runs past the panel (${Math.round(r.left)}–${Math.round(r.right)} vs ${Math.round(box.left)}–${Math.round(box.right)})`)
      if (!ellipsis(el) && getComputedStyle(el).overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1) out.push(`${name(el)} is cut (${el.scrollWidth} > ${el.clientWidth})`)
    }
    return out
  })
}

/** The notes panel's checklist rows: the section ends the text's last line; the checkbox sits on the first. */
function rows(page: Page) {
  return page.evaluate(() => [...document.querySelectorAll('[data-testid="notes-panel"] .nt-panel-list li.nt-card-item')].map((li) => {
    // The line boxes of a run of text (a flex item reports one box for itself, so ask a Range).
    const lines = (node: Node) => {
      const r = document.createRange()
      r.selectNodeContents(node)
      return [...r.getClientRects()].filter((b) => b.width > 1)
    }
    const text = li.querySelector('.nt-item-text')!
    const box = li.querySelector('input[type="checkbox"]')!.getBoundingClientRect()
    const own = lines(text.firstChild!)
    const sec = li.querySelector('.nt-item-section')
    const s = sec ? lines(sec.lastChild!) : []
    return {
      text: text.textContent ?? '',
      checkboxLevel: Math.abs((box.top + box.bottom) / 2 - (own[0].top + own[0].bottom) / 2) <= 4,
      // The section starts on the line where the text ends (or the next line, when that one is full).
      sectionFollows: !s.length || s[0].top >= own[own.length - 1].top - 1,
      // A short section name stays on one line.
      sectionWhole: s.length <= 1,
    }
  }))
}

/** A new panel width on the open page, applied as open() applies it, then two frames for layout. */
async function setWidth(page: Page, width: number) {
  await page.evaluate((w) => document.documentElement.style.setProperty('--panel-w', `${w}px`), width)
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
}

// One test per theme, scheme and direction (each checks its widths), so they run side by side on the
// workers (fullyParallel) instead of five long tests finishing last. The matrix (e2e/matrix.ts):
// nightly every look and width; otherwise light left to right plus one more look per theme, at the
// narrowest and widest width.
for (const { theme, dark, rtl } of looks({ dark: true, rtl: true })) {
  const where = `${theme} ${dark ? 'dark' : 'light'} ${rtl ? 'rtl' : 'ltr'}`
  test(`${where}: the notes, tasks and calendar panels fit their width`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    await seedNotes(page.request)
    const d = new Date()
    const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    await post(page.request, '/api/v1/tasks', { summary: 'E2E Renew the passport, book the photo appointment and collect the old one from the drawer', due: today })
    const start = new Date(Date.now() + 3_600_000)
    await post(page.request, '/api/v1/calendar/events', {
      summary: 'E2E Quarterly planning with finance and the operations team about next year', location: 'Conference room on the fourth floor, east wing',
      start: start.toISOString(), end: new Date(start.getTime() + 3_600_000).toISOString(),
    })
    await page.setViewportSize({ width: 1518, height: 800 })
    // Every width on one load of the page: open() sets the width on the loaded page too.
    await open(page, 'notes', theme, { dark, rtl, width: WIDTHS[0] })
    const panel = page.getByTestId('notes-panel')
    for (const width of widths(WIDTHS)) {
      await setWidth(page, width)
      await expect(panel).toContainText(LONG_ITEM)
      expect(await problems(page), `notes ${where} ${width}px`).toEqual([])
      for (const r of await rows(page)) expect(r, `notes row ${where} ${width}px`).toEqual({ text: r.text, checkboxLevel: true, sectionFollows: true, sectionWhole: true })
      if (SHOTS && (width === 330 || width === 420)) await page.locator('.side-panel').screenshot({ path: path.join(SHOTS, `notes-${theme}-${dark ? 'dark' : 'light'}-${rtl ? 'rtl' : 'ltr'}-${width}.png`) })
    }
    for (const p of ['tasks', 'calendar'] as const) {
      await open(page, p, theme, { dark, rtl, width: 300 })
      await expect(page.locator('.side-panel-body')).toContainText(p === 'tasks' ? 'E2E Renew the passport' : 'E2E Quarterly planning')
      expect(await problems(page), `${p} ${where}`).toEqual([])
      if (SHOTS) await page.locator('.side-panel').screenshot({ path: path.join(SHOTS, `${p}-${theme}-${dark ? 'dark' : 'light'}-${rtl ? 'rtl' : 'ltr'}-300.png`) })
    }
  })
}

// Below 900 px the panel never opens on load (stores/ui.ts); there it spans the screen.
test('the notes panel fits where it floats over the page', async ({ page }) => {
  await seedNotes(page.request)
  for (const size of [{ width: 1280, height: 800 }, { width: 1024, height: 768 }]) {
    await page.setViewportSize(size)
    await open(page, 'notes', 'classic')
    await expect(page.getByTestId('notes-panel')).toContainText(LONG_ITEM)
    expect(await problems(page), `at ${size.width}`).toEqual([])
  }
})
