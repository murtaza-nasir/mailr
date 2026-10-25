// React glue for one live editor: hears the item's live events (not this tab's own saves), fetches
// the stored copy after the editor's own saves have landed, and reports typing.

import { useEffect, useRef } from 'react'
import { api } from '../../api/client'
import { CLIENT_ID } from './clientId'
import type { Content, Versioned } from './machine'
import { LIVE_RESYNC, subscribeItem, useEditing, useLive, type ItemEvent, type LiveKind } from './store'

export interface LiveItem<T extends Content = Content> {
  kind: LiveKind
  id: string | number
  version: number
  content: T
  by: string | null
}

export function fetchLive<T extends Content = Content>(kind: LiveKind, id: string | number): Promise<LiveItem<T>> {
  return api.get<LiveItem<T>>(`/live/${kind}/${encodeURIComponent(String(id))}`)
}

const lastTyping = new Map<string, number>()

/** The user typed in this item's editor: tell the server, at most once a second, so a write the
 *  assistant was asked to hold ("Let me finish typing first") waits for a pause. */
export function reportTyping(kind: LiveKind, id: string | number | null | undefined) {
  if (id == null || !useLive.getState().holding) return
  const k = `${kind}:${id}`
  const now = Date.now()
  if (now - (lastTyping.get(k) ?? 0) < 1000) return
  lastTyping.set(k, now)
  void api.post('/live/typing', { kind, id: String(id) }).catch(() => {})
}

export interface UseLiveItemOptions<T extends Content> {
  kind: LiveKind
  id: string | number | null | undefined
  /** Another writer stored a newer version: here it is. */
  onRemote: (theirs: Versioned<T>, ev: ItemEvent) => void
  /** The item was deleted (or a draft sent) elsewhere. */
  onDeleted?: (ev: ItemEvent) => void
  /** Resolves when this editor's own saves in flight have landed (a remote change is applied after them). */
  settled?: () => Promise<unknown>
  /** The version the editor has now; events at or below it are ignored. */
  version?: () => number | null
}

/** Subscribe an open editor to its item. Returns who is editing it now ("Assistant is editing…"). */
export function useLiveItem<T extends Content>(opts: UseLiveItemOptions<T>): { by: string } | null {
  const ref = useRef(opts)
  useEffect(() => {
    ref.current = opts
  })
  const { kind, id } = opts
  useEffect(() => {
    if (id == null) return
    let chain: Promise<unknown> = Promise.resolve()
    let wanted = 0
    let alive = true
    const handle = (ev: ItemEvent) => {
      if (ev.origin && ev.origin === CLIENT_ID) return
      if (ev.deleted) {
        ref.current.onDeleted?.(ev)
        return
      }
      const mine = ref.current.version?.() ?? null
      if (ev.version != null && mine != null && ev.version <= mine) return
      const n = ++wanted
      chain = chain.then(async () => {
        try {
          await ref.current.settled?.()
        } catch {
          /* a failed save still lets the change in */
        }
        if (!alive || n !== wanted) return // a newer event fetches the newer copy
        const cur = ref.current.version?.() ?? null
        if (ev.version != null && cur != null && ev.version <= cur) return
        try {
          const got = await fetchLive<T>(kind, id)
          if (!alive) return
          const now = ref.current.version?.() ?? null
          if (now != null && got.version <= now) return
          ref.current.onRemote({ version: got.version, content: got.content, by: got.by ?? ev.by }, ev)
        } catch {
          /* gone or offline: the next event or a reopen catches up */
        }
      })
    }
    const unsub = subscribeItem(kind, id, handle)
    // After a reconnect, events may have been missed: look once (a version this editor has is ignored).
    const resync = () => handle({ kind, id, version: null, by: '' })
    window.addEventListener(LIVE_RESYNC, resync)
    return () => {
      alive = false
      unsub()
      window.removeEventListener(LIVE_RESYNC, resync)
    }
  }, [kind, id])
  return useEditing(kind, id)
}
