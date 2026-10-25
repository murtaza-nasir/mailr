// The shell's connection and version banners. A failed session check never takes
// the app down: offline, a network error, a timeout or a 5xx keeps everything on screen, says so,
// and retries with backoff. A newer build on the server offers a reload.
import { CloudOff, RefreshCw, WifiOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import { useMe } from '../../api/hooks'
import { appFromPath } from '../../lib/routes'
import { useNotesQueue } from '../notes/offline'
import { useOnline } from '../../hooks/useOnline'
import { applyUpdate } from '../../lib/serviceWorker'
import { backoffDelay, isTransient } from '../../lib/netErrors'
import { useAppStatus } from '../../stores/appStatus'
import { t as tr, tx } from '../../i18n'

interface Retryable {
  isError: boolean
  error: unknown
  errorUpdatedAt: number
  refetch: () => unknown
}

/** While `q` fails with a transient error, refetch it with growing delays (2 s up to 60 s). */
export function useReconnect(q: Retryable): void {
  const attempt = useRef(0)
  const failing = q.isError && isTransient(q.error)
  const { errorUpdatedAt, refetch } = q
  useEffect(() => {
    if (!failing) {
      attempt.current = 0
      return
    }
    const t = setTimeout(() => void refetch(), backoffDelay(attempt.current++))
    return () => clearTimeout(t)
  }, [failing, errorUpdatedAt, refetch])
}

export function ConnectionBanner() {
  const online = useOnline()
  const me = useMe()
  const newVersion = useAppStatus((s) => s.newVersion)
  const [updating, setUpdating] = useState(false)
  const unreachable = online && me.isError && isTransient(me.error)
  // One offline line, about the app that is open (Notes and tables: what keeps working there).
  const app = appFromPath(useLocation().pathname)
  const waiting = useNotesQueue((s) => s.ops.filter((o) => o.state === 'queued').length)
  return (
    <>
      {!online && (
        <div className="banner no-print" role="status" data-connection="offline" style={{ margin: 12, borderRadius: 12 }}>
          <WifiOff />
          <span style={{ flex: 1 }}>
            {app === 'notes'
              ? <>{tx('layout.connectionBanner.youreOfflineListsKeepWorking', { value: waiting ? ` (${tr('layout.connectionBanner.changesWait', { count: waiting })})` : '' }, { b: (c) => <b>{c}</b> })}</>
              : <>{tx('layout.connectionBanner.youreOfflineShowingWhatsCached', undefined, { b: (c) => <b>{c}</b> })}</>}
          </span>
        </div>
      )}
      {unreachable && (
        <div className="banner no-print" role="status" data-connection="unreachable" style={{ margin: 12, borderRadius: 12 }}>
          <CloudOff />
          <span style={{ flex: 1 }}>
            {tx('layout.connectionBanner.cantReachMailrRetryingWhat', undefined, { b: (c) => <b>{c}</b> })}
          </span>
          <button type="button" className="btn tonal sm" onClick={() => void me.refetch()}>
            {tr('layout.connectionBanner.retryNow')}
          </button>
        </div>
      )}
      {newVersion && (
        <div className="banner no-print" role="status" data-connection="new-version" style={{ margin: 12, borderRadius: 12 }}>
          <RefreshCw />
          <span style={{ flex: 1 }}>
            {tx('layout.connectionBanner.newVersionOfMailrReload', undefined, { b: (c) => <b>{c}</b> })}
          </span>
          <button type="button" className="btn filled sm" disabled={updating} aria-busy={updating}
            onClick={() => { setUpdating(true); void applyUpdate() }}>
            {updating ? tr('layout.connectionBanner.updating') : tr('layout.connectionBanner.reload')}
          </button>
        </div>
      )}
    </>
  )
}
