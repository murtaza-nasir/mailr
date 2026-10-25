// Recordings in mail.
// - RecordingMailCard (web.reader): above a Speakr notification email, the recording it points at:
//   title, length, the linked event, the summary's first line, Open, Follow-up email, Add tasks and
//   Open in Speakr; "Transcription failed" with Open in Speakr; "Processing" while Speakr works. mailr
//   recognises the email by its link alone (host and id), and everything shown comes from the mirror
//   and Speakr, never from the email.
// - TranscribeButton (web.reader): "Transcribe in Speakr" on an audio or video attachment.
// - RecordingChip (web.list): the ▶ chip on rows linked to a recording.
// - RecordingsGlance (web.list): the Recordings section above mail search results.

import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarDays, CheckSquare, ExternalLink, Lock, Mic, Play } from 'lucide-react'
import { useState } from 'react'
import type { Attachment, Message } from '../../api/types'
import { Spinner } from '../../components/bits'
import { toast, toastError } from '../../stores/ui'
import { FollowUpButtons } from './actions'
import { speakrKeys, transcribeAttachment, useLinkedConversations, useMailCard, useRecordingHits, useRecordingList, useSpeakrStatus, type Recording } from './api'
import { fmtDuration, fmtT, isMedia, recordingQuery } from './format'
import { useRecordingSheet } from './store'
import './speakr.css'
import { t, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

function whenOf(r: Recording): string {
  const v = r.meeting_date ?? r.created_at
  return v ? new Date(v).toLocaleString(appLocale(), { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() }) : ''
}

// ACT recording.view @web.reader, ACT recording.follow_up @web.reader, ACT recording.to_tasks @web.reader
export function RecordingMailCard({ m }: { m: Message }) {
  const status = useSpeakrStatus()
  const q = useMailCard(m.id, !!status.data?.connected)
  const items = q.data?.items ?? []
  if (!items.length) return null
  return <>{items.map((r) => <OneCard key={`${r.connector_id}:${r.recording_id}`} r={r} />)}</>
}

function OneCard({ r }: { r: Recording }) {
  const ev = (r.links ?? []).find((l) => l.kind === 'event')
  const evRef = ev ? { uid: ev.target, occurrence: ev.occurrence } : null
  const open = () => useRecordingSheet.getState().show(r.connector_id, r.recording_id)
  return (
    <section className="cal-rec rec-mail" aria-label={t('common.recording')} data-testid="recording-mail-card">
      <div className="cal-rec-head">
        {r.failed ? <AlertTriangle size={14} aria-hidden /> : <Play size={14} aria-hidden />}
        <span>{r.failed ? t('speakr.mailCard.transcriptionFailed') : r.processing ? t('speakr.mailCard.recordingProcessing') : t('common.recording')}</span>
        <span className="muted" style={{ fontWeight: 400 }}>{[r.title, whenOf(r), fmtDuration(r.duration_s)].filter(Boolean).join(' · ')}</span>
        {r.local_only && <span className="pill warn" data-tip={t('speakr.mailCard.onlyLocalModelsReadThis')}><Lock size={11} /></span>}
      </div>
      {ev && (
        <div className="rec-mail-ev muted">
          <CalendarDays size={13} aria-hidden /> {ev.state === 'suggested' ? t('speakr.mailCard.probably') : t('speakr.mailCard.linkedTo')} {ev.title || t('speakr.mailCard.anEvent')}
          {ev.start ? ` · ${new Date(ev.start).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' })}` : ''}
        </div>
      )}
      {r.summary_excerpt && !r.failed && <p className="cal-rec-excerpt">{r.summary_excerpt}</p>}
      <div className="cal-rec-actions">
        {!r.failed && (
          <>
            <button type="button" className="btn text sm" onClick={open}>{t('speakr.mailCard.openRecording')}</button>
            <FollowUpButtons cid={r.connector_id} rid={r.recording_id} ev={evRef} disabled={!r.has_summary} />
            <button type="button" className="btn text sm" disabled={!r.has_summary && !r.has_transcript}
              onClick={() => useRecordingSheet.getState().showTasks({ cid: r.connector_id, rid: r.recording_id, title: r.title, ev: evRef })}>
              <CheckSquare size={16} aria-hidden />{' '}{t('common.addTasks')}
            </button>
          </>
        )}
        {/* ACT recording.open_speakr @web.reader */}
        <a className="btn text sm" href={r.url} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden />{' '}{t('speakr.mailCard.openInSpeakr')}</a>
      </div>
    </section>
  )
}

// ACT mail.to_speakr @web.reader
export function TranscribeButton({ m, a }: { m: Message; a: Attachment }) {
  const status = useSpeakrStatus(isMedia(a))
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  if (!isMedia(a) || !status.data?.connected) return null
  const go = async () => {
    setBusy(true)
    try {
      const up = await transcribeAttachment(m.id, a.part)
      void qc.invalidateQueries({ queryKey: speakrKeys.mailCard(m.id) })
      void qc.invalidateQueries({ queryKey: speakrKeys.linked })
      toast({
        message: t('speakr.mailCard.sentToSpeakrItAppears', { upTitle: up.title }),
        actionLabel: t('speakr.mailCard.openInSpeakr'), onAction: () => window.open(up.url, '_blank', 'noopener,noreferrer'),
      })
    } catch (e) {
      toastError(e, t('speakr.mailCard.notSentToSpeakr'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <button type="button" className="icon-btn" onClick={() => void go()} disabled={busy}
      aria-label={t('speakr.mailCard.transcribeInSpeakr', { filename: a.filename })} data-tip={t('speakr.mailCard.transcribeInSpeakr2')}>
      {busy ? <Spinner small /> : <Mic />}
    </button>
  )
}

// ACT recording.search @web.list (the list chip)
export function RecordingChip({ id }: { id: number }) {
  const status = useSpeakrStatus()
  const linked = useLinkedConversations(!!status.data?.connected)
  if (!linked.data?.has(id)) return null
  return (
    <span className="chip-mini rec-chip" data-tip={t('speakr.mailCard.linkedToARecording')} aria-label={t('speakr.mailCard.linkedToARecording')} data-testid="recording-chip">
      <Play size={10} aria-hidden />
    </span>
  )
}

// ACT recording.search (the Recordings section of mail search). On a Speakr that searches its
// transcripts, the words said in recordings show too, each opening the recording at that moment.
export function RecordingsGlance({ q }: { q: string }) {
  const status = useSpeakrStatus()
  const rq = recordingQuery(q)
  const list = useRecordingList(rq.text, !!status.data?.connected && (!!rq.text || rq.on))
  const searchable = !!status.data?.connectors.some((c) => c.search)
  const hitsQ = useRecordingHits(rq.text, searchable && !!rq.text)
  const items = (list.data?.items ?? []).slice(0, 3)
  const shown = new Set(items.map((r) => `${r.connector_id}:${r.recording_id}`))
  const hits = (hitsQ.data?.hits ?? []).filter((h) => h.where !== 'title' && h.where !== 'participants').slice(0, 3)
  if (!items.length && !hits.length) return null
  const base = status.data?.connectors[0]?.base_url
  return (
    <section className="sg rec-glance" role="region" aria-label={t('common.recordings')}>
      <div className="sg-col">
        <div className="sg-head">
          <Play size={16} aria-hidden /> <b>{t('common.recordings')}</b>
          {base && <a className="btn text sm" href={base} target="_blank" rel="noreferrer">{t('speakr.mailCard.seeAllInSpeakr')}</a>}
        </div>
        {items.map((r) => (
          <button key={`${r.connector_id}:${r.recording_id}`} type="button" className="sg-row rec-glance-row"
            onClick={() => useRecordingSheet.getState().show(r.connector_id, r.recording_id)}>
            <Play size={14} aria-hidden className="rec-mark" />
            <span className="sg-title">{r.title || t('common.untitledRecording')}</span>
            <span className="sg-when">{[whenOf(r), r.participants.slice(0, 3).join(', ')].filter(Boolean).join(' · ')}</span>
          </button>
        ))}
        {hits.map((h, i) => (
          <button key={`hit-${h.connector_id}:${h.recording_id}:${h.t}:${i}`} type="button" className="sg-row rec-glance-row" data-testid="recording-hit"
            onClick={() => useRecordingSheet.getState().show(h.connector_id, h.recording_id, h.t)}>
            <Play size={14} aria-hidden className="rec-mark" />
            <span className="sg-title">“{h.text.length > 120 ? `${h.text.slice(0, 117)}…` : h.text}”</span>
            <span className="sg-when">{[shown.has(`${h.connector_id}:${h.recording_id}`) ? '' : h.title, h.speaker,
              h.t !== null ? fmtT(h.t) : h.where].filter(Boolean).join(' · ')}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
