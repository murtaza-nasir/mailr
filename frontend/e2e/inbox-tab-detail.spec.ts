// ACT-SURFACES: web.settings
// layout.tab_detail: "Show more on tabs when there is room" in the layout editor.
// When it is on and the tab strip has spare width, a tab shows all its indicators ("3 new ·
// Due Dec 3 · 12" in the pill themes, the small numbers and the dot in Desk); each tab falls
// back to its usual form when space runs out, so the strip never scrolls because of it. A phone
// keeps its one compact indicator. Screenshots of Classic and Desk, wide, light and dark.
import os from 'node:os'
import path from 'node:path'
import { expect, test, type Page } from './fixtures'
import { looks, THEMES, widths } from './matrix'
const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-tab-detail-shots')

const sec = (q = '') => [{ title: 'All', base: 'inbox', q }]
const TABS = [
  { name: 'Inbox', icon: 'inbox', indicators: ['unread', 'all', 'due'], sections: sec() },
  { name: 'Financial', icon: 'briefcase', indicators: ['unread', 'due', 'all'], claims: true, sections: sec('subject:"E2E Due"') },
  { name: 'Later', icon: 'bookmark', indicators: ['unread', 'all'], sections: sec('subject:"E2E Reply to me"') },
]

async function putLayout(page: Page, tabDetail: boolean, tabs: unknown[] = TABS) {
  const r = await page.request.put('/api/v1/layouts', { data: { active: 'counted', layouts: [{ name: 'Counted', tab_detail: tabDetail, tabs }] } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

const strip = (page: Page) => page.getByRole('tablist', { name: 'Counted tabs' })
const tab = (page: Page, name: string) => strip(page).getByRole('tab', { name: new RegExp(`^${name}`) })

/** The strip holds its tabs without scrolling, and no indicator wraps or covers the tab's name. */
async function noOverflow(page: Page) {
  const s = strip(page)
  const { scroll, client } = await s.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }))
  expect(scroll).toBeLessThanOrEqual(client + 1)
  for (const t of await s.getByRole('tab').all()) {
    const nb = (await t.locator('.cat-tab-name').boundingBox())!
    const tb = (await t.boundingBox())!
    for (const ind of await t.locator('[data-indicator]').all()) {
      const b = (await ind.boundingBox())!
      expect(b.x).toBeGreaterThanOrEqual(nb.x + nb.width - 0.5)
      expect(b.x + b.width).toBeLessThanOrEqual(tb.x + tb.width + 0.5)
      expect(b.height).toBeLessThan(24)
    }
  }
}

/**
 * The choice is the fullest the strip allows: the tabs, at the widths their hidden copies give,
 * fit the strip, and every tab still in its usual form would not fit in its fuller form. Returns
 * how many tabs show more.
 */
async function fitsBest(page: Page): Promise<number> {
  const r = await page.evaluate(() => {
    const s = document.querySelector<HTMLElement>('[role="tablist"][aria-label="Counted tabs"]')!
    const ghost = s.parentElement!.querySelector<HTMLElement>(':scope > .cat-tabs-measure')
    const tabs = Array.from(s.querySelectorAll<HTMLElement>(':scope > [role="tab"]'))
    const on = tabs.map((t) => t.hasAttribute('data-detail'))
    if (!ghost) return { on: on.filter(Boolean).length, ok: !on.some(Boolean), why: 'no measuring copy' }
    const cs = getComputedStyle(s)
    const inner = s.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
    const gap = parseFloat(cs.columnGap) || 0
    const w = Array.from(ghost.children).map((p) => Array.from(p.children).map((c) => c.getBoundingClientRect().width))
    const used = w.reduce((sum, x, i) => sum + (on[i] ? x[1] : x[0]), 0) + gap * (w.length - 1)
    const usual = w.reduce((sum, x) => sum + x[0], 0) + gap * (w.length - 1)
    if (usual > inner + 0.5) return { on: on.filter(Boolean).length, ok: !on.some(Boolean), why: 'usual forms overflow' }
    const missed = w.findIndex((x, i) => !on[i] && x.length > 1 && used + x[1] - x[0] <= inner + 0.5)
    return { on: on.filter(Boolean).length, ok: used <= inner + 0.5 && missed < 0, why: `used ${used} of ${inner}, missed ${missed}` }
  })
  expect(r.ok, r.why).toBe(true)
  return r.on
}

