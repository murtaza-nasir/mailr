// Touch gestures on the mail list, the same in every theme (rows are found by data-id):
// - swipe a row sideways for its swipe action (Settings → Mail → Reading → Swipe actions);
// - press and hold a row for its menu (as a right-click does);
// - pull the list down from the top to refresh.
// Plain DOM work on touch events, so a gesture never re-renders the list while it moves. Under
// prefers-reduced-motion rows snap instead of sliding.
import { useEffect, useRef, type RefObject } from 'react'
import { reducedMotion } from '../../lib/motion'

export interface SwipeLook {
  label: string
  tone: 'archive' | 'danger' | 'neutral' | 'snooze'
  removes: boolean
}

export interface TouchListOptions {
  /** What a swipe in this direction does to this row; null: no swipe. */
  swipeFor: (id: number, dir: 'right' | 'left') => SwipeLook | null
  /** The swipe was made. The row has slid out (removes) or back. */
  onSwipe: (id: number, dir: 'right' | 'left', row: HTMLElement) => void
  onLongPress: (id: number, x: number, y: number) => void
  onRefresh: () => Promise<unknown>
  enabled: boolean
}

const LONG_PRESS_MS = 480
const SLOP = 10
const PULL_MAX = 96
const PULL_ARM = 64

function opaqueBackground(el: HTMLElement | null): string {
  for (let e = el; e; e = e.parentElement) {
    const bg = getComputedStyle(e).backgroundColor
    if (bg && bg !== 'transparent' && !/rgba\(.*,\s*0\)$/.test(bg)) return bg
  }
  return getComputedStyle(document.body).backgroundColor || '#fff'
}

function tick() {
  try {
    navigator.vibrate?.(8)
  } catch {
    /* no haptics */
  }
}

