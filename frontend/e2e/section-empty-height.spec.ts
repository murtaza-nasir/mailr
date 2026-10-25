// ACT-SURFACES: web.list web.section
// An inbox section that loses its last conversation keeps its height:
// the empty state ("No … conversations.") is exactly one row tall, the header is as tall with the
// select-all box and count as without, and nothing below the section moves while the row leaves.
// The exit animation runs (reduced motion is not emulated): the next section's top and the section's
// body are sampled on every animation frame from the click until the empty state has settled.
// Every theme that draws sections, every density, light and dark, left to right and right to left,
// with and without the reading pane on the right (narrow rows).
// Covers: mail.delete, mail.archive.
// Screenshots go to E2E_SHOTS (default: <tmp>/mailr-section-empty-shots).
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { conv, expect, test } from './fixtures'
import { pick, THEMES, type Theme } from './matrix'

/** The measured geometry, printed only with SECTION_DEBUG=1. */
const debug = (...args: unknown[]) => { if (process.env.SECTION_DEBUG) console.log(...args) }
const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-section-empty-shots')
const DENSITIES = ['default', 'compact', 'comfortable'] as const
const ONLY = 'E2E Archive me'

type Density = (typeof DENSITIES)[number]
interface Variant { theme: Theme; density: Density; dark?: boolean; rtl?: boolean; pane?: 'none' | 'right'; width?: number; action?: 'Delete' | 'Archive'; long?: boolean }

/** The always-empty section; a long name gives a long empty text, which stays on one line. */
const LONG = 'Nothing from the quarterly vendor reconciliation review' // names stop at 60
const nothing = (v: Variant) => (v.long ? LONG : 'Nothing')

const section = (page: Page, title: string) => page.locator(`section.section[aria-label="${title}"]`)
const head = (page: Page, title: string) => section(page, title).locator('.section-head')

async function csrf(page: Page): Promise<Record<string, string>> {
  const t = (await (await page.request.get('/api/v1/me')).json()).csrf
  return t ? { 'X-CSRF-Token': t } : {}
}

/** One Inbox tab: "Solo" (one conversation with a one-line preview and no chips: a usual row),
 *  "Nothing" (always empty), then everything else. */