test('the layout editor turns "Show more on tabs" on and off, and the tabs follow', async ({ page }) => {
  // Desk: one compact indicator usually; two pills with the setting on.
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'desk'))
  await putLayout(page, false)
  await page.goto('/mail/inbox')
  await expect(tab(page, 'Inbox').locator('[data-indicator]')).toHaveCount(1)

  await page.goto('/settings/inbox')
  await page.getByRole('button', { name: 'Edit Counted' }).click()
  const toggle = page.getByRole('checkbox', { name: 'Show more on tabs when there is room' })
  await expect(toggle).not.toBeChecked()
  await toggle.check()
  await page.getByRole('button', { name: 'Save layout' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const lays = await (await page.request.get('/api/v1/layouts')).json()
  expect(lays.layouts.find((l: { id: string }) => l.id === 'counted').tab_detail).toBe(true)

  await page.goto('/mail/inbox')
  await expect(tab(page, 'Inbox')).toHaveAttribute('data-detail', 'true')
  await expect(tab(page, 'Inbox').locator('[data-indicator]')).toHaveText([/^\d+ new$/, /^\d+$/])
  await fitsBest(page)
  await noOverflow(page)

  // Off again: back to one.
  await page.goto('/settings/inbox')
  await page.getByRole('button', { name: 'Edit Counted' }).click()
  await expect(toggle).toBeChecked()
  await toggle.uncheck()
  await page.getByRole('button', { name: 'Save layout' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.goto('/mail/inbox')
  await expect(tab(page, 'Inbox').locator('[data-indicator]')).toHaveCount(1)
  await expect(tab(page, 'Inbox')).not.toHaveAttribute('data-detail', 'true')
  await expect(page.locator('.cat-tabs-measure')).toHaveCount(0)
})

test.describe('with deadlines', () => {
  test.use({ extras: true })

  for (const theme of THEMES) {
    test(`${theme}: all indicators on a wide strip, the usual form per tab on a narrow one, never overflowing`, async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
      const fin = tab(page, 'Financial')
      const sweep = async (widths: number[]) => {
        const shown: number[] = []
        for (const width of widths) {
          await page.setViewportSize({ width, height: 900 })
          await page.goto('/mail/inbox')
          await expect(fin.locator('[data-indicator]').first()).toBeVisible()
          // Let the strip settle (the counts arrive, the ResizeObserver picks).
          await page.waitForTimeout(150)
          shown.push(await fitsBest(page))
          const scrolls = await strip(page).evaluate((el) => el.scrollWidth > el.clientWidth + 1)
          if (!scrolls) await noOverflow(page)
          else expect(shown.at(-1)).toBe(0)
        }
        return shown
      }
      await putLayout(page, true)
      const shown = await sweep(widths([2560, 1440, 1000, 760]))
      // Some width gives a tab its fuller form. (The list pane is not wider at every wider
      // window: a reading pane opens beside it.)
      expect(Math.max(...shown)).toBeGreaterThan(0)
      // More tabs than the strip holds in full: each tab falls back on its own.
      const many = ['Shopping', 'Receipts', 'Travel', 'Family'].map((name) => ({ name, icon: 'folder', indicators: ['unread', 'all'], sections: sec() }))
      await putLayout(page, true, [...TABS, ...many])
      await sweep(widths([2560, 1440, 1000]))

      // With room, the Financial tab has its unread, deadline and count as pills. Selected, it
      // is the first to take its fuller form. (Office and Cards keep a list pane about 450 px wide
      // beside the reading pane at any window size; two tabs fit there.)
      await page.setViewportSize({ width: 1440, height: 900 })
      await putLayout(page, true, TABS.slice(0, 2))
      await page.goto('/mail/inbox')
      await fin.click()
      await expect(fin).toHaveAttribute('data-detail', 'true')
      await expect(fin.locator('.due')).toHaveText(/^Due Dec 3/)
      await expect(fin.locator('.count')).toHaveText(/^\d+$/)
      await expect(fin.locator('.new')).toHaveText(/^\d+ new$/)
      await fitsBest(page)
      await noOverflow(page)
      // With the setting off, the same strip shows the theme's usual form.
      await putLayout(page, false)
      await page.reload()
      await expect(fin.locator('[data-indicator]').first()).toBeVisible()
      await expect(fin).not.toHaveAttribute('data-detail', 'true')
      expect(await fin.locator('[data-indicator]').count()).toBeLessThan(3)
    })
  }

  test('a phone keeps one compact indicator per tab', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 })
    await putLayout(page, true)
    await page.goto('/mail/inbox')
    const fin = tab(page, 'Financial')
    await expect(fin.locator('[data-indicator]')).toHaveCount(1)
    await expect(strip(page).locator('[role="tab"][data-detail]')).toHaveCount(0)
  })

  for (const { theme, dark } of looks({ dark: true, all: ['classic', 'desk'] })) {
    test(`${theme} ${dark ? 'dark' : 'light'}: screenshot of the wide strip`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light', reducedMotion: 'reduce' })
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
      await putLayout(page, true)
      await page.goto('/mail/inbox')
      await expect(tab(page, 'Financial')).toHaveAttribute('data-detail', 'true')
      await fitsBest(page)
      await noOverflow(page)
      await page.locator('.cat-tabs-wrap').screenshot({ path: `${SHOTS}/${theme}-${dark ? 'dark' : 'light'}.png` })
    })
  }
})