export function useTouchList(scrollRef: RefObject<HTMLElement | null>, opts: TouchListOptions): void {
  const o = useRef(opts)
  o.current = opts
  useEffect(() => {
    const scroller = scrollRef.current
    if (!scroller || !opts.enabled) return
    const pane = scroller.parentElement as HTMLElement

    let startX = 0
    let startY = 0
    let startT = 0
    let mode: 'none' | 'swipe' | 'pull' | 'scroll' = 'none'
    let row: HTMLElement | null = null
    let id = 0
    let dir: 'right' | 'left' | null = null
    let look: SwipeLook | null = null
    let under: HTMLElement | null = null
    let armed = false
    let lastDx = 0
    let longTimer: ReturnType<typeof setTimeout> | null = null
    let suppressClick = false
    let ptr: HTMLElement | null = null
    let refreshing = false

    const clearLong = () => {
      if (longTimer) clearTimeout(longTimer)
      longTimer = null
    }

    const makeUnder = (r: HTMLElement, l: SwipeLook, d: 'right' | 'left') => {
      const sr = scroller.getBoundingClientRect()
      const rr = r.getBoundingClientRect()
      const u = document.createElement('div')
      u.className = `swipe-under tone-${l.tone} dir-${d}`
      u.style.top = `${rr.top - sr.top + scroller.scrollTop}px`
      u.style.left = `${rr.left - sr.left}px`
      u.style.width = `${rr.width}px`
      u.style.height = `${rr.height}px`
      u.setAttribute('aria-hidden', 'true')
      const s = document.createElement('span')
      s.textContent = l.label
      u.appendChild(s)
      scroller.prepend(u)
      return u
    }

    const resetRow = (r: HTMLElement) => {
      r.style.transform = ''
      r.style.transition = ''
      r.style.position = ''
      r.style.zIndex = ''
      r.style.background = ''
      r.classList.remove('swiping')
    }

    const settleBack = (r: HTMLElement, u: HTMLElement | null) => {
      const done = () => {
        resetRow(r)
        u?.remove()
      }
      if (reducedMotion()) return done()
      r.style.transition = 'transform 180ms cubic-bezier(0.2, 0, 0, 1)'
      r.style.transform = 'translateX(0)'
      setTimeout(done, 200)
    }

    const ensurePtr = () => {
      if (ptr) return ptr
      if (getComputedStyle(pane).position === 'static') pane.style.position = 'relative'
      ptr = document.createElement('div')
      ptr.className = 'ptr'
      ptr.setAttribute('role', 'status')
      ptr.setAttribute('aria-label', 'Pull to refresh')
      ptr.innerHTML = '<span class="ptr-spinner" aria-hidden="true"></span>'
      pane.appendChild(ptr)
      return ptr
    }

    const onStart = (e: TouchEvent) => {
      if (e.touches.length !== 1 || refreshing) return
      const t = e.touches[0]
      startX = t.clientX
      startY = t.clientY
      startT = Date.now()
      mode = 'none'
      dir = null
      look = null
      armed = false
      lastDx = 0
      row = (e.target as HTMLElement).closest<HTMLElement>('[data-id]')
      if (row && !scroller.contains(row)) row = null
      id = row ? Number(row.dataset.id) : 0
      clearLong()
      if (row && !(e.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]')) {
        const x = t.clientX
        const y = t.clientY
        const target = row
        longTimer = setTimeout(() => {
          longTimer = null
          if (mode !== 'none') return
          suppressClick = true
          tick()
          target.classList.add('pressed')
          setTimeout(() => target.classList.remove('pressed'), 250)
          o.current.onLongPress(Number(target.dataset.id), x, y)
        }, LONG_PRESS_MS)
      }
    }

    const onMove = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      const t = e.touches[0]
      const dx = t.clientX - startX
      const dy = t.clientY - startY
      if (mode === 'none') {
        if (Math.abs(dx) < SLOP && Math.abs(dy) < SLOP) return
        clearLong()
        if (row && Math.abs(dx) > Math.abs(dy) * 1.4) {
          dir = dx > 0 ? 'right' : 'left'
          look = o.current.swipeFor(id, dir)
          if (!look) {
            mode = 'scroll'
            return
          }
          mode = 'swipe'
          row.classList.add('swiping')
          row.style.position = 'relative'
          row.style.zIndex = '1'
          row.style.background = opaqueBackground(row)
          under = makeUnder(row, look, dir)
        } else if (dy > 0 && scroller.scrollTop <= 0 && Math.abs(dy) > Math.abs(dx)) {
          mode = 'pull'
        } else {
          mode = 'scroll'
          return
        }
      }
      if (mode === 'swipe' && row && dir) {
        e.preventDefault()
        // The row follows the finger in its direction only.
        const x = dir === 'right' ? Math.max(0, dx) : Math.min(0, dx)
        lastDx = x
        row.style.transform = `translateX(${x}px)`
        const threshold = Math.max(80, row.offsetWidth * 0.35)
        const now = Math.abs(x) >= threshold
        if (now !== armed) {
          armed = now
          under?.classList.toggle('armed', armed)
          if (armed) tick()
        }
      } else if (mode === 'pull') {
        if (scroller.scrollTop > 0) return
        e.preventDefault()
        const p = ensurePtr()
        const pull = Math.min(PULL_MAX, Math.max(0, dy) * 0.5)
        p.style.transform = `translate(-50%, ${pull}px) rotate(${pull * 3}deg)`
        p.style.opacity = String(Math.min(1, pull / PULL_ARM))
        const now = pull >= PULL_ARM
        if (now !== armed) {
          armed = now
          p.classList.toggle('armed', armed)
          if (armed) tick()
        }
      }
    }

    const onEnd = () => {
      clearLong()
      if (mode === 'swipe' && row && dir && look) {
        const r = row
        const d = dir
        const l = look
        const u = under
        const velocity = Math.abs(lastDx) / Math.max(1, Date.now() - startT)
        const commit = armed || (velocity > 0.6 && Math.abs(lastDx) > 48)
        if (!commit) settleBack(r, u)
        else if (l.removes) {
          const w = r.offsetWidth
          const done = () => {
            r.dataset.swiped = 'true'
            o.current.onSwipe(Number(r.dataset.id), d, r)
            // The row leaves with the list's update; the background goes with it.
            setTimeout(() => {
              u?.remove()
              if (r.isConnected) {
                delete r.dataset.swiped
                resetRow(r)
                r.style.opacity = ''
              }
            }, 900)
          }
          if (reducedMotion()) {
            r.style.opacity = '0'
            done()
          } else {
            r.style.transition = 'transform 160ms cubic-bezier(0.4, 0, 1, 1)'
            r.style.transform = `translateX(${d === 'right' ? w : -w}px)`
            setTimeout(done, 170)
          }
        } else {
          o.current.onSwipe(Number(r.dataset.id), d, r)
          settleBack(r, u)
        }
        suppressClick = true
      } else if (mode === 'pull' && ptr) {
        const p = ptr
        if (armed) {
          refreshing = true
          p.classList.add('spinning')
          p.style.transform = `translate(-50%, ${PULL_ARM}px)`
          void o.current.onRefresh().finally(() => {
            refreshing = false
            p.classList.remove('spinning', 'armed')
            p.style.transform = 'translate(-50%, 0)'
            p.style.opacity = '0'
          })
        } else {
          p.style.transform = 'translate(-50%, 0)'
          p.style.opacity = '0'
        }
      }
      mode = 'none'
      row = null
      under = null
      armed = false
    }

    // A long-press or a swipe is not also a tap on the row.
    const onClick = (e: MouseEvent) => {
      if (!suppressClick) return
      suppressClick = false
      e.preventDefault()
      e.stopPropagation()
    }
    // iOS opens its own menu (copy, look up) on a held link or text: the row's menu replaces it.
    const onContext = (e: Event) => {
      if (Date.now() - startT < 1500 && (e.target as HTMLElement).closest('[data-id]')) {
        e.preventDefault()
        e.stopPropagation()
      }
    }

    scroller.addEventListener('touchstart', onStart, { passive: true })
    scroller.addEventListener('touchmove', onMove, { passive: false })
    scroller.addEventListener('touchend', onEnd)
    scroller.addEventListener('touchcancel', onEnd)
    scroller.addEventListener('click', onClick, true)
    scroller.addEventListener('contextmenu', onContext, true)
    return () => {
      clearLong()
      scroller.removeEventListener('touchstart', onStart)
      scroller.removeEventListener('touchmove', onMove)
      scroller.removeEventListener('touchend', onEnd)
      scroller.removeEventListener('touchcancel', onEnd)
      scroller.removeEventListener('click', onClick, true)
      scroller.removeEventListener('contextmenu', onContext, true)
      ptr?.remove()
    }
  }, [scrollRef, opts.enabled])
}