async function setUp(page: Page, v: Variant) {
  const h = await csrf(page)
  const r = await page.request.put('/api/v1/layouts', { headers: h, data: { active: 'page', layouts: [{ name: 'Page', tabs: [
    { name: 'Inbox', icon: 'inbox', sections: [
      { title: 'Solo', base: 'inbox', q: `subject:"${ONLY}"`, limit: 10 },
      { title: nothing(v), base: 'inbox', q: 'subject:"zz-no-such-subject-zz"', limit: 10 },
      { title: 'Everything else', base: 'inbox', limit: null, exclude_above: true },
    ] },
  ] }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const pane = v.pane ?? 'none'
  const s = await page.request.patch('/api/v1/me/settings', { headers: h, data: { density: v.density, reading_pane: pane, reading_pane_by_theme: { [v.theme]: pane } } })
  expect(s.ok(), await s.text()).toBeTruthy()
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), v.theme)
  await page.emulateMedia({ colorScheme: v.dark ? 'dark' : 'light', reducedMotion: 'no-preference' })
  if (v.width) await page.setViewportSize({ width: v.width, height: 900 })
  await page.goto('/mail/inbox')
  if (v.rtl) await page.evaluate(() => { document.documentElement.dir = 'rtl' })
  await expect(section(page, 'Solo').locator('[data-id]')).toHaveCount(1)
  await expect(section(page, nothing(v)).locator('.section-empty')).toBeVisible()
  await expect(section(page, 'Everything else').getByRole('row').first()).toBeVisible()
}

/** Height of a section below its header (rows, or the empty state), margins included. */
function bodyHeight(page: Page, title: string) {
  return section(page, title).evaluate((s) => {
    const h = s.querySelector('.section-head') as HTMLElement
    return s.getBoundingClientRect().bottom - h.getBoundingClientRect().bottom
  })
}

const name = (v: Variant) => [v.theme, v.density, v.dark ? 'dark' : 'light', v.rtl ? 'rtl' : 'ltr', v.pane === 'right' ? 'pane' : 'wide', v.width ?? 1440, v.action ?? 'Delete', v.long ? 'long' : ''].filter(Boolean).join('-')

async function check(page: Page, v: Variant) {
  await setUp(page, v)
  await page.waitForTimeout(400) // entry animations (Cards) settle
  const soloHead = await head(page, 'Solo').evaluate((e) => e.getBoundingClientRect().height)
  const emptyHead = await head(page, nothing(v)).evaluate((e) => e.getBoundingClientRect().height)
  const rowBody = await bodyHeight(page, 'Solo')
  const emptyBody = await bodyHeight(page, nothing(v))
  const rowH = await section(page, 'Solo').locator('[data-id]').evaluate((e) => e.getBoundingClientRect().height)
  const text = section(page, nothing(v)).locator('.section-empty')
  debug(name(v), JSON.stringify({ soloHead, emptyHead, rowBody, emptyBody, rowH }))
  await page.screenshot({ path: `${SHOTS}/${name(v)}-before.png` })

  // A header with its box and count is as tall as one without.
  expect(Math.abs(soloHead - emptyHead)).toBeLessThanOrEqual(0.5)
  // The empty state is one row tall, its text on one line and centred.
  expect(Math.abs(rowBody - emptyBody)).toBeLessThanOrEqual(0.5)
  const centred = await text.evaluate((e) => {
    const box = e.getBoundingClientRect()
    const t = e.querySelector('.section-empty-text') as HTMLElement
    const range = document.createRange()
    range.selectNodeContents(t)
    const lines = new Set([...range.getClientRects()].map((x) => Math.round(x.top))).size
    const r = t.getBoundingClientRect()
    const pad = parseFloat(getComputedStyle(e).paddingBottom) + parseFloat(getComputedStyle(e).borderBottomWidth)
    return { above: r.top - box.top, below: box.bottom - pad - r.bottom, lines }
  })
  expect(centred.lines).toBe(1)
  // A long text is cut with an ellipsis, not wrapped.
  if (v.long) expect(await text.locator('.section-empty-text').evaluate((t) => t.scrollWidth > t.clientWidth && getComputedStyle(t).textOverflow === 'ellipsis')).toBe(true)
  expect(Math.abs(centred.above - centred.below)).toBeLessThanOrEqual(1)

  // Sample the next section's top and Solo's body on every frame from before the click until the
  // empty state has been on screen for a while.
  await sample(page, nothing(v))
  const nextTop = await head(page, nothing(v)).evaluate((e) => e.getBoundingClientRect().top)
  await remove(page, v.action ?? 'Delete')
  const samples = await samples_(page)
  await page.screenshot({ path: `${SHOTS}/${name(v)}-after.png` })
  const nextDev = Math.max(...samples.map((s) => Math.abs(s.next - nextTop)))
  const bodyDev = Math.max(...samples.map((s) => Math.abs(s.body - rowBody)))
  debug(name(v), 'frames', samples.length, 'next dev', nextDev, 'body dev', bodyDev,
    JSON.stringify(samples.filter((s) => Math.abs(s.body - rowBody) > 0.5).slice(0, 6)))
  expect(samples.length).toBeGreaterThan(10)
  expect(nextDev).toBeLessThanOrEqual(0.5)
  expect(bodyDev).toBeLessThanOrEqual(0.5)
  expect(Math.abs(await bodyHeight(page, 'Solo') - rowBody)).toBeLessThanOrEqual(0.5)
  expect(Math.abs(await head(page, 'Solo').evaluate((e) => e.getBoundingClientRect().height) - soloHead)).toBeLessThanOrEqual(0.5)
}

interface Sample { next: number; body: number; t: number }

/** Record the next section's top and Solo's body on every animation frame until samples_(). */
async function sample(page: Page, next: string) {
  await page.evaluate((next) => {
    const w = window as unknown as { __samples: { next: number; body: number; t: number }[]; __stop: boolean }
    w.__samples = []
    w.__stop = false
    const solo = document.querySelector('section.section[aria-label="Solo"]') as HTMLElement
    const nextHead = document.querySelector(`section.section[aria-label="${next}"] .section-head`) as HTMLElement
    const tick = (t: number) => {
      const h = solo.querySelector('.section-head') as HTMLElement
      w.__samples.push({ next: nextHead.getBoundingClientRect().top, body: solo.getBoundingClientRect().bottom - h.getBoundingClientRect().bottom, t })
      if (!w.__stop) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }, next)
}

async function samples_(page: Page): Promise<Sample[]> {
  return page.evaluate(() => {
    const w = window as unknown as { __samples: { next: number; body: number; t: number }[]; __stop: boolean }
    w.__stop = true
    return w.__samples
  })
}

/** The row's own right-click menu, as a person would; then the empty state settles. */
async function remove(page: Page, action: 'Delete' | 'Archive') {
  const row = section(page, 'Solo').locator('[data-id]').first()
  await row.click({ button: 'right', position: { x: 120, y: 8 } })
  await page.getByRole('menu', { name: 'Conversation actions' }).getByRole('menuitem', { name: new RegExp(`^${action}`) }).click()
  await expect(section(page, 'Solo').locator('.section-empty')).toBeVisible()
  await page.waitForTimeout(600)
}

// The matrix (e2e/matrix.ts): nightly every density in every theme; otherwise the default density,
// since the three variants below already cover compact and comfortable in every theme.
for (const theme of THEMES) {
  for (const density of pick(DENSITIES, ['default'])) {
    test(`${theme} ${density}: deleting a section's last row moves nothing below it (mail.delete)`, async ({ page }) => {
      await check(page, { theme, density })
    })
  }
  test(`${theme}: dark, right to left, reading pane on the right, archive (mail.archive)`, async ({ page }) => {
    await check(page, { theme, density: 'default', dark: true, rtl: true, pane: 'right', action: 'Archive' })
  })
  test(`${theme}: compact, narrow window, dark`, async ({ page }) => {
    await check(page, { theme, density: 'compact', dark: true, width: 900 })
  })
  test(`${theme}: comfortable, phone width, a long empty text on one line`, async ({ page }) => {
    await check(page, { theme, density: 'comfortable', width: 400, long: true })
  })
}

// A card with a chip line is taller than an empty section (one usual card). Its section glides to
// the empty state's height in one direction: it never shrinks below it and never grows back.
test('cards: a taller last card glides to the empty height without a dip', async ({ page, seeded }) => {
  const id = (await conv(page.request, ONLY)).id
  const h = await csrf(page)
  const r = await page.request.post('/api/v1/conversations/actions', { headers: h, data: { ids: [id], action: 'add_labels', label_ids: [seeded.labels['E2E Label']] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const v: Variant = { theme: 'cards', density: 'default' }
  await setUp(page, v)
  await page.waitForTimeout(400)
  const rowBody = await bodyHeight(page, 'Solo')
  const emptyBody = await bodyHeight(page, 'Nothing')
  expect(rowBody - emptyBody).toBeGreaterThan(10)
  await sample(page, 'Nothing')
  const nextTop = await head(page, 'Nothing').evaluate((e) => e.getBoundingClientRect().top)
  await remove(page, 'Delete')
  const samples = await samples_(page)
  debug('cards glide', JSON.stringify(samples.map((s) => Math.round(s.body * 10) / 10)))
  for (let i = 1; i < samples.length; i++) expect(samples[i].next).toBeLessThanOrEqual(samples[i - 1].next + 0.5)
  expect(Math.min(...samples.map((s) => s.body))).toBeGreaterThanOrEqual(emptyBody - 0.5)
  expect(Math.abs(samples[samples.length - 1].next - (nextTop - (rowBody - emptyBody)))).toBeLessThanOrEqual(0.5)
})
