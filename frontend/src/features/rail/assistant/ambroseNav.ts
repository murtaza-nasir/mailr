// Links in Ambrose's chat. On a phone Ambrose covers the page, so an
// item opened from the chat (a citation chip, a source row, an "Open" card, an in-app link in the
// answer) would open under the panel and stay hidden. On the phone layout Ambrose therefore steps
// aside first (useUI.leaveAmbrose: the chat stays mounted and hidden) and the item opens on the
// page. The way back:
//   - "Back to Ambrose" (PhoneChrome's AmbroseReturn) and the Ambrose tab show the chat as it was;
//   - the browser's back gesture or button: the history entry the chat was open on is marked
//     (`ambrose: true` in its state), and going back to it opens Ambrose again (useAmbroseReturn).
// On a wider screen Ambrose sits beside the page, which shows the item at once: nothing changes there.
// Web links (web citations, links to other sites) open in a new tab on every layout.
import { useCallback, useEffect, useRef } from 'react'
import { useLocation, useNavigate, useNavigationType } from 'react-router'
import { useUI } from '../../../stores/ui'

/** The phone frame's breakpoint (PhoneChrome's usePhoneChrome). */
export const PHONE_QUERY = '(max-width: 900px)'

export function isPhoneLayout(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(PHONE_QUERY).matches
}

/** The first path segments of mailr's own pages (lib/routes.ts). */
const APP_SEGMENTS = new Set(['mail', 'calendar', 'tasks', 'notes', 'tables', 'recordings', 'settings'])

/**
 * The in-app path of a link in an answer, or null for a link that leaves mailr. A path such as
 * `/calendar/event/…` or a full URL on this server's own origin counts; anything else (another
 * site, `/api/…`, a protocol-relative `//host`) does not.
 */
export function appPathOf(href: string | null | undefined, origin = typeof location === 'undefined' ? 'http://localhost' : location.origin): string | null {
  if (!href) return null
  let url: URL
  try {
    if (href.startsWith('//')) return null
    url = new URL(href, origin)
  } catch {
    return null
  }
  if (url.origin !== origin) return null
  const seg = url.pathname.split('/')[1] ?? ''
  if (!APP_SEGMENTS.has(seg)) return null
  return `${url.pathname}${url.search}${url.hash}`
}

/** History state of an entry Ambrose was open on (phone layout). */
export interface AmbroseEntryState {
  ambrose?: boolean
}

/** Mark the current history entry as one with Ambrose open, without a navigation. The app's own state
 *  sits under `usr` in react-router's history state; location.state reads it back on return. */
function markEntry() {
  try {
    const st = (window.history.state ?? {}) as { usr?: Record<string, unknown> | null }
    window.history.replaceState({ ...st, usr: { ...(st.usr ?? {}), ambrose: true } }, '')
  } catch {
    /* a history the page cannot write: back simply goes back */
  }
}

/** Opens an in-app path from Ambrose's chat: in place beside the page, or with Ambrose stepping
 *  aside on the phone layout (see the top of this file). */
export function useAmbroseNav(): (to: string) => void {
  const nav = useNavigate()
  return useCallback(
    (to: string) => {
      if (!isPhoneLayout() || useUI.getState().panel !== 'agent') {
        nav(to)
        return
      }
      markEntry()
      useUI.getState().leaveAmbrose()
      nav(to)
    },
    [nav],
  )
}

/** Back (gesture or button) to an entry Ambrose was open on opens Ambrose again (phone layout). */
export function useAmbroseReturn(phone: boolean) {
  const location = useLocation()
  const type = useNavigationType()
  const marked = !!(location.state as AmbroseEntryState | null)?.ambrose
  // The page's first location is a POP too (a reload): that one leaves the panel closed, as on any load.
  const initial = useRef(location.key)
  const moved = useRef(false)
  useEffect(() => {
    if (location.key !== initial.current) moved.current = true
    if (!moved.current) return
    if (phone && marked && type === 'POP' && useUI.getState().panel !== 'agent') useUI.getState().setPanel('agent')
  }, [phone, marked, type, location.key])
}
