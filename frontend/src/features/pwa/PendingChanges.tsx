// Pending changes (as on Android): mail actions made offline that wait for the connection,
// the ones the server refused (Retry or Discard), and compose text only this device holds.
// A chip in the shell's banner area opens it; More lists it too.
import { CloudUpload, RefreshCw, RotateCcw, Trash2, TriangleAlert } from 'lucide-react'
import { useMemo } from 'react'
import { BottomSheet } from '../../components/BottomSheet'
import { Dialog } from '../../components/Dialog'
import { useMe } from '../../api/hooks'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useOnline } from '../../hooks/useOnline'
import { readLocalDrafts } from '../../lib/draftStore'
import { discard, retry, usePending } from '../../lib/offlineQueue'
import { useSyncNow } from './usePwaSync'
import { t, appLocale } from '../../i18n'

function ago(at: number): string {
  const m = Math.round((Date.now() - at) / 60_000)
  if (m < 1) return t('pwa.pendingChanges.justNow')
  if (m < 60) return t('pwa.pendingChanges.minAgo', { m })
  const h = Math.round(m / 60)
  return h < 24 ? t('pwa.pendingChanges.hAgo', { h }) : new Date(at).toLocaleDateString(appLocale())
}

/** The chip: how many changes wait, and whether any failed. */
export function PendingChip() {
  const ops = usePending((s) => s.ops)
  const setOpen = usePending((s) => s.setOpen)
  if (!ops.length) return null
  const failed = ops.filter((o) => o.state === 'failed').length
  return (
    <button type="button" className={`pending-chip no-print${failed ? ' failed' : ''}`} onClick={() => setOpen(true)} data-testid="pending-chip">
      {failed ? <TriangleAlert /> : <CloudUpload />}
      <span>
        {t('pwa.pendingChanges.pendingChanges', { count: ops.length })}
        {failed ? t('pwa.pendingChanges.failed', { failed }) : ''}
      </span>
    </button>
  )
}

function PendingList() {
  const ops = usePending((s) => s.ops)
  const draining = usePending((s) => s.draining)
  const paused = usePending((s) => s.paused)
  const online = useOnline()
  const sync = useSyncNow()
  const me = useMe().data
  const drafts = useMemo(() => Object.values(readLocalDrafts()).filter((d) => d.owner === me?.user.id), [me?.user.id])
  const sorted = [...ops].sort((a, b) => (a.state === b.state ? a.at - b.at : a.state === 'failed' ? -1 : 1))
  return (
    <div className="pending-list" data-testid="pending-list">
      <p className="muted" style={{ margin: '0 0 12px' }}>
        {!online
          ? t('pwa.pendingChanges.youAreOfflineTheseChanges')
          : paused
            ? t('pwa.pendingChanges.signInAgainToSend')
            : ops.length
              ? t('pwa.pendingChanges.theseChangesAreWaitingTo')
              : t('pwa.pendingChanges.nothingIsWaitingEveryChange')}
      </p>
      {sorted.map((o) => (
        <div key={o.id} className={`pending-row${o.state === 'failed' ? ' failed' : ''}`} data-state={o.state}>
          {o.state === 'failed' ? <TriangleAlert aria-hidden /> : <CloudUpload aria-hidden />}
          <div className="pending-text">
            <div>{o.text}</div>
            <div className="muted small">
              {ago(o.at)}
              {o.error ? ` · ${o.error}` : ''}
            </div>
          </div>
          {o.state === 'failed' && (
            <button type="button" className="icon-btn" aria-label={t('pwa.pendingChanges.retry', { text: o.text })} onClick={() => void retry(o.id).then(sync)}>
              <RotateCcw />
            </button>
          )}
          <button type="button" className="icon-btn" aria-label={t('pwa.pendingChanges.discard', { text: o.text })} onClick={() => void discard(o.id)}>
            <Trash2 />
          </button>
        </div>
      ))}
      {drafts.length > 0 && (
        <p className="muted small" style={{ marginTop: 12 }}>
          {t('pwa.pendingChanges.keptOnThisDeviceUntil', { count: drafts.length, value: t('pwa.pendingChanges.draftsAre', { count: drafts.length }) })}
        </p>
      )}
      {ops.length > 0 && (
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
          <button type="button" className="btn filled" disabled={!online || draining} onClick={sync}>
            <RefreshCw /> {draining ? t('common.sending') : t('common.sendNow')}
          </button>
        </div>
      )}
    </div>
  )
}

/** Pending changes: a bottom sheet on phones, a dialog elsewhere. */
export function PendingSheet() {
  const open = usePending((s) => s.open)
  const setOpen = usePending((s) => s.setOpen)
  const phone = useMediaQuery('(max-width: 900px)')
  if (phone) {
    return (
      <BottomSheet open={open} onClose={() => setOpen(false)} title={t('pwa.pendingChanges.pendingChanges2')} testId="pending-sheet">
        <PendingList />
      </BottomSheet>
    )
  }
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title={t('pwa.pendingChanges.pendingChanges2')}>
      <PendingList />
    </Dialog>
  )
}
