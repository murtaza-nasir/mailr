// ACT-SURFACES: web.settings
// The colours in every style: a preset and a custom colour with
// complementary partners are picked in Settings → Look → Colours, applied to the page, saved to the
// account, shown again after a reload before the first render, and every accent text/background
// pair on the main surfaces passes in light and dark (computed colours), for the chosen colours and
// for the style's own after Reset: text on a fill by APCA
// (|Lc| 60, WCAG 3:1 floor, and white where white reads better), the rest by WCAG AA.
// Covers: ACT settings.colours_preset, ACT settings.colours_custom (web.settings).
// With COLOUR_SHOTS=<dir> the inbox is saved in each style × light/dark × preset/custom.
import { mkdirSync } from 'node:fs'
import { apiSettled, expect, test, type Page } from './fixtures'
import { THEMES } from './matrix'

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'
/** The second preset of each style (the first is the style's own). */
const PRESET: Record<Theme, string> = { classic: 'classic.plum', office: 'office.wine', focus: 'focus.sage', desk: 'desk.amber', cards: 'cards.rose' }
const CUSTOM = '#b04a7a'
const SHOTS = process.env.COLOUR_SHOTS

async function settings(page: Page): Promise<Record<string, unknown>> {
  return (await (await page.request.get('/api/v1/me')).json()).settings
}

async function patch(page: Page, data: Record<string, unknown>) {
  const r = await page.request.patch('/api/v1/me/settings', { data })
  expect(r.ok(), await r.text()).toBeTruthy()
}

/**
 * Contrast of accent pairs, from the colours the browser computes (custom properties resolved on a
 * probe, read back as pixels so color-mix() and oklab come out as sRGB). Returns the failures.
 */
