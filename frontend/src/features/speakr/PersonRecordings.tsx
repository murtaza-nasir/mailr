// The person card's Recordings list (web.people): recent Speakr recordings
// whose participants name this person (their confirmed speakers or their own full name), newest
// first, each opening the recording sheet. Nothing shows without a Speakr account or a recording.

import { Lock, Play } from 'lucide-react'
import { usePersonRecordings, useSpeakrStatus } from './api'
import { fmtDuration } from './format'
import { useRecordingSheet } from './store'
import './speakr.css'
import { t, appLocale } from '../../i18n'

function day(v: string | null | undefined): string {
  return v ? new Date(v).toLocaleDateString(appLocale(), { year: 'numeric', month: 'short', day: 'numeric' }) : ''
}

// ACT recording.view @web.people
export function PersonRecordings({ email }: { email: string }) {
  const status = useSpeakrStatus()
  const q = usePersonRecordings(email, !!status.data?.connected)
  const items = q.data?.items ?? []
  if (!status.data?.connected || !items.length) return null
  const count = q.data?.count ?? items.length
  return (
    <section className="rec-person" aria-label={t('common.recordings')} data-testid="person-recordings">
      <h4>{t('speakr.personRecordings.recordings', { count })}</h4>
      <ul>
        {items.map((r) => (
          <li key={`${r.connector_id}:${r.recording_id}`}>
            <button type="button" className="rec-person-row" onClick={() => useRecordingSheet.getState().show(r.connector_id, r.recording_id)}>
              <Play size={13} aria-hidden className="rec-mark" />
              <span className="when">{day(r.meeting_date ?? r.created_at)}</span>
              <span className="ttl">{r.title || t('common.untitledRecording')}</span>
              <span className="muted">{fmtDuration(r.duration_s)}</span>
              {r.local_only && <Lock size={11} aria-label={t('speakr.personRecordings.onlyLocalModelsReadThis')} />}
            </button>
          </li>
        ))}
      </ul>
      {count > items.length && <p className="muted">{t('speakr.personRecordings.theMostRecentOf', { itemsLength: items.length, count })}</p>}
    </section>
  )
}
