// The installed app's background chores while the shell is mounted: send changes made offline
// when the connection is back, keep the app badge on the Inbox's unread count, close mail
// notifications once their conversation is read (here or on another device: the `counts` event
// and the change stream are the signal), and pick up where the app was after iOS ended it.
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { api, type ApiError } from '../../api/client'
import { invalidateMail, qk } from '../../api/hooks'
import type { ActionResponse, ConversationDetail, ConversationSummary, Page } from '../../api/types'
import { useOnline } from '../../hooks/useOnline'
import { drain, loadQueue, usePending } from '../../lib/offlineQueue'
import { isStandalone } from '../../lib/pwa'
import { announceUser, closeConversationNotifications, onWorkerMessage, setBadge, shownConversationNotifications } from '../../lib/serviceWorker'
import { toast } from '../../stores/ui'
import { useSidebarCounts } from '../layout/sidebarCounts'
import { useNotesOfflineSync } from '../notes/offlineSync'
import { t as tr } from '../../i18n'

async function sendAll(qc: QueryClient): Promise<void> {
  const r = await drain(async (op) => {
    await api.post<ActionResponse>('/conversations/actions', op.req as unknown as Record<string, unknown>, { idempotencyKey: op.id })
  })
  if (r.sent || r.dropped || r.failed) invalidateMail(qc)
  if (r.failed) {
    toast({
      message: tr('pwa.usePwaSync.changesCouldNotBeMade', { count: r.failed }),
      error: true, actionLabel: tr('common.review'), onAction: () => usePending.getState().setOpen(true),
    })
  } else if (r.sent) {
    toast({ message: tr('pwa.usePwaSync.offlineChangesWereSent', { count: r.sent }) })
  }
}

/** "Send now" in Pending changes. */
export function useSyncNow(): () => void {
  const qc = useQueryClient()
  return useCallback(() => void sendAll(qc), [qc])
}

/** Is this conversation read, as far as this tab knows? undefined: it does not know. */
function knownRead(qc: QueryClient, id: number): boolean | undefined {
  const det = qc.getQueryData<ConversationDetail>(qk.conversation(id))
  if (det) return det.conversation.unread_count === 0
  for (const [, page] of qc.getQueriesData<Page<ConversationSummary>>({ queryKey: qk.conversations })) {
    const c = page?.items?.find((x) => x.id === id)
    if (c) return c.unread_count === 0
  }
  return undefined
}

const RESUME_KEY = 'mailr.resume'
/** An installed app iOS ended in the background starts at the start page: back to where it was within this long. */
const RESUME_MS = 60 * 60_000

export function usePwaSync(userId: number | undefined): void {
  const qc = useQueryClient()
  const online = useOnline()
  const queued = usePending((s) => s.ops.some((o) => o.state === 'queued'))
  // Lists edited offline (features/notes/offline.ts): their own queue, sent the same way.
  useNotesOfflineSync(userId)

  // The queue of this user, and the worker's cache for this user.
  useEffect(() => {
    if (userId == null) return
    announceUser(userId)
    void loadQueue(userId).then(() => (navigator.onLine ? sendAll(qc) : undefined))
  }, [userId, qc])

  // Back online, back in view, and every 10 s while something waits (navigator.onLine is not
  // to be trusted: a phone on a captive or flaky network says online and fails; a failed pass
  // costs one request).
  useEffect(() => {
    if (!queued) return
    if (online) void sendAll(qc)
    const t = setInterval(() => void sendAll(qc), 10_000)
    const again = () => void sendAll(qc)
    const onVisible = () => {
      if (document.visibilityState === 'visible') again()
    }
    window.addEventListener('online', again)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(t)
      window.removeEventListener('online', again)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [online, queued, qc])

  // A notification's Archive or Mark read changed mail while the app was open.
  useEffect(() => onWorkerMessage((m) => {
    if (m.type === 'mailr:mail-changed') invalidateMail(qc)
  }), [qc])

  // The badge follows the Inbox's unread count; notifications of read conversations close.
  const counts = useSidebarCounts().data
  const unread = counts?.views?.inbox?.unread
  useEffect(() => {
    if (typeof unread === 'number') void setBadge(unread)
  }, [unread])
  const cursor = counts?.cursor
  const checking = useRef(false)
  useEffect(() => {
    if (cursor == null || checking.current) return
    checking.current = true
    void (async () => {
      try {
        const shown = await shownConversationNotifications()
        const read: number[] = []
        for (const id of shown.slice(0, 10)) {
          let r = knownRead(qc, id)
          if (r === undefined) {
            const det = await api.get<ConversationDetail>(`/conversations/${id}`).catch((e: ApiError) => (e.status === 404 ? null : undefined))
            r = det === null ? true : det ? det.conversation.unread_count === 0 : undefined
          }
          if (r) read.push(id)
        }
        await closeConversationNotifications(read)
      } finally {
        checking.current = false
      }
    })()
  }, [cursor, qc])
}

/** In the installed app: remember the place, and go back to it after iOS restarted the app. */
export function useResumePlace(): void {
  const location = useLocation()
  const navigate = useNavigate()
  const first = useRef(true)
  useEffect(() => {
    if (!isStandalone()) return
    const here = location.pathname + location.search
    if (first.current) {
      first.current = false
      try {
        const saved = JSON.parse(localStorage.getItem(RESUME_KEY) ?? 'null') as { path: string; at: number } | null
        if (here === '/mail/inbox' && saved && saved.path !== here && Date.now() - saved.at < RESUME_MS && saved.path.startsWith('/')) {
          navigate(saved.path, { replace: true })
          return
        }
      } catch {
        /* nothing saved */
      }
    }
    try {
      localStorage.setItem(RESUME_KEY, JSON.stringify({ path: here, at: Date.now() }))
    } catch {
      /* private mode */
    }
  }, [location.pathname, location.search, navigate])
}