async function aaFailures(page: Page, root = 'html'): Promise<string[]> {
  return page.evaluate((sel) => {
    const host = document.querySelector(sel) as HTMLElement
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const ctx = canvas.getContext('2d', { willReadFrequently: true })!
    const probe = document.createElement('div')
    host.appendChild(probe)
    const px = (cssColour: string): [number, number, number] => {
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = '#fff'
      ctx.fillRect(0, 0, 1, 1)
      ctx.fillStyle = cssColour
      ctx.fillRect(0, 0, 1, 1)
      const d = ctx.getImageData(0, 0, 1, 1).data
      return [d[0], d[1], d[2]]
    }
    const resolve = (v: string) => {
      probe.style.color = `var(${v})`
      return px(getComputedStyle(probe).color)
    }
    const lum = ([r, g, b]: [number, number, number]) => {
      const f = (c: number) => ((c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    }
    const ratio = (a: [number, number, number], b: [number, number, number]) => {
      const [x, y] = [lum(a), lum(b)]
      return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
    }
    // APCA-W3 0.0.98G-4g (lib/colour/contrast.ts), for text on fills.
    const apca = (t: [number, number, number], b: [number, number, number]) => {
      const y = (p: number[]) => 0.2126729 * (p[0] / 255) ** 2.4 + 0.7151522 * (p[1] / 255) ** 2.4 + 0.072175 * (p[2] / 255) ** 2.4
      const k = (v: number) => (v > 0.022 ? v : v + (0.022 - v) ** 1.414)
      const [ty, by] = [k(y(t)), k(y(b))]
      if (Math.abs(by - ty) < 0.0005) return 0
      if (by > ty) { const x = (by ** 0.56 - ty ** 0.57) * 1.14; return x < 0.1 ? 0 : (x - 0.027) * 100 }
      const x = (by ** 0.65 - ty ** 0.62) * 1.14
      return x > -0.1 ? 0 : (x + 0.027) * 100
    }
    const fillFails = (fg: [number, number, number], bg: [number, number, number]) => {
      // Dark text (positive Lc) where white would read better is the mid-blue pill case.
      const lc = Math.abs(apca(fg, bg))
      const white = apca(fg, bg) > 0 && Math.abs(apca([255, 255, 255], bg)) > lc
      return lc < 60 || ratio(fg, bg) < 3 || white ? `Lc ${lc.toFixed(1)}${white ? ' (white reads better)' : ''}` : null
    }
    const pairs: [string, string, number | 'fill'][] = [
      ['--on-primary', '--primary', 'fill'],
      ['--on-primary-container', '--primary-container', 4.5],
      ['--nav-active-fg', '--nav-active', 4.5],
      ['--compose-fg', '--compose-bg', 'fill'],
      ['--accent-chip-fg', '--accent-chip-bg', 4.5],
      ['--accent-badge-fg', '--accent-badge-bg', 4.5],
      ['--primary', '--surface', 4.5],
      ['--primary', '--bg', 4.5],
      ['--link', '--surface', 4.5],
      ['--text', '--row-selected', 4.5],
      ['--focus-ring', '--surface', 3],
      ['--row-focused-bar', '--surface', 3],
    ]
    const out: string[] = []
    for (const [fg, bg, min] of pairs) {
      if (min === 'fill') {
        const f = fillFails(resolve(fg), resolve(bg))
        if (f) out.push(`${fg} on ${bg}: ${f}`)
        continue
      }
      const r = ratio(resolve(fg), resolve(bg))
      if (r < min) out.push(`${fg} on ${bg}: ${r.toFixed(2)} < ${min}`)
    }
    probe.remove()
    return out
  }, root)
}

/** The same check on real elements: text colour against the background drawn under it. Selectors in
 *  `fills` are text on a filled colour and take the APCA rule. */
async function elementFailures(page: Page, selectors: string[], fills: string[] = FILLS): Promise<string[]> {
  return page.evaluate(([sels, fillSels]) => {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const ctx = canvas.getContext('2d', { willReadFrequently: true })!
    const px = (c: string): [number, number, number, number] => {
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = c
      ctx.fillRect(0, 0, 1, 1)
      const d = ctx.getImageData(0, 0, 1, 1).data
      return [d[0], d[1], d[2], d[3]]
    }
    const lum = (p: number[]) => {
      const f = (c: number) => ((c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
      return 0.2126 * f(p[0]) + 0.7152 * f(p[1]) + 0.0722 * f(p[2])
    }
    const bgOf = (el: Element | null): number[] => {
      for (let e = el; e; e = e.parentElement) {
        const c = px(getComputedStyle(e).backgroundColor)
        if (c[3] > 250) return c
      }
      return [255, 255, 255]
    }
    const apca = (t: number[], b: number[]) => {
      const y = (p: number[]) => 0.2126729 * (p[0] / 255) ** 2.4 + 0.7151522 * (p[1] / 255) ** 2.4 + 0.072175 * (p[2] / 255) ** 2.4
      const k = (v: number) => (v > 0.022 ? v : v + (0.022 - v) ** 1.414)
      const [ty, by] = [k(y(t)), k(y(b))]
      if (Math.abs(by - ty) < 0.0005) return 0
      if (by > ty) { const x = (by ** 0.56 - ty ** 0.57) * 1.14; return x < 0.1 ? 0 : (x - 0.027) * 100 }
      const x = (by ** 0.65 - ty ** 0.62) * 1.14
      return x > -0.1 ? 0 : (x + 0.027) * 100
    }
    const out: string[] = []
    for (const sel of sels) {
      for (const el of document.querySelectorAll(sel)) {
        const fg = px(getComputedStyle(el).color)
        const bg = bgOf(el)
        const [x, y] = [lum(fg), lum(bg)]
        const r = (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
        if (fillSels.includes(sel)) {
          const lc = Math.abs(apca(fg, bg))
          const white = apca(fg, bg) > 0 && Math.abs(apca([255, 255, 255], bg)) > lc
          if (lc < 60 || r < 3 || white) out.push(`${sel}: Lc ${lc.toFixed(1)}${white ? ' (white reads better)' : ''}`)
        } else if (r < 4.5) out.push(`${sel}: ${r.toFixed(2)}`)
      }
    }
    return out
  }, [selectors, fills] as const)
}

/** Text on a filled colour: the primary button and the compose button. */
const FILLS = ['.ap-btn.filled', '.ap-btn.compose']
const PREVIEW = ['.ap-folder.on', '.ap-btn.filled', '.ap-btn.compose', '.ap-filter', '.signal-chip.needs_reply', '.ap-link', '.ap-row.on b']

for (const theme of THEMES) {
  test(`${theme}: a preset and a custom colour apply, persist and pass AA in light and dark`, async ({ page }) => {
    test.setTimeout(SHOTS ? 240_000 : 150_000)
    // The account's style, not the fixture's device-only Classic.
    await page.addInitScript(() => localStorage.removeItem('mailr.uiTheme'))
    await patch(page, { ui_theme: theme, ui_accent: null, theme: 'light' })
    await page.goto('/settings/look/look#colours')
    const section = page.locator('.accent-picker')
    await expect(section).toBeVisible()
    const html = page.locator('html')
    await expect(html).toHaveAttribute('data-ui-theme', theme)
    // The style's own colours first: nothing overridden, except Cards (its accents were always built).
    if (theme === 'cards') await expect(html).toHaveAttribute('data-accent-src', 'engine')
    else await expect(html).not.toHaveAttribute('data-accent-src', /.+/)
    const nativePrimary = await html.evaluate((el) => getComputedStyle(el).getPropertyValue('--primary').trim())

    // A preset.
    await section.locator(`[data-preset="${PRESET[theme]}"]`).click()
    await expect(html).toHaveAttribute('data-accent', PRESET[theme])
    await expect(html).toHaveAttribute('data-accent-src', 'engine')
    await expect.poll(() => html.evaluate((el) => getComputedStyle(el).getPropertyValue('--primary').trim())).not.toBe(nativePrimary)
    await apiSettled(page)
    expect((await settings(page)).ui_accent).toMatchObject({ preset: PRESET[theme], harmony: 'single' })
    expect(await aaFailures(page)).toEqual([])
    expect(await elementFailures(page, PREVIEW)).toEqual([])
    await shot(page, theme, 'preset', 'light')

    // A custom colour with complementary partners: the hex field and the harmony.
    await section.getByRole('radio', { name: 'Custom' }).click()
    const editor = page.getByTestId('accent-custom')
    await expect(editor).toBeVisible()
    await editor.getByLabel('Hex code').fill(CUSTOM)
    await editor.getByRole('radio', { name: 'Complementary', exact: true }).check()
    await expect(html).toHaveAttribute('data-accent', 'custom')
    await apiSettled(page)
    await expect.poll(async () => (await settings(page)).ui_accent).toEqual({ seed: CUSTOM, harmony: 'complementary' })
    expect(await aaFailures(page)).toEqual([])
    expect(await elementFailures(page, PREVIEW)).toEqual([])
    // The wheel's dot moves with the arrow keys and the hue it saves follows.
    const dot = editor.getByRole('slider', { name: /Colour/ })
    const before = await dot.getAttribute('aria-valuenow')
    await dot.focus()
    await page.keyboard.press('Shift+ArrowLeft')
    await expect(dot).not.toHaveAttribute('aria-valuenow', before!)
    await apiSettled(page)
    await expect.poll(async () => ((await settings(page)).ui_accent as { seed: string }).seed).not.toBe(CUSTOM)
    await editor.getByLabel('Hex code').fill(CUSTOM)
    await apiSettled(page)
    await expect.poll(async () => (await settings(page)).ui_accent).toEqual({ seed: CUSTOM, harmony: 'complementary' })
    await shot(page, theme, 'custom', 'light')

    // Saved to the account: a reload shows it, before React draws (the boot sheet).
    await page.addInitScript(() => {
      document.addEventListener('DOMContentLoaded', () => {
        ;(window as unknown as { __accentAtBoot: string | undefined }).__accentAtBoot = document.documentElement.dataset.accentSrc
      })
    })
    await page.reload()
    expect(await page.evaluate(() => (window as unknown as { __accentAtBoot: string | undefined }).__accentAtBoot)).toBe('engine')
    await expect(html).toHaveAttribute('data-accent', 'custom')

    // Dark: the same choice, every pair still passes.
    await patch(page, { theme: 'dark' })
    await page.reload()
    await expect(html).toHaveAttribute('data-theme', 'dark')
    await expect(page.locator('.accent-picker')).toBeVisible()
    expect(await aaFailures(page)).toEqual([])
    expect(await elementFailures(page, PREVIEW)).toEqual([])
    await shot(page, theme, 'custom', 'dark')
    await patch(page, { ui_accent: { preset: PRESET[theme], seed: '#000000', harmony: 'single' } })
    await page.reload()
    await expect(html).toHaveAttribute('data-accent', PRESET[theme])
    expect(await aaFailures(page)).toEqual([])
    await shot(page, theme, 'preset', 'dark')

    // Reset: the style's own colours again.
    await patch(page, { theme: 'light' })
    await page.reload()
    await page.locator('.accent-picker').getByRole('button', { name: 'Reset' }).click()
    await apiSettled(page)
    await expect.poll(async () => (await settings(page)).ui_accent ?? null).toBeNull()
    if (theme !== 'cards') await expect(html).not.toHaveAttribute('data-accent-src', /.+/)
    // The style's own colours keep the same rules, in light and dark (Focus dark's violet, 9 Oct).
    expect(await aaFailures(page)).toEqual([])
    expect(await elementFailures(page, PREVIEW)).toEqual([])
    await patch(page, { theme: 'dark' })
    await page.reload()
    await expect(html).toHaveAttribute('data-theme', 'dark')
    await expect(page.locator('.accent-picker')).toBeVisible()
    expect(await aaFailures(page)).toEqual([])
    expect(await elementFailures(page, PREVIEW)).toEqual([])
  })
}

test("the old Cards accent string still shows its colour, and the style's own palette shows elsewhere", async ({ page }) => {
  // A device-only style (the fixture sets Classic on every load; this runs after it).
  await page.addInitScript(() => {
    localStorage.setItem('mailr.uiTheme', sessionStorage.getItem('colours-theme') ?? 'cards')
    if (sessionStorage.getItem('colours-seeded')) return
    sessionStorage.setItem('colours-seeded', '1')
    localStorage.setItem('mailr.uiAccent', 'teal')
  })
  await page.goto('/mail/inbox')
  const html = page.locator('html')
  await expect(html).toHaveAttribute('data-accent', 'cards.teal')
  await expect.poll(() => html.evaluate((el) => getComputedStyle(el).getPropertyValue('--c-accent').trim())).toBe('#006a6a')
  // A preset belongs to its style: in Desk the Cards preset gives way to Desk's own colours.
  await page.evaluate(() => sessionStorage.setItem('colours-theme', 'desk'))
  await page.reload()
  await expect(html).toHaveAttribute('data-accent', 'desk.ocean')
  await expect(html).not.toHaveAttribute('data-accent-src', /.+/)
})

test('quick settings show the style presets; Custom leads to Settings → Look → Colours', async ({ page }) => {
  await page.goto('/mail/inbox')
  await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
  const row = page.getByRole('dialog', { name: 'Quick settings' }).locator('.accent-row')
  await expect(row.locator('[data-preset="classic.ocean"]')).toHaveAttribute('aria-checked', 'true')
  await row.locator('[data-preset="classic.forest"]').click()
  await expect(page.locator('html')).toHaveAttribute('data-accent', 'classic.forest')
  await row.getByRole('radio', { name: 'Custom' }).click()
  await expect(page).toHaveURL(/\/settings\/look\/look#colours/)
  await expect(page.locator('.accent-picker')).toBeVisible()
})

async function shot(page: Page, theme: Theme, kind: 'preset' | 'custom', scheme: 'light' | 'dark') {
  if (!SHOTS) return
  mkdirSync(SHOTS, { recursive: true })
  await page.locator('.accent-picker').screenshot({ path: `${SHOTS}/${theme}-${kind}-${scheme}-picker.png` })
  const url = page.url()
  await page.goto('/mail/inbox')
  await expect(page.locator('[role="row"], .conv-row, .card-row').first()).toBeVisible()
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/${theme}-${kind}-${scheme}.png` })
  await page.goto(url)
  await expect(page.locator('.accent-picker')).toBeVisible()
}

test("Classic dark with a mid blue (#009ccd, complementary): white text on the primary fills", async ({ page }) => {
  // Near-black text on the "new" pill read worse than white (APCA Lc 41 against -63).
  await page.addInitScript(() => localStorage.removeItem('mailr.uiTheme'))
  await patch(page, { ui_theme: 'classic', ui_accent: { seed: '#009ccd', harmony: 'complementary' }, theme: 'dark' })
  // One unread conversation in every tab, so each tab that shows unread has its "1 new" pill.
  await page.route('**/api/v1/layouts/*/counts*', async (route) => {
    const res = await route.fetch()
    const body = (await res.json()) as { tabs: Record<string, Record<string, unknown>> }
    for (const c of Object.values(body.tabs)) c.unread = 1
    await route.fulfill({ response: res, json: body })
  })
  await page.goto('/mail/inbox')
  const html = page.locator('html')
  await expect(html).toHaveAttribute('data-theme', 'dark')
  await expect(html).toHaveAttribute('data-accent-src', 'engine')
  const prop = (name: string) => html.evaluate((el, n) => getComputedStyle(el).getPropertyValue(n).trim(), name)
  await expect.poll(() => prop('--on-primary')).toBe('#ffffff')
  expect(await prop('--primary')).toBe('#009ccd')
  expect(await aaFailures(page)).toEqual([])
  const pill = page.locator('.cat-tab .new')
  await expect(pill.first()).toBeVisible()
  expect(await pill.first().evaluate((el) => getComputedStyle(el).color)).toBe('rgb(255, 255, 255)')
  if (await pill.count()) expect(await elementFailures(page, ['.cat-tab .new'], ['.cat-tab .new'])).toEqual([])
  if (SHOTS) {
    mkdirSync(SHOTS, { recursive: true })
    await expect(page.locator('[role="row"], .conv-row, .card-row').first()).toBeVisible()
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${SHOTS}/owner-classic-dark-inbox.png` })
    if (await pill.count()) await page.locator('.cat-tabs').screenshot({ path: `${SHOTS}/owner-classic-dark-tabs.png` })
  }
  await page.goto('/settings/look/look#colours')
  await expect(page.locator('.accent-picker')).toBeVisible()
  expect(await elementFailures(page, PREVIEW)).toEqual([])
  if (SHOTS) await page.locator('.accent-picker').screenshot({ path: `${SHOTS}/owner-classic-dark-picker.png` })
})
