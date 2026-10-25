// A card that floats beside a point or an element (quick create, the event detail popover). It
// differs from components/Popover in two ways the grid needs: it stays open while the grid
// scrolls, and it goes to the side of the anchor with more room (the end side first: the right, or
// the left in a right-to-left page), then keeps inside the viewport as its content grows.

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { isRtl } from '../../lib/rtl'

export function Floating({ anchor, onClose, label, className = '', children, testid }: {
  anchor: DOMRect | null; onClose: () => void; label: string; className?: string; children: ReactNode; testid?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const close = useRef(onClose)
  close.current = onClose

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const place = () => {
      const w = el.offsetWidth
      const h = el.offsetHeight
      const vw = window.innerWidth
      const vh = window.innerHeight
      if (!anchor) {
        setPos({ top: Math.max(16, Math.min(vh * 0.18, vh - h - 16)), left: Math.max(8, (vw - w) / 2) })
        return
      }
      const gap = 10
      const right = anchor.right + gap
      const left = anchor.left - gap - w
      const fitsRight = right + w <= vw - 8
      const fitsLeft = left >= 8
      let x = isRtl()
        ? (fitsLeft ? left : fitsRight ? right : Math.max(8, Math.min(anchor.right - w, vw - w - 8)))
        : (fitsRight ? right : fitsLeft ? left : Math.max(8, Math.min(anchor.left, vw - w - 8)))
      if (vw < 600) x = Math.max(8, (vw - w) / 2)
      const y = Math.max(8, Math.min(anchor.top - 24, vh - h - 8))
      setPos({ top: y, left: x })
    }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(el)
    window.addEventListener('resize', place)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', place)
    }
  }, [anchor])

  // Once placed, the element marked data-autofocus takes focus. The card waits unplaced with
  // opacity 0, not visibility: hidden, because a field that was hidden a moment ago can refuse
  // the focus.
  const focused = useRef(false)
  const opener = useRef<Element | null>(document.activeElement)
  useEffect(() => {
    if (!pos || focused.current) return
    focused.current = true
    // After the click that opened it has finished (a focus during it does not stick), and after a
    // menu that opened it has put the focus back on its own button (Popover's F7 restore runs in a
    // frame). A text field also selects its text, so a restored draft can be typed over.
    const grab = () => {
      const el = ref.current?.querySelector<HTMLElement>('[data-autofocus]')
      if (!el || el.contains(document.activeElement)) return
      el.focus({ preventScroll: true })
      if (el instanceof HTMLInputElement) el.select()
    }
    // Tries for a short while: until the focus is in the card, or the user has put it somewhere
    // else (anything but the page, the opener and a menu button).
    const t0 = Date.now()
    const tryGrab = () => {
      const a = document.activeElement
      if (ref.current?.contains(a)) return
      if (!a || a === document.body || a === opener.current || a.matches('[aria-haspopup]')) grab()
      if (!ref.current?.contains(document.activeElement) && Date.now() - t0 < 400) setTimeout(tryGrab, 40)
    }
    setTimeout(tryGrab, 30)
  }, [pos])

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Element
      if (ref.current?.contains(t)) return
      // Dialogs opened from inside (scope, send updates) are not "outside".
      if (t.closest?.('.scrim, .popover, .toasts')) return
      close.current()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.querySelector('.scrim')) {
        e.stopPropagation()
        close.current()
      }
    }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [])

  return createPortal(
    <div
      ref={ref}
      className={`popover cal-pop cal-floating ${className}`}
      role="dialog"
      aria-label={label}
      data-testid={testid}
      // rtl-ok: screen coordinates, placed above for either direction
      style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, opacity: pos ? undefined : 0, pointerEvents: pos ? undefined : 'none' }}
    >
      {children}
    </div>,
    document.body,
  )
}
