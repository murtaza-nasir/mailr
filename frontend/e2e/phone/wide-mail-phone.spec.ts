// HTML mail on a phone (a fixed-width bank mail cut off at the
// right, a newsletter cut off on both sides). A body wider than the screen is zoomed to fit
// (lib/html.ts fitBodyZoom), as Gmail shows it; responsive mail stays at zoom 1. Pinch-to-zoom is
// the browser's own and still works. The screenshots are attached.
import { expect, REVIEW_SHOTS, test, type Page } from '../fixtures'
import { row } from './phone'

const FIXED_600 = `<table width="600" cellpadding="0" cellspacing="0" style="width:600px"><tr>
<td width="600" style="width:600px;padding:24px;background:#e8f0fe;font:16px Arial">
<h1 style="margin:0">Your statement is ready</h1><p>Balance <b>$1,234.56</b>, due <b>Oct 28</b>.</p>
<table width="552"><tr><td width="276">Left column START</td><td width="276" align="right">Right column END</td></tr></table>
</td></tr></table>`
// A 600 px block centred in a flex row: it overflows on both sides, and no scrolling reaches the left.
const CENTRED = `<div style="display:flex;justify-content:center"><div style="flex:none;width:600px;background:#fef7e0;padding:12px;box-sizing:border-box">
<p>START of a centred block that is six hundred pixels wide and wraps over a few lines. END</p></div></div>`
const RESPONSIVE = `<div style="max-width:600px;margin:0 auto"><p>A responsive mail. Its paragraphs wrap on a phone and need no zoom. END</p></div>`

async function openWith(page: Page, html: string) {
  await page.route('**/api/v1/messages/*/body**', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    await route.fulfill({ response: res, json: { ...body, html, text: null, source: 'html', quoted_html_start: null } })
  })
  await page.goto('/mail/inbox')
  await row(page, 'E2E Read me').click()
  const frame = page.locator('.conv-view iframe').first()
  await expect(frame).toBeVisible()
  await expect.poll(() => frame.evaluate((f: HTMLIFrameElement) => f.contentDocument?.body?.textContent ?? '')).toContain('END')
  return frame
}

/** The frame's page: its width, how far it scrolls sideways, what sits left of it, and its zoom. */
const probe = (f: HTMLIFrameElement) => {
  const d = f.contentDocument!
  const root = d.documentElement
  const els = Array.from(d.body.querySelectorAll('*'))
  return {
    view: root.clientWidth,
    scroll: root.scrollWidth,
    left: Math.min(0, ...els.map((e) => e.getBoundingClientRect().left)),
    right: Math.max(...els.map((e) => e.getBoundingClientRect().right)),
    zoom: Number(root.style.zoom || 1),
    frameRight: f.getBoundingClientRect().right,
  }
}

for (const [name, html] of [['fixed 600 px table', FIXED_600], ['centred wide block', CENTRED]] as const) {
  test(`a ${name} fits the screen width, with nothing cut off`, async ({ page }, info) => {
    const frame = await openWith(page, html)
    await expect.poll(async () => (await frame.evaluate(probe)).zoom).toBeLessThan(1)
    const p = await frame.evaluate(probe)
    expect(p.scroll, JSON.stringify(p)).toBeLessThanOrEqual(p.view + 2)
    expect(p.left, 'nothing left of the page').toBeGreaterThanOrEqual(-1)
    expect(p.right, 'nothing right of the page').toBeLessThanOrEqual(p.view + 2)
    // The frame itself stays inside the screen.
    expect(p.frameRight).toBeLessThanOrEqual(page.viewportSize()!.width)
    await expect(frame.contentFrame().getByText('START', { exact: false }).first()).toBeVisible()
    const shot = info.outputPath(`${name.replace(/ /g, '-')}.png`)
    await page.screenshot({ path: shot })
    if (REVIEW_SHOTS) await info.attach(name, { path: shot, contentType: 'image/png' })
  })
}

test('a responsive mail keeps zoom 1', async ({ page }) => {
  const frame = await openWith(page, RESPONSIVE)
  await page.waitForTimeout(300)
  const p = await frame.evaluate(probe)
  expect(p.zoom).toBe(1)
  expect(p.scroll).toBeLessThanOrEqual(p.view + 2)
})
