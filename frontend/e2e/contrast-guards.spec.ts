// Contrast of two small things across the themes (accessibility):
// - the time of a past event in the month view (dimmed with its chip it fell to 3.85–4.23:1
//   at 12 px in the light themes; it needs 4.5:1);
// - the empty task circle, a control that needs 3:1 against its background (WCAG 1.4.11; the
//   Android app measured 1.37:1 there, and the web shares tokens).
// Covers: web.a11y.
import type { Locator } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'
import { mkTask, openApp } from './helpers'
import { looks, THEMES } from './matrix'

/** The contrast of an element's text (or border) colour, through the opacity of its ancestors,
 *  against the first background behind it. */
async function contrastOf(el: Locator, prop: 'color' | 'borderTopColor'): Promise<number> {
  return el.evaluate((node, p) => {
    const ctx = document.createElement('canvas').getContext('2d')!
    const rgba = (c: string): [number, number, number, number] => {
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = '#000'
      ctx.fillStyle = c
      ctx.fillRect(0, 0, 1, 1)
      const d = ctx.getImageData(0, 0, 1, 1).data
      return [d[0], d[1], d[2], d[3] / 255]
    }
    let alpha = 1
    let bg: [number, number, number, number] | null = null
    for (let e: Element | null = node; e; e = e.parentElement) {
      const cs = getComputedStyle(e)
      alpha *= Number(cs.opacity)
      if (e !== node) {
        const b = rgba(cs.backgroundColor)
        if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && b[3] > 0.99) { bg = b; break }
      }
    }
    bg ??= [255, 255, 255, 1]
    const fg = rgba(getComputedStyle(node)[p as 'color'])
    const a = alpha * fg[3]
    const mixed = fg.slice(0, 3).map((v, i) => v * a + bg![i] * (1 - a))
    const lum = (c: number[]) => {
      const [r, g, b] = c.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4 })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const [l1, l2] = [lum(mixed), lum(bg.slice(0, 3))].sort((x, y) => y - x)
    return (l1 + 0.05) / (l2 + 0.05)
  }, prop)
}

function yesterday(): string {
  const d = new Date(Date.now() - 86_400_000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

for (const theme of THEMES) {
  test(`${theme} light: a past event's time in the month view has 4.5:1`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    // "Dim past events" on (Settings → Calendar).
    expect((await page.request.patch('/api/v1/me/settings', { data: { calendar_dim_past: true } })).ok()).toBeTruthy()
    const r = await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E past chip', start: `${yesterday()}T09:30:00`, end: `${yesterday()}T10:00:00`, tz: Intl.DateTimeFormat().resolvedOptions().timeZone } })
    expect(r.ok(), await r.text()).toBeTruthy()
    await page.setViewportSize({ width: 1440, height: 900 })
    await openApp(page, `/calendar/month/${yesterday()}`, { theme, dark: false })
    const time = page.locator('.cal-mchip.past', { hasText: 'E2E past chip' }).locator('.cal-mchip-time')
    await expect(time).toBeVisible()
    expect(await contrastOf(time, 'color')).toBeGreaterThanOrEqual(4.5)
  })
}

for (const { theme, dark } of looks({ dark: true })) {
  test(`${theme}${dark ? ' dark' : ''}: the empty task circle has 3:1`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    await mkTask(page.request, { summary: 'E2E ring task' })
    await openApp(page, '/tasks/all', { theme, dark })
    const ring = page.locator('.tk-check-ring:not(.on)').first()
    await expect(ring).toBeVisible()
    expect(await contrastOf(ring, 'borderTopColor')).toBeGreaterThanOrEqual(3)
  })
}
