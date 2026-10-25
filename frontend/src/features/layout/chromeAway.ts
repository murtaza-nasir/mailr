// On short screens (200 % zoom on a laptop, a phone held sideways) the fixed bars leave little room
// for mail: at 720 × 450 CSS pixels the inbox showed one conversation (WCAG 1.4.10).
// There, scrolling a list or a conversation down moves the top bar and the phone tab bar out of the
// way, as phone browsers do with their own bars; scrolling up, or reaching the top, brings them
// back. html[data-chrome-away] carries the state; styles/layout.css hides the bars.
import { useEffect } from 'react'
import { useLocation } from 'react-router'

/** Short enough that the bars move away on scroll. */
export const SHORT_SCREEN = '(max-height: 600px)'

/** Whether the bars are away after a scroll to `top` that moved by `delta` pixels. Small moves
 *  (a touch jitter, a row growing) change nothing; near the top the bars always show. */
export function nextAway(away: boolean, top: number, delta: number): boolean {
  if (top < 24) return false
  if (delta > 6) return true
  if (delta < -6) return false
  return away
}

/** A scroll this soon after the bars moved is their own doing: the list's height changed, and with
 *  it the scroll position. Acting on it moved the bars back, and so on (Desk at 582 px: the rows
 *  shifted under the pointer every few frames, so a hover never held). */
const SETTLE_MS = 350
/** Only a scroll the user makes moves the bars: a wheel, a touch, a key, or a drag on the
 *  scrollbar within this long. A scroll the app makes (a row kept in view) leaves them. */
const INPUT_MS = 1000

let toggledAt = 0

function setAway(on: boolean) {
  const el = document.documentElement
  if (on === el.hasAttribute('data-chrome-away')) return
  el.toggleAttribute('data-chrome-away', on)
  toggledAt = performance.now()
}

export function useChromeAway() {
  const { pathname } = useLocation()
  // A new page starts with the bars shown.
  useEffect(() => setAway(false), [pathname])
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(SHORT_SCREEN)
    const last = new WeakMap<Element, number>()
    let inputAt = -Infinity
    let pointerDown = false
    const onInput = () => {
      inputAt = performance.now()
    }
    const onPointerDown = () => {
      pointerDown = true
      onInput()
    }
    const onPointerUp = () => {
      pointerDown = false
    }
    const onScroll = (e: Event) => {
      const t = e.target
      if (!mq.matches || !(t instanceof Element)) return
      // Never while the focus is in a bar that would go (the search field).
      if (document.activeElement?.closest('.app > header, .app > .d-toolbar')) return
      const top = t.scrollTop
      const prev = last.get(t) ?? 0
      last.set(t, top)
      // At the top the bars always show, whoever scrolled.
      if (top < 24) return setAway(false)
      const now = performance.now()
      if (now - toggledAt < SETTLE_MS) return
      if (!pointerDown && now - inputAt > INPUT_MS) return
      setAway(nextAway(document.documentElement.hasAttribute('data-chrome-away'), top, top - prev))
    }
    const onChange = () => {
      if (!mq.matches) setAway(false)
    }
    const inputs = ['wheel', 'touchmove', 'keydown'] as const
    for (const ty of inputs) document.addEventListener(ty, onInput, { capture: true, passive: true })
    document.addEventListener('pointerdown', onPointerDown, { capture: true, passive: true })
    document.addEventListener('pointerup', onPointerUp, { capture: true, passive: true })
    document.addEventListener('pointercancel', onPointerUp, { capture: true, passive: true })
    document.addEventListener('scroll', onScroll, { capture: true, passive: true })
    mq.addEventListener('change', onChange)
    return () => {
      for (const ty of inputs) document.removeEventListener(ty, onInput, { capture: true })
      document.removeEventListener('pointerdown', onPointerDown, { capture: true })
      document.removeEventListener('pointerup', onPointerUp, { capture: true })
      document.removeEventListener('pointercancel', onPointerUp, { capture: true })
      document.removeEventListener('scroll', onScroll, { capture: true })
      mq.removeEventListener('change', onChange)
      setAway(false)
    }
  }, [])
}
