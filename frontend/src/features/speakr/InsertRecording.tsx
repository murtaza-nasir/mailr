// "Insert → From a recording…" in the compose toolbar (recording.insert):
// pick a recent recording (or search), then insert its summary, or tick transcript turns and
// insert them as a quoted excerpt with speaker names and times. It is saved with the draft like
// any typing. Shown only when the user has a Speakr account.
// ACT recording.insert @web.editor

import type { Editor } from '@tiptap/react'
import { ArrowLeft, Mic } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { Spinner } from '../../components/bits'
import { Popover, useAnchor } from '../../components/Popover'
import { toastError } from '../../stores/ui'
import { useRecordingList, useSpeakrStatus, useTranscript, type Recording } from './api'
import { excerptHtml, fmtT, markdownHtml } from './format'
import './speakr.css'
import { t, appLocale } from '../../i18n'

export function InsertRecordingButton({ editor }: { editor: Editor | null }) {
  const status = useSpeakrStatus()
  const menu = useAnchor()
  if (!status.data?.connected) return null
  const insert = (html: string) => {
    menu.close()
    editor?.chain().focus().insertContent(html).run()
  }
  return (
    <>
      <button type="button" className={`icon-btn${menu.open ? ' active' : ''}`} aria-label={t('speakr.insertRecording.insertFromARecording')} data-tip={t('speakr.insertRecording.insertFromARecording')}
        aria-haspopup="dialog" onMouseDown={(e) => e.preventDefault()} onClick={(e) => menu.toggle(e.currentTarget)}>
        <Mic />
      </button>
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="top-start" role="dialog" label={t('speakr.insertRecording.insertFromARecording')} minWidth={340} className="ir-pop">
        {menu.open && <Picker onInsert={insert} />}
      </Popover>
    </>
  )
}

function Picker({ onInsert }: { onInsert: (html: string) => void }) {
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<Recording | null>(null)
  const list = useRecordingList(q.trim(), !picked)
  if (picked) return <Parts r={picked} onBack={() => setPicked(null)} onInsert={onInsert} />
  return (
    <>
      <b>{t('speakr.insertRecording.insertFromARecording')}</b>
      <input className="input" placeholder={t('common.searchRecordings')} aria-label={t('common.searchRecordings')} value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
      {list.isLoading ? <Spinner small /> : (
        <ul className="ir-list" aria-label={t('common.recordings')}>
          {(list.data?.items ?? []).map((r) => (
            <li key={`${r.connector_id}:${r.recording_id}`}>
              <button type="button" onClick={() => setPicked(r)}>
                <div>{r.title || t('common.recording')}</div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {r.meeting_date ? new Date(r.meeting_date).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' }) : ''}
                  {r.participants.length ? ` · ${r.participants.slice(0, 3).join(', ')}` : ''}
                </div>
              </button>
            </li>
          ))}
          {!list.data?.items?.length && <li className="muted">{t('speakr.insertRecording.noRecordingsFound')}</li>}
        </ul>
      )}
    </>
  )
}

function Parts({ r, onBack, onInsert }: { r: Recording; onBack: () => void; onInsert: (html: string) => void }) {
  const [mode, setMode] = useState<'choose' | 'excerpt'>('choose')
  const [busy, setBusy] = useState(false)
  const [on, setOn] = useState<Set<number>>(new Set())
  const tq = useTranscript(r.connector_id, r.recording_id, mode === 'excerpt')
  const segs = tq.data?.segments ?? []
  const summary = async () => {
    setBusy(true)
    try {
      const s = await api.get<{ summary: string }>(`/speakr/recordings/${r.connector_id}/${r.recording_id}/summary`)
      onInsert(`<p><i>From the recording “${(r.title || t('common.recording')).replace(/</g, '&lt;')}”</i></p>${markdownHtml(s.summary)}`)
    } catch (e) {
      toastError(e, t('speakr.insertRecording.noSummary'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <div className="row" style={{ gap: 6 }}>
        <button type="button" className="icon-btn sm" aria-label={t('common.back')} onClick={onBack}><ArrowLeft size={16} /></button>
        <b className="ellipsis">{r.title || t('common.recording')}</b>
      </div>
      {mode === 'choose' ? (
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <button type="button" className="btn tonal sm" disabled={!r.has_summary || busy} onClick={() => void summary()}>{t('speakr.insertRecording.insertTheSummary')}</button>
          <button type="button" className="btn text sm" disabled={!r.has_transcript} onClick={() => setMode('excerpt')}>{t('speakr.insertRecording.chooseTranscriptTurns')}</button>
        </div>
      ) : tq.isLoading ? <Spinner small /> : (
        <>
          <div className="ir-turns" role="group" aria-label={t('speakr.insertRecording.transcriptTurns')}>
            {segs.map((s, i) => (
              <label key={i}>
                <input type="checkbox" className="checkbox" checked={on.has(i)}
                  onChange={(e) => setOn((o) => { const n = new Set(o); if (e.target.checked) n.add(i); else n.delete(i); return n })} />
                <span><span className="muted">{fmtT(s.t)}</span> {s.speaker && <b>{s.speaker}:</b>} {s.text}</span>
              </label>
            ))}
          </div>
          <button type="button" className="btn filled sm" disabled={!on.size}
            onClick={() => onInsert(excerptHtml(r.title || t('common.recording'), segs.filter((_, i) => on.has(i))))}>
            {t('speakr.insertRecording.insertTurns', { count: on.size, onSize: on.size || '' })}
          </button>
        </>
      )}
    </>
  )
}
