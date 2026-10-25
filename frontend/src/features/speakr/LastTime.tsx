// "Last time" (event.last_recording): on an upcoming event and its reminder,
// the latest earlier recording of the same meeting (same series, or the same title with the same
// people) and the first line of its summary, which mailr fetches from Speakr when this is shown.
// A tap opens the recording sheet. Nothing shows without a Speakr account or such a recording.
// NextMeetingLastTime is the calendar panel's version: the next meeting today, with its "Last time".

import { History } from 'lucide-react'
import { useLastTime, useSpeakrStatus } from './api'
import { useRecordingSheet } from './store'
import './speakr.css'
import { t, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

// ACT event.last_recording @web.panel @web.sheet @web.calendar
export function LastTime({ uid, occurrence, onOpen }: { uid: string; occurrence?: string | null; onOpen?: () => void }) {
  const status = useSpeakrStatus()
  const q = useLastTime(uid, occurrence, !!status.data?.connected && !!uid)
  const r = q.data?.item
  if (!r) return null
  const when = r.meeting_date ?? r.created_at
  return (
    <div className="rec-last" data-testid="last-time">
      <History size={14} aria-hidden className="rec-mark" />
      <span><b>{t('speakr.lastTime.lastTime')}</b></span>
      <button type="button" className="btn text sm" style={{ padding: '0 4px', height: 'auto' }}
        onClick={() => { onOpen?.(); useRecordingSheet.getState().show(r.connector_id, r.recording_id) }}>
        {r.title || t('common.recording')}
      </button>
      {when && <span className="muted">{new Date(when).toLocaleDateString(appLocale(), { weekday: 'short', month: 'short', day: 'numeric' })}</span>}
      {r.summary_line && <span className="rec-last-line">{r.summary_line}</span>}
    </div>
  )
}

// ACT event.last_recording (the calendar panel, web.panel)
export function NextMeetingLastTime({ uid, occurrence, title, start }: { uid: string; occurrence?: string | null; title: string; start: Date }) {
  const status = useSpeakrStatus()
  const q = useLastTime(uid, occurrence, !!status.data?.connected && !!uid)
  if (!q.data?.item) return null
  return (
    <div className="cp-next" data-testid="next-last-time">
      <div className="cp-next-head">
        <span className="muted">{t('speakr.lastTime.next')}</span>
        <b>{title || t('common.noTitle')}</b>
        <span className="muted">{start.toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })}</span>
      </div>
      <LastTime uid={uid} occurrence={occurrence} />
    </div>
  )
}
