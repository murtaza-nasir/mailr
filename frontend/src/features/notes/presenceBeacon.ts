// Presence on a shared list, note or table (notes/presence.py). While a
// shared item is open, this tab says so every 15 s, and once more when it closes; the other people's
// open copies show "Cleo is in this list". A newcomer's first beat is a `hello`, so the people already
// there answer at once. Nothing is sent for an item only this viewer sees, or while offline.

import { useEffect, useState } from 'react'
import { getCsrf, request } from '../../api/client'
import { CLIENT_ID } from '../../lib/live/clientId'
import { BEAT_MS, HERE_MS, TOUCH_MS, hereNames, usePresence } from './presence'

const path = (kind: 'note' | 'table', id: string) => `/${kind}s/${encodeURIComponent(id)}/presence`

/** Someone greeted an item this tab may have open (api/events.ts dispatches it). */
export const HELLO_EVENT = 'mailr:presence-hello'

// ACT note.presence @web.note, ACT table.presence @web.table
export function usePresenceBeacon(kind: 'note' | 'table', id: string | null | undefined, enabled: boolean): void {
  useEffect(() => {
    if (!id || !enabled) return
    let stopped = false
    const beat = (hello = false) => {
      if (stopped || (typeof navigator !== 'undefined' && navigator.onLine === false)) return
      void request(path(kind, id), { method: 'POST', body: { state: 'here', hello } }).catch(() => undefined)
    }
    beat(true)
    const t = window.setInterval(() => beat(), BEAT_MS)
    const onHello = (e: Event) => {
      if ((e as CustomEvent<{ id: string }>).detail?.id === id) beat()
    }
    window.addEventListener(HELLO_EVENT, onHello)
    // A closing tab, or one leaving the app, still says goodbye (keepalive outlives the page).
    const left = () => {
      if (stopped) return
      stopped = true
      void fetch(`/api/v1${path(kind, id)}`, {
        method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrf(), 'X-Mailr-Client': CLIENT_ID },
        body: JSON.stringify({ state: 'left' }),
      }).catch(() => undefined)
    }
    window.addEventListener('pagehide', left)
    return () => {
      window.clearInterval(t)
      window.removeEventListener(HELLO_EVENT, onHello)
      window.removeEventListener('pagehide', left)
      left()
    }
  }, [kind, id, enabled])
}

/** The other people who have the item open, by first name; re-renders as a beat runs out. */
export function useHere(id: string | null | undefined, me: number | undefined): string[] {
  const m = usePresence((s) => (id ? s.here[id] : undefined))
  const [, tick] = useState(0)
  useEffect(() => {
    if (!m || !Object.keys(m).length) return
    const t = window.setInterval(() => tick((n) => n + 1), Math.min(5_000, HERE_MS))
    return () => window.clearInterval(t)
  }, [m])
  return id ? hereNames(id, me) : []
}

/** Another person's change to this item row, for a moment ("Cleo" beside the row), or null. */
export function useTouchedBy(itemId: string): string | null {
  const t = usePresence((s) => s.touched[itemId])
  const [, tick] = useState(0)
  const live = !!t && Date.now() - t.at < TOUCH_MS
  useEffect(() => {
    if (!live || !t) return
    const h = window.setTimeout(() => tick((n) => n + 1), Math.max(50, TOUCH_MS - (Date.now() - t.at)))
    return () => window.clearTimeout(h)
  }, [live, t])
  return live ? t!.name : null
}
