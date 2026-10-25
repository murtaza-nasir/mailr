// Focus handling for anything modal: dialogs, the narrow-screen drawers of Calendar, Tasks,
// Recordings and Cards, and the note editor when it opens over the notes. While open:
// the focus moves inside, Tab and Shift+Tab stay inside, Escape closes. On close: the focus goes
// back where it was. Pair it with role="dialog" and aria-modal="true" on the element; a drawer
// adds data-modal-drawer, and global shortcuts stand down while one is open.
//
// Options: `escape: false` leaves Escape to the element's own key handler (the note editor's
// fields use Escape first). `inert: true` makes the rest of the page inert while open.
// `focusSelf` moves the focus to the element itself, not its first control (no on-screen
// keyboard on a phone); focus already inside stays where it is.
import { useEffect, useRef, type RefObject } from 'react'

export const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

function visible(el: HTMLElement): boolean {
  return el.offsetParent !== null || el.getClientRects().length > 0
}

/** Elements that stay usable beside a modal: the toasts (their Undo) and anything marked
 *  data-keep-active. Overlays added later (menus, dialogs on top) are not touched. */
const KEEP_ACTIVE = '.toasts, [data-keep-active]'

/** Makes everything in the page inert except `el`, its ancestors and KEEP_ACTIVE; returns the undo. */
export function inertOutside(el: HTMLElement): () => void {
  const keep = [el, ...document.querySelectorAll<HTMLElement>(KEEP_ACTIVE)]
  const made: HTMLElement[] = []
  const visit = (parent: Element) => {
    for (const child of Array.from(parent.children)) {
      if (!(child instanceof HTMLElement) || keep.includes(child)) continue
      if (keep.some((k) => child.contains(k))) visit(child)
      else if (!child.inert && !['SCRIPT', 'STYLE', 'LINK', 'TEMPLATE'].includes(child.tagName)) {
        child.inert = true
        made.push(child)
      }
    }
  }
  visit(document.body)
  return () => made.forEach((m) => (m.inert = false))
}

/** Whether closing `el` may put the focus back where it was: only while the focus is inside it or
 *  nowhere. Something that closes after the user moved on (the event editor that the detail's
 *  Edit opened, while the detail is still leaving) keeps its field focused. */
export function mayRestoreFocus(active: Element | null, el: Element | null): boolean {
  return !active || active.tagName === 'BODY' || !!el?.contains(active)
}

export function useModalFocus(
  ref: RefObject<HTMLElement | null>,
  open: boolean,
  onClose: () => void,
  opts: { initialFocus?: string; escape?: boolean; inert?: boolean; focusSelf?: boolean } = {},
) {
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const { initialFocus, escape = true, inert = false, focusSelf = false } = opts
  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    const el = ref.current
    const self = () => {
      if (!el) return
      if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1')
      el.focus({ preventScroll: true })
    }
    if (el && el.contains(document.activeElement)) {
      // Already inside (the element focused one of its own fields first).
    } else if (focusSelf) self()
    else {
      const target = (initialFocus && el?.querySelector<HTMLElement>(initialFocus)) || el?.querySelector<HTMLElement>(FOCUSABLE)
      if (target) target.focus()
      else self()
    }
    const restore = inert && el ? inertOutside(el) : null
    const onKey = (e: KeyboardEvent) => {
      if (!el) return
      // A dialog opened on top (it is later in the document) handles its own keys.
      const top = [...document.querySelectorAll<HTMLElement>('[aria-modal="true"]')].pop()
      if (top && top !== el && !el.contains(top)) return
      // An open menu (Popover, portalled outside) takes Escape and Tab first.
      if (document.querySelector('.popover')) return
      if (e.key === 'Escape') {
        if (!escape) return
        e.stopPropagation()
        e.preventDefault()
        onCloseRef.current()
      } else if (e.key === 'Tab') {
        const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(visible)
        if (!items.length) {
          e.preventDefault()
          return
        }
        const first = items[0]
        const last = items[items.length - 1]
        const active = document.activeElement as HTMLElement | null
        if (!active || !el.contains(active)) {
          // With the page inert, focus outside is in something meant to stay usable (the toasts,
          // the side panel, an ask card opened on top): Tab moves on there.
          if (inert && active && active !== document.body) return
          e.preventDefault()
          ;(e.shiftKey ? last : first).focus()
        } else if (e.shiftKey && active === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && active === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      restore?.()
      if (prev && prev.isConnected && prev !== document.body) prev.focus?.({ preventScroll: true })
    }
  }, [open, initialFocus, escape, inert, focusSelf, ref])
}
