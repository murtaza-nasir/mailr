// ACT-SURFACES: web.panel web.tasks
// A task row keeps its size and layout under the pointer and under keyboard focus (seen
// in the Tasks side panel, dark): the hover actions (reschedule, priority, more) took room
// in the first line, so the due chip moved, the title was cut shorter and the row grew about 9 px,
// moving every row below it. The indicator icons (bell, notes) were also smaller than the actions.
// - Hovering a row, or focusing its checkbox, leaves the row's height and the title, due chip and
//   second line where they were (within 0.5 px), in every theme, light and dark, left to right and
//   right to left, in the side panel at 300 and 420 px and in the full Tasks app.
// - The actions show on hover and on focus, inside the row, on the title's line. In the app's wide
//   rows they take a width kept free for them and cover nothing; in the panel's narrow rows they lie
//   over the end of the line (the chip and indicators step aside).
// - The actions' icons and the indicator icons have one size; the checkbox is level with the
//   title's first line.
// With HOVER_SHOTS=1 the rows at rest and hovered are saved under the Playwright output folder
// (shots/task-row-hover).
import path from 'node:path'
import type { APIRequestContext, Locator, Page } from '@playwright/test'
import { connectCalendar, conv, expect, shotsDir, test } from './fixtures'
import { mkTask } from './helpers'
import { looks } from './matrix'

const SHOTS = process.env.HOVER_SHOTS ? shotsDir('task-row-hover') : null
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const day = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return iso(d) }

const mk = (request: APIRequestContext, body: Record<string, unknown>) => mkTask(request, body)

/** Typical rows (an overdue task with a reminder and notes, a timed one today, one days ahead), a
 *  task from an email and one with subtasks (their buttons move into the hover actions). */
async function seed(request: APIRequestContext) {
  await mk(request, { summary: 'E2E Record the lecture on decision models', due: day(-2), reminders: [15], notes: 'Slides from Module 8\nthen upload' })
  await mk(request, { summary: 'E2E Water the plants', due: new Date(new Date().setHours(23, 0, 0, 0)).toISOString(), notes: 'Balcony first' })
  await mk(request, { summary: 'E2E Renew the passport, book the photo appointment and collect the old one', due: day(3), reminders: [60] })
  const source = await conv(request, 'E2E Reply to me')
  await mk(request, { summary: 'E2E Reply to the committee', due: day(2), conversation_id: source.id })
  const parent = await mk(request, { summary: 'E2E Plan the offsite', due: day(1), notes: 'Budget first' })
  for (const s of ['E2E Pick dates', 'E2E Shortlist venues']) await mk(request, { summary: s, parent: parent.uid })
}

async function open(page: Page, where: 'panel' | 'app', theme: string, opts: { dark: boolean; rtl: boolean; width?: number }) {
  await page.addInitScript(([t, w]) => {
    localStorage.setItem('mailr.uiTheme', t as string)
    // (the app without the panel: Focus floats it over the page behind a scrim)
    if (w === 'panel') localStorage.setItem('mailr.panel', 'tasks')
    else localStorage.removeItem('mailr.panel')
  }, [theme, where] as const)
  await page.emulateMedia({ colorScheme: opts.dark ? 'dark' : 'light', reducedMotion: 'reduce' })
  await page.goto(where === 'panel' ? '/mail/inbox' : '/tasks/all')
  await page.evaluate(([rtl, w]) => {
    document.documentElement.dir = rtl ? 'rtl' : 'ltr'
    if (w) document.documentElement.style.setProperty('--panel-w', `${w}px`)
  }, [opts.rtl, opts.width ?? 0] as const)
  const scope = where === 'panel' ? page.locator('.side-panel') : page.getByTestId('tasks-app')
  await expect(scope.getByTestId('task-row').filter({ hasText: 'E2E Record the lecture' })).toBeVisible()
  return scope
}

interface Box { x: number; y: number; w: number; h: number }
interface Layout { row: Box; title: Box; chip: Box | null; meta: Box | null; check: Box; actions: Box | null; actionIcons: number[]; indicatorIcons: number[] }

function layout(row: Locator): Promise<Layout> {
  return row.evaluate((el) => {
    const box = (e: Element | null): Box | null => {
      if (!e) return null
      const r = e.getBoundingClientRect()
      return r.width > 0 && r.height > 0 ? { x: r.x, y: r.y, w: r.width, h: r.height } : null
    }
    // The icons in view (an indicator button the actions repeat is hidden while they show).
    const sizes = (sel: string) => [...el.querySelectorAll(sel)].filter((s) => getComputedStyle(s).visibility !== 'hidden')
      .map((s) => s.getBoundingClientRect()).filter((r) => r.width > 0).map((r) => r.width)
    return {
      row: box(el)!,
      title: box(el.querySelector('.tk-title'))!,
      chip: box(el.querySelector('.tk-due')),
      meta: box(el.querySelector('.tk-meta')),
      check: box(el.querySelector('.tk-check-ring'))!,
      actions: box(el.querySelector('.tk-hover')),
      actionIcons: sizes('.tk-hover svg'),
      // (the subtask count's chevron is part of its text, not an icon of its own)
      indicatorIcons: sizes('.tk-icons > svg, .tk-icons .tk-icon-btn svg'),
    }
  })
}

/** The pointer over the row's edge, half way down: a fixed point (before the fix, hovering the title
 *  moved the chip under the pointer and the row flickered between its two layouts). */
async function hover(page: Page, row: Locator) {
  const b = (await row.boundingBox())!
  await page.mouse.move(b.x + 3, b.y + b.height / 2)
  await page.waitForTimeout(50)
}

