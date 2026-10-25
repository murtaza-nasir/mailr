// The event detail's Recording section (web.calendar): the recording linked to
// this occurrence, or the suggested one ("Is this the recording of this meeting?
// Yes / No"), with the summary's first lines, Open recording, Follow-up email and Add tasks.
// Above it, for an upcoming meeting, "Last time" (LastTime.tsx). With nothing linked, a meeting
// that has started offers "Find recording…" and one going on now "Record this meeting"
// (FindRecording.tsx); nothing shows without a Speakr account.

import { useQueryClient } from '@tanstack/react-query'
import { CheckSquare, Play } from 'lucide-react'
import { toast, toastError } from '../../stores/ui'
import { FollowUpButtons } from './actions'
import { linkRecording, unlinkRecording, useEventRecordings, useSpeakrStatus, type Recording } from './api'
import { FindRecording, type EventInfo } from './FindRecording'
import { fmtDuration } from './format'
import { LastTime } from './LastTime'
import { useRecordingSheet } from './store'
import './speakr.css'
import { t, tx } from '../../i18n'

// ACT recording.view @web.calendar, ACT recording.link @web.calendar, ACT recording.unlink @web.calendar, ACT recording.follow_up @web.calendar, ACT recording.to_tasks @web.calendar,
// ACT event.last_recording @web.calendar
export function EventRecordings({ uid, occurrence, onOpen, ev }: { uid: string; occurrence: string | null | undefined; onOpen?: () => void; ev?: EventInfo }) {
  const status = useSpeakrStatus()
  const q = useEventRecordings(uid, occurrence, !!status.data?.connected)
  const items = q.data?.items ?? []
  if (!status.data?.connected) return null
  return (
    <>
      {/* ACT event.last_recording: an upcoming meeting shows the recording of the last one. */}
      <LastTime uid={uid} occurrence={occurrence} onOpen={onOpen} />
      {items.map((r) => <One key={`${r.connector_id}:${r.recording_id}`} r={r} uid={uid} occurrence={occurrence} onOpen={onOpen} />)}
      {ev && q.isSuccess && !items.length && <FindRecording uid={uid} occurrence={occurrence} ev={ev} />}
    </>
  )
}

function One({ r, uid, occurrence, onOpen }: { r: Recording; uid: string; occurrence: string | null | undefined; onOpen?: () => void }) {
  const qc = useQueryClient()
  const link = r.link!
  const refresh = () => void qc.invalidateQueries({ queryKey: ['speakr'] })
  const yes = () =>
    linkRecording({ connector_id: r.connector_id, recording_id: r.recording_id, kind: 'event', target: uid, occurrence: link.occurrence ?? occurrence ?? '' })
      .then(() => { refresh(); toast({ message: t('speakr.eventRecordings.recordingLinkedToThisMeeting') }) })
      .catch((e) => toastError(e))
  const no = () =>
    unlinkRecording(link.id)
      .then(() => { refresh(); toast({ message: t('speakr.eventRecordings.suggestionDismissed'), actionLabel: t('common.undo'), onAction: () => void yes() }) })
      .catch((e) => toastError(e))
  const ev = { uid, occurrence: link.occurrence ?? occurrence ?? null }
  return (
    <section className="cal-rec" aria-label={t('common.recording')} data-testid="cal-recording">
      <div className="cal-rec-head">
        <Play size={14} aria-hidden />{' '}{tx('speakr.eventRecordings.recording', { filter: [r.title, fmtDuration(r.duration_s)].filter(Boolean).join(' · ') }, { span: (c) => <span className="muted" style={{ fontWeight: 400 }}>{c}</span> })}
      </div>
      {link.state === 'suggested' && (
        <div className="cal-rec-ask" role="group" aria-label={t('speakr.eventRecordings.isThisTheRecordingOf')}>
          <span>{t('speakr.eventRecordings.isThisTheRecordingOf')}</span>
          <button type="button" className="btn tonal sm" onClick={() => void yes()}>{t('common.yes')}</button>
          <button type="button" className="btn text sm" onClick={() => void no()}>{t('common.no')}</button>
        </div>
      )}
      {r.summary_excerpt && <p className="cal-rec-excerpt">{r.summary_excerpt.replace(/^#+\s*/gm, '').replace(/\*\*/g, '')}</p>}
      <div className="cal-rec-actions">
        <button type="button" className="btn text sm" onClick={() => { onOpen?.(); useRecordingSheet.getState().show(r.connector_id, r.recording_id) }}>
          {t('speakr.eventRecordings.openRecording')}
        </button>
        <FollowUpButtons cid={r.connector_id} rid={r.recording_id} ev={ev} disabled={!r.has_summary} />
        <button type="button" className="btn text sm" onClick={() => {
          onOpen?.()
          useRecordingSheet.getState().showTasks({ cid: r.connector_id, rid: r.recording_id, title: r.title, ev })
        }}><CheckSquare size={16} aria-hidden />{' '}{t('common.addTasks')}</button>
      </div>
    </section>
  )
}
