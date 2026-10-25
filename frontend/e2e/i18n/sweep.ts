// The pseudo-language sweeps: every theme's main screens in en-XA, accented and
// about 35% longer, so truncation and overflow show before a real translation does, and in ar-XB,
// the right-to-left twin, so a layout that does not mirror shows. Shared by the desktop specs
// (e2e/i18n.spec.ts, e2e/rtl.spec.ts) and the phone specs (e2e/phone/i18n-phone.spec.ts,
// e2e/phone/rtl-phone.spec.ts).
import type { Page, TestInfo } from '@playwright/test'
import { expect, REVIEW_SHOTS } from '../fixtures'

export { THEMES } from '../matrix'

const failed = new WeakMap<Page, string[]>()

/** Start the page in a pseudo-language (en-XA, or ar-XB) and the given theme. Failed requests are
 *  noted: a page that never starts (lang still "en", an empty document) is a module request the
 *  browser dropped, usually net::ERR_NETWORK_CHANGED when a Docker container started (e2e/fixtures.ts
 *  networkGuard). */
export async function pseudoPage(page: Page, theme: string, lang: 'en-XA' | 'ar-XB' = 'en-XA') {
  failed.set(page, [])
  page.on('requestfailed', (r) => failed.get(page)?.push(`${r.failure()?.errorText ?? '?'} ${r.url()}`))
  await page.addInitScript(([t, l]) => {
    localStorage.setItem('mailr.uiTheme', t)
    localStorage.setItem('mailr.i18n.pseudo', '1')
    localStorage.setItem('mailr.language', l)
  }, [theme, lang] as const)
}

/** What overflows on the page: the page itself wider than the window, and text cut off. */
export async function overflow(page: Page): Promise<{ page: number; clipped: string[] }> {
  return page.evaluate(() => {
    const page = document.documentElement.scrollWidth - window.innerWidth
    const clipped: string[] = []
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('button, a, h1, h2, h3, label, .chip, .tab, th, [role="tab"], [role="menuitem"]'))) {
      const r = el.getBoundingClientRect()
      if (!r.width || !r.height || r.right < 0 || r.left > window.innerWidth) continue
      const s = getComputedStyle(el)
      const cut = (el.scrollWidth > el.clientWidth + 1 && (s.overflowX === 'hidden' || s.textOverflow === 'ellipsis')) || r.right > window.innerWidth + 1 || r.left < -1
      if (cut && el.textContent?.trim()) clipped.push(`${el.tagName.toLowerCase()}${el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''}: ${el.textContent.trim().slice(0, 50)}`)
    }
    return { page, clipped: [...new Set(clipped)] }
  })
}

/** Look at one screen: in the pseudo-language, no page-wide horizontal scroll, a screenshot kept.
 *  With SWEEP_SHOTS=<dir> the screenshot is also written there, for looking through a whole run. */
export async function sweepScreen(page: Page, info: TestInfo, name: string, opts: { maxClipped?: number; lang?: 'en-XA' | 'ar-XB' } = {}) {
  const lang = opts.lang ?? 'en-XA'
  await expect(page.locator('html'), `the app did not start; failed requests:\n${(failed.get(page) ?? []).slice(0, 10).join('\n')}`).toHaveAttribute('lang', lang)
  if (lang === 'ar-XB') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await page.waitForTimeout(400)
  const o = await overflow(page)
  // The screen goes into the report when a check below fails (or with E2E_SHOTS set), not every time.
  const failing = o.page > 1 || (opts.maxClipped !== undefined && o.clipped.length > opts.maxClipped)
  if (failing || REVIEW_SHOTS) {
    const shot = await page.screenshot()
    await info.attach(`${name}.png`, { body: shot, contentType: 'image/png' })
  }
  if (process.env.SWEEP_SHOTS) await page.screenshot({ path: `${process.env.SWEEP_SHOTS}/${info.project.name}-${name}.png` })
  if (o.clipped.length) await info.attach(`${name}-clipped.txt`, { body: o.clipped.join('\n'), contentType: 'text/plain' })
  if (process.env.I18N_SWEEP_LOG) console.log(`[${info.project.name}] ${name}: page +${o.page}px, ${o.clipped.length} clipped\n  ${o.clipped.slice(0, 15).join('\n  ')}`)
  expect(o.page, `${name}: the page scrolls sideways by ${o.page}px`).toBeLessThanOrEqual(1)
  if (opts.maxClipped !== undefined) expect(o.clipped.length, o.clipped.join('\n')).toBeLessThanOrEqual(opts.maxClipped)
}
