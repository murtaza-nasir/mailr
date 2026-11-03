// A saved draft shown in the thread (MessageCard). Its id stays the same through every save, so
// the body cached for it (api/hooks.ts messageBodyQuery, staleTime Infinity) must be marked stale
// whenever the draft changes: by this tab, another tab or device, the assistant, an agent, or an
// approval the worker applies.

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { Message } from '../../api/types'
import { CLIENT_ID } from '../../lib/live/clientId'
import { subscribeItem } from '../../lib/live/store'

/** How long a draft card says who changed it ("Updated by Ambrose · just now"). */
export const UPDATED_NOTE_MS = 30_000

/** Mark the cached body of draft `id` stale: an open card refetches it, a hidden one on its next show. */
export function invalidateDraftBody(qc: QueryClient, id: number | string) {
  const n = Number(id)
  if (!Number.isFinite(n)) return
  void qc.invalidateQueries({ queryKey: ['body', n] })
  void qc.invalidateQueries({ queryKey: ['body-highlight', n] })
}

/** What the conversation says about a draft's content: it changes with every save (the snippet,
 *  and the Date header written at each save). */
export function draftRevision(m: Pick<Message, 'draft' | 'date' | 'snippet' | 'subject'>): string | null {
  return m.draft ? `${m.date ?? ''}\u0000${m.subject ?? ''}\u0000${m.snippet ?? ''}` : null
}

/** For a draft card: refetch the body when the conversation shows a new revision (this covers
 *  live events missed while the stream was down), and return who else changed it in the last
 *  UPDATED_NOTE_MS (null otherwise). */
export function useDraftCard(m: Pick<Message, 'id' | 'draft' | 'date' | 'snippet' | 'subject'>): { by: string } | null {
  const qc = useQueryClient()
  const rev = draftRevision(m)
  const seen = useRef(rev)
  useEffect(() => {
    if (rev === seen.current) return
    seen.current = rev
    if (rev !== null) invalidateDraftBody(qc, m.id)
  }, [rev, qc, m.id])

  const [updated, setUpdated] = useState<{ by: string } | null>(null)
  useEffect(() => {
    if (!m.draft) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const unsub = subscribeItem('draft', m.id, (ev) => {
      if (ev.deleted || ev.version == null) return
      // This tab's own saves need no note: the user just made them.
      if (ev.origin && ev.origin === CLIENT_ID) return
      setUpdated({ by: ev.by })
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => setUpdated(null), UPDATED_NOTE_MS)
    })
    return () => {
      unsub()
      if (timer) clearTimeout(timer)
    }
  }, [m.draft, m.id])
  return updated
}
