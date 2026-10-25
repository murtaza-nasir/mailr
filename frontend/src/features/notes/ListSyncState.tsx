// The state of an open list's offline changes, in the editor's meta
// line, which is always there, so nothing moves when it changes. The app-wide banner says once that
// you are offline and what keeps working (layout/ConnectionBanner.tsx); here the list says only its
// own state: how many changes wait, "Sending 2 changes…", and a change the server refused, with
// Retry and Discard. Rows that wait have a dashed edge (Checklist.tsx).

import { useQueryClient } from '@tanstack/react-query'
import { discard, retry, sendNotesQueue, useNotesQueue } from './offline'
import type { Note } from './types'
import { t } from '../../i18n'

export function ListSyncState({ note, online }: { note: Note; online: boolean }) {
  const qc = useQueryClient()
  const ops = useNotesQueue((s) => s.ops)
  const mine = ops.filter((o) => o.note === note.id)
  const waiting = mine.filter((o) => o.state === 'queued').length
  const failed = mine.filter((o) => o.state === 'failed')
  if (failed.length) {
    const f = failed[0]
    return (
      <span className="nt-sync failed" role="alert" data-testid="note-offline-failed">
        {t('notes.listSyncState.changesMadeOfflineCouldNot', { count: failed.length })}: {f.text}. {f.error}{' '}
        <button type="button" className="btn text sm" onClick={() => void retry(f.id).then(() => sendNotesQueue(qc))}>{t('common.retry')}</button>
        <button type="button" className="btn text sm" onClick={() => void discard(f.id).then(() => qc.invalidateQueries({ queryKey: ['notes'] }))}>{t('common.discard')}</button>
        {' · '}
      </span>
    )
  }
  if (!waiting) return null
  return (
    <span className="nt-sync" role="status" data-testid="note-offline">
      {online ? t('notes.listSyncState.sendingMadeOffline', { value: t('notes.listSyncState.changes', { count: waiting }) }) : t('notes.listSyncState.onThisDevice', { value: t('notes.listSyncState.changesWait', { count: waiting }) })}{' · '}
    </span>
  )
}
