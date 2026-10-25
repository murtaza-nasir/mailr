// The task pane's "From:" line (web.pane): for a task made from a recording's
// action items (or linked to one), the line gives the recording's title and the moment; a click
// opens the recording sheet there. Nothing shows without a Speakr account or a linked recording.

import { Play } from 'lucide-react'
import { useSpeakrStatus, useTaskRecordings } from './api'
import { fmtT } from './format'
import { useRecordingSheet } from './store'
import './speakr.css'
import { t } from '../../i18n'

// ACT recording.view @web.pane
export function TaskRecording({ uid }: { uid: string }) {
  const status = useSpeakrStatus()
  const q = useTaskRecordings(uid, !!status.data?.connected)
  const items = q.data?.items ?? []
  if (!status.data?.connected || !items.length) return null
  return (
    <>
      <dt>{t('common.from')}</dt>
      <dd className="tk-field-row" data-testid="task-recording">
        {items.map((r) => (
          <button key={`${r.connector_id}:${r.recording_id}`} type="button" className="tk-chip tk-chip-link"
            onClick={() => useRecordingSheet.getState().show(r.connector_id, r.recording_id, r.t)}>
            <Play size={13} aria-hidden />{' '}{t('speakr.taskRecording.from', { title: r.title || t('common.untitledRecording'), value: r.t != null ? ` · ${fmtT(r.t)}` : '' })}
          </button>
        ))}
      </dd>
    </>
  )
}
