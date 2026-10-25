// Helpers for the phone and installed-app tests (e2e/phone, e2e/pwa; playwright.config.ts projects
// pixel, iphone, pixel-pwa, iphone-pwa).
import type { Locator, Page } from '@playwright/test'

export const isIPhone = (projectName: string) => projectName.startsWith('iphone')

/** Run as the installed app: display-mode standalone, and iOS's navigator.standalone. */
export async function emulateStandalone(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true })
    const orig = window.matchMedia.bind(window)
    window.matchMedia = (q: string) => {
      if (!/display-mode:\s*standalone/.test(q)) return orig(q)
      const noop = () => {}
      return { matches: true, media: q, onchange: null, addEventListener: noop, removeEventListener: noop, addListener: noop, removeListener: noop, dispatchEvent: () => false } as unknown as MediaQueryList
    }
  })
}

/** Safe-area insets as a notched iPhone has them in the installed app. The layout reads them
 *  through --sat/--sab/--sal/--sar (styles/phone.css), which default to env(safe-area-inset-*). */
export async function notch(page: Page, top = 47, bottom = 34) {
  await page.addInitScript(([t, b]) => {
    const add = () => {
      const s = document.createElement('style')
      s.textContent = `:root { --sat: ${t}px !important; --sab: ${b}px !important; }`
      document.head.appendChild(s)
    }
    if (document.head) add()
    else document.addEventListener('DOMContentLoaded', add)
  }, [top, bottom] as const)
}

/** A finger on the element: touchstart, a few touchmoves by (dx, dy), touchend. */
export async function drag(target: Locator, dx: number, dy: number, opts: { steps?: number; holdMs?: number } = {}) {
  const box = await target.boundingBox()
  if (!box) throw new Error('no box')
  const x = box.x + box.width / 2
  const y = box.y + Math.min(box.height / 2, 30)
  await target.evaluate(
    async (el, { x, y, dx, dy, steps, holdMs }) => {
      // Plain events with touch lists: WebKit has no Touch constructor, and the gestures read
      // only touches[0].clientX/Y (useTouchList).
      const mk = (cx: number, cy: number) => ({ identifier: 1, target: el, clientX: cx, clientY: cy, pageX: cx, pageY: cy, screenX: cx, screenY: cy })
      const fire = (type: string, t: ReturnType<typeof mk> | null) => {
        const ev = new Event(type, { bubbles: true, cancelable: true, composed: true })
        const live = t && type !== 'touchend' ? [t] : []
        Object.defineProperties(ev, { touches: { value: live }, targetTouches: { value: live }, changedTouches: { value: t ? [t] : [] } })
        el.dispatchEvent(ev)
      }
      fire('touchstart', mk(x, y))
      if (holdMs) await new Promise((r) => setTimeout(r, holdMs))
      for (let i = 1; i <= steps; i++) {
        fire('touchmove', mk(x + (dx * i) / steps, y + (dy * i) / steps))
        await new Promise((r) => setTimeout(r, 16))
      }
      fire('touchend', mk(x + dx, y + dy))
    },
    { x, y, dx, dy, steps: opts.steps ?? 12, holdMs: opts.holdMs ?? 0 },
  )
}

/** Wait until the element's animations (a sheet sliding up) have finished. */
export async function settled(target: Locator) {
  await target.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished.catch(() => undefined))))
}

/** Press and hold without moving. */
export async function longPress(target: Locator, ms = 650) {
  await drag(target, 0, 0, { steps: 0, holdMs: ms })
}

export const rows = (page: Page) => page.locator('[data-mail-list] [role="row"][data-id]')
export const row = (page: Page, subject: string) => rows(page).filter({ hasText: subject }).first()
