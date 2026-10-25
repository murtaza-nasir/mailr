// The notes queue's chores while the app shell is mounted (offline.ts): load this user's waiting list
// changes, and send them when the connection is back, when the app comes back into view, and every
// 10 s while something waits (navigator.onLine is not to be trusted on a flaky line).

import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useOnline } from '../../hooks/useOnline'
import { loadNotesQueue, sendNotesQueue, useNotesQueue } from './offline'

export function useNotesOfflineSync(userId: number | undefined): void {
  const qc = useQueryClient()
  const online = useOnline()
  const queued = useNotesQueue((s) => s.ops.some((o) => o.state === 'queued'))

  useEffect(() => {
    if (userId == null) return
    void loadNotesQueue(userId).then(() => (navigator.onLine ? sendNotesQueue(qc) : undefined))
  }, [userId, qc])

  useEffect(() => {
    if (!queued) return
    if (online) void sendNotesQueue(qc)
    const t = setInterval(() => void sendNotesQueue(qc), 10_000)
    const again = () => void sendNotesQueue(qc)
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
}