const near = (a: Box | null, b: Box | null) => (!a && !b) || (!!a && !!b && Math.abs(a.x - b.x) <= 0.5 && Math.abs(a.y - b.y) <= 0.5 && Math.abs(a.w - b.w) <= 0.5 && Math.abs(a.h - b.h) <= 0.5)

/** Rest, hover and focus of every row in scope: nothing moves; the actions show inside the row. */
async function check(page: Page, scope: Locator, where: string, table: boolean, wide: boolean) {
  const rows = scope.getByTestId('task-row')
  const n = await rows.count()
  expect(n, where).toBeGreaterThan(0)
  for (let i = 0; i < n; i++) {
    const row = rows.nth(i)
    await page.mouse.move(1, 1)
    const rest = await layout(row)
    const name = `${where} row ${i}`
    expect(rest.actions, `${name}: actions hidden at rest`).toBeNull()
    // The checkbox sits level with the title's first line.
    expect(Math.abs(rest.check.y + rest.check.h / 2 - (rest.title.y + rest.title.h / 2)), `${name}: checkbox level with the title`).toBeLessThanOrEqual(1.5)
    for (const how of ['hover', 'focus'] as const) {
      if (how === 'hover') await hover(page, row)
      else {
        await page.mouse.move(1, 1)
        await row.locator('.tk-check-ring').focus()
      }
      const now = await layout(row)
      const what = `${name} on ${how}`
      expect(Math.abs(now.row.h - rest.row.h), `${what}: row height ${rest.row.h} -> ${now.row.h}`).toBeLessThanOrEqual(0.5)
      expect(near(now.row, rest.row), `${what}: row box`).toBe(true)
      expect(near(now.title, rest.title), `${what}: title ${JSON.stringify(rest.title)} -> ${JSON.stringify(now.title)}`).toBe(true)
      expect(near(now.chip, rest.chip), `${what}: due chip ${JSON.stringify(rest.chip)} -> ${JSON.stringify(now.chip)}`).toBe(true)
      expect(near(now.meta, rest.meta), `${what}: second line`).toBe(true)
      if (table) continue
      expect(now.actions, `${what}: actions shown`).not.toBeNull()
      const a = now.actions!
      expect(a.x >= now.row.x - 0.5 && a.x + a.w <= now.row.x + now.row.w + 0.5, `${what}: actions inside the row`).toBe(true)
      // A wide row (the app) keeps the actions' width free: they cover neither the chip nor the
      // title, and the chip stays clickable. A narrow row (the panel) lays them over the line's end.
      if (wide) {
        const c = now.chip
        if (c) expect(a.x >= c.x + c.w - 0.5 || a.x + a.w <= c.x + 0.5, `${what}: actions beside the due chip`).toBe(true)
        expect(a.x >= now.title.x + now.title.w - 0.5 || a.x + a.w <= now.title.x + 0.5, `${what}: actions beside the title`).toBe(true)
        expect(await row.locator('.tk-trail').evaluate((e) => getComputedStyle(e).visibility), `${what}: chip in view`).toBe('visible')
      }
      // On the title's line.
      expect(Math.abs(a.y + a.h / 2 - (now.title.y + now.title.h / 2)), `${what}: actions on the title's line`).toBeLessThanOrEqual(1.5)
      // One icon size for the actions and the indicators.
      const icons = [...now.actionIcons, ...rest.indicatorIcons]
      expect(now.actionIcons.length, what).toBeGreaterThanOrEqual(3)
      expect(Math.max(...icons) - Math.min(...icons), `${what}: icon sizes ${icons.join(', ')}`).toBeLessThanOrEqual(0.5)
    }
    await row.locator('.tk-check-ring').blur()
  }
  await page.mouse.move(1, 1)
}

async function shot(page: Page, scope: Locator, file: string) {
  if (!SHOTS) return
  await page.mouse.move(1, 1)
  const row = scope.getByTestId('task-row').filter({ hasText: 'E2E Record the lecture' })
  await scope.screenshot({ path: path.join(SHOTS, `${file}-rest.png`) })
  await hover(page, row)
  await scope.screenshot({ path: path.join(SHOTS, `${file}-hover.png`) })
  await page.mouse.move(1, 1)
}

// One test per theme, scheme and direction, so they run side by side on the workers (fullyParallel)
// instead of five long tests finishing last. The matrix (e2e/matrix.ts): nightly all twenty;
// otherwise light left to right plus one more look per theme.
for (const { theme, dark, rtl } of looks({ dark: true, rtl: true })) {
  const mode = `${theme} ${dark ? 'dark' : 'light'} ${rtl ? 'rtl' : 'ltr'}`
  test(`${mode}: hovering or focusing a task row moves nothing, in the panel and the Tasks app`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    await seed(page.request)
    await page.setViewportSize({ width: 1518, height: 900 })
    // Both panel widths on one load of the page: open() sets the width on the loaded page.
    const scope = await open(page, 'panel', theme, { dark, rtl, width: 300 })
    await shot(page, scope, `panel-${theme}-${dark ? 'dark' : 'light'}-${rtl ? 'rtl' : 'ltr'}`)
    await check(page, scope, `panel ${mode} 300px`, false, false)
    await page.evaluate(() => document.documentElement.style.setProperty('--panel-w', '420px'))
    await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
    await check(page, scope, `panel ${mode} 420px`, false, false)
    const app = await open(page, 'app', theme, { dark, rtl })
    const list = app.locator('.tk-main-col')
    if (!rtl) await shot(page, list, `app-${theme}-${dark ? 'dark' : 'light'}`)
    await check(page, list, `app ${mode}`, theme === 'desk', true)
  })
}
