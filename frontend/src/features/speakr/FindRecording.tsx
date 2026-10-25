// The event detail's Recording section when nothing is linked (web.calendar): "Find recording…"
// opens a short list of recordings from around the meeting's day, closest in time first, with a
// search by title, people and summary; Link links the chosen one to this occurrence (Undo in the
// toast). During the meeting, or just before it, "Record this meeting" opens the browser recorder
// with the event, its title and its guests filled in. Nothing shows for a meeting far in the future.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, Mic, Search } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { Spinner } from '../../components/bits'
import { personName } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { useRecorder } from '../recordings/recorderStore'
import { linkRecording, unlinkRecording, type Recording } from './api'
import { fmtDuration } from './format'
import { t, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export interface EventInfo {
  title: string
  start: Date
  end: Date
  guests?: string[]
}

// ACT recording.link @web.calendar, ACT recording.record @web.calendar
export function FindRecording({ uid, occurrence, ev }: { uid: string; occurrence: string | null | undefined; ev: EventInfo }) {
  const [open, setOpen] = useState(false)
  const now = Date.now()
  const started = ev.start.getTime() <= now + 10 * 60_000
  const recordable = ev.start.getTime() - 15 * 60_000 <= now && ev.end.getTime() + 5 * 60_000 >= now
  if (!started && !recordable) return null
  return (
    <section className="cal-rec cal-rec-find" aria-label={t('common.recording')} data-testid="cal-recording-find">
      <div className="cal-rec-actions">
        {recordable && (
          <button type="button" className="btn tonal sm" onClick={() => useRecorder.getState().open({
            title: ev.title, participants: (ev.guests ?? []).map(personName),
            event: { uid, occurrence: occurrence ?? '', summary: ev.title, start: ev.start.toISOString() },
          })}>
            <Mic size={16} aria-hidden />{' '}{t('speakr.findRecording.recordThisMeeting')}
          </button>
        )}
        {started && !open && (
          <button type="button" className="btn text sm" onClick={() => setOpen(true)}>
            <Search size={16} aria-hidden />{' '}{t('speakr.findRecording.findRecording')}
          </button>
        )}
      </div>
      {open && <Finder uid={uid} occurrence={occurrence} ev={ev} onDone={() => setOpen(false)} />}
    </section>
  )
}

function Finder({ uid, occurrence, ev, onDone }: { uid: string; occurrence: string | null | undefined; ev: EventInfo; onDone: () => void }) {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const from = new Date(ev.start.getTime() - 86_400_000)
  const to = new Date(ev.end.getTime() + 86_400_000)
  const list = useQuery({
    queryKey: ['speakr', 'find', uid, occurrence ?? '', q],
    // ACT recording.search @web.calendar
    queryFn: ({ signal }) => api.get<{ items: Recording[] }>('/speakr/recordings', q.trim() ? { q, limit: 12 } : { from: iso(from), to: iso(to), limit: 12 }, signal),
    staleTime: 30_000,
    retry: false,
  })
  const t0 = ev.start.getTime()
  const items = [...(list.data?.items ?? [])].sort((a, b) => {
    const d = (r: Recording) => Math.abs(new Date(r.meeting_date ?? r.created_at ?? 0).getTime() - t0)
    return d(a) - d(b)
  })
  const link = (r: Recording) =>
    linkRecording({ connector_id: r.connector_id, recording_id: r.recording_id, kind: 'event', target: uid, occurrence: occurrence ?? '' })
      .then((lk) => {
        void qc.invalidateQueries({ queryKey: ['speakr'] })
        onDone()
        toast({
          message: t('speakr.findRecording.linkedToThisMeeting', { title: r.title || t('common.recording') }),
          actionLabel: t('common.undo'),
          onAction: () => void unlinkRecording(lk.id).then(() => qc.invalidateQueries({ queryKey: ['speakr'] })),
        })
      })
      .catch((e) => toastError(e, t('common.notLinked')))
  return (
    <div className="cal-rec-finder" data-testid="find-recording">
      <label className="cal-rec-search">
        <Search size={14} aria-hidden />
        <input className="input sm" autoFocus placeholder={t('common.searchRecordings')} aria-label={t('common.searchRecordings')} value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onDone() } }} />
      </label>
      {list.isLoading ? (
        <div className="cal-rec-wait"><Spinner small /></div>
      ) : list.isError ? (
        <p className="muted">{(list.error as Error).message}</p>
      ) : !items.length ? (
        <p className="muted cal-rec-none">{q.trim() ? t('common.nothingMatches') : t('speakr.findRecording.noRecordingsFromAroundThat')}</p>
      ) : (
        <ul className="cal-rec-options">
          {items.map((r) => {
            const w = r.meeting_date ?? r.created_at
            return (
              <li key={`${r.connector_id}:${r.recording_id}`}>
                <span className="grow">
                  <b>{r.title || t('common.recording')}</b>
                  <span className="muted"> · {w ? new Date(w).toLocaleString(appLocale(), { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() }) : t('speakr.findRecording.noDate')}{r.duration_s ? ` · ${fmtDuration(r.duration_s)}` : ''}</span>
                </span>
                <button type="button" className="btn tonal sm" onClick={() => void link(r)}><Link2 size={14} aria-hidden />{' '}{t('common.link')}</button>
              </li>
            )
          })}
        </ul>
      )}
      <button type="button" className="btn text sm" onClick={onDone}>{t('common.cancel')}</button>
    </div>
  )
}
