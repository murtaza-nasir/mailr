// "New recording" (recording.record, web.recordings): record a meeting in the browser and send it
// to Speakr through mailr. The microphone is asked for when you press Record. While it records you
// see the time and the level, can pause and resume, and can write notes (Markdown); the title,
// participants (People suggest names), meeting date, folder, tags and the calendar event to link
// to can be set before, during or after. Stop, then Upload: the progress shows, and the recording
// opens in its sheet when mailr answers. Closing the dialog keeps the recording going in a small
// bar at the bottom of the window (RecorderHost); a tab closed in the middle leaves the audio in
// IndexedDB, and the Recordings app offers it for upload.

import { useQueryClient } from '@tanstack/react-query'
import { CalendarDays, Check, Mic, Pause, Play, Square, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BlockedImage } from '../../lib/markdownImages'
import { useLocation, useNavigate } from 'react-router'
import { usePeople } from '../../api/hooks'
import { Avatar } from '../../components/bits'
import { personName } from '../../lib/format'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { toast, toastError } from '../../stores/ui'
import { recordingPath } from '../speakr/format'
import { useSpeakrStatus } from '../speakr/api'
import { useRecordingSheet } from '../speakr/store'
import { currentMeeting, recKeys, useMeetingsAround, useSpeakrFolders, useSpeakrTags } from './api'
import * as recorder from './recorder'
import { clock, useRecorder } from './recorderStore'
import './recordings.css'
import { t as tr, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'
import { rehypeDirAuto } from '../../lib/rehypeDirAuto'

const fmtEvent = (e: { summary: string; start: string }) => {
  const d = new Date(e.start)
  return `${e.summary || tr('common.noTitle')} · ${d.toLocaleDateString(appLocale(), { weekday: 'short', month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })}`
}

const toLocalInput = (ms: number) => {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/** The level meter: a row of bars that fill with the microphone's level. */
export function LevelMeter({ level, bars = 24, live }: { level: number; bars?: number; live: boolean }) {
  const lit = Math.round(level * bars)
  return (
    <div className={`rx-meter${live ? ' live' : ''}`} role="meter" aria-label={tr('recordings.newRecording.microphoneLevel')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(level * 100)}>
      {Array.from({ length: bars }, (_, i) => (
        <span key={i} className={i < lit ? (i >= bars * 0.85 ? 'on hot' : 'on') : ''} style={{ height: `${30 + 70 * Math.sin((i / bars) * Math.PI)}%` }} />
      ))}
    </div>
  )
}

// ACT recording.record @web.recordings
export default function NewRecording() {
  const s = useRecorder()
  const form = s.form
  const qc = useQueryClient()
  const nav = useNavigate()
  const loc = useLocation()
  const status = useSpeakrStatus()
  const on = !!status.data?.connected
  const folders = useSpeakrFolders(on && s.dialog)
  const tags = useSpeakrTags(on && s.dialog)
  const meetings = useMeetingsAround(s.dialog)
  const [notesView, setNotesView] = useState<'write' | 'preview'>('write')
  const [discarding, setDiscarding] = useState(false)
  const abortRef = useRef<(() => void) | null>(null)
  const recording = s.phase === 'recording' || s.phase === 'paused'
  const busy = s.phase === 'uploading'

  // The meeting going on now (or about to start) is the default link, once, on a fresh form.
  const prefilled = useRef(false)
  useEffect(() => {
    if (prefilled.current || !meetings.data || s.phase !== 'idle' || form.event || form.title) return
    prefilled.current = true
    const ev = currentMeeting(meetings.data)
    if (ev) pickEvent(ev.uid + '|' + (ev.occurrence ?? ''))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetings.data])

  // The form goes with the audio in IndexedDB, so a recovered recording keeps it.
  useEffect(() => {
    const t = window.setTimeout(() => recorder.saveForm(form), 400)
    return () => window.clearTimeout(t)
  }, [form])

  const pickEvent = (key: string) => {
    const ev = (meetings.data ?? []).find((e) => `${e.uid}|${e.occurrence ?? ''}` === key)
    if (!ev) return s.setForm({ event: null })
    const names = (ev.attendee_list ?? []).map((a) => personName(a.name || '') || a.email).filter(Boolean)
    s.setForm({
      event: { uid: ev.uid, occurrence: ev.occurrence ?? '', summary: ev.summary, start: ev.start },
      ...(useRecorder.getState().form.title ? {} : { title: ev.summary }),
      ...(useRecorder.getState().form.participants.length || !names.length ? {} : { participants: names }),
    })
  }

  const close = () => s.set({ dialog: false })

  const doUpload = async () => {
    const blob = s.blob
    if (!blob) return
    s.set({ phase: 'uploading', progress: 0, error: null })
    const { promise, abort } = recorder.upload(blob, form, s.startedAt)
    abortRef.current = abort
    try {
      const out = await promise
      await recorder.finish()
      void qc.invalidateQueries({ queryKey: ['speakr'] })
      void qc.invalidateQueries({ queryKey: recKeys.all })
      const missing = out.unsupported_fields?.length ? tr('recordings.newRecording.speakrDidNotTake', { unsupportedFields: out.unsupported_fields.join(', ') }) : ''
      toast({ message: tr('recordings.newRecording.uploadedSpeakrIsTranscribingIt', { outTitle: out.title || tr('common.recording'), missing }) })
      useRecorder.getState().set({ dialog: false })
      if (loc.pathname.startsWith('/recordings')) nav(recordingPath(out.connector_id, out.recording_id))
      else useRecordingSheet.getState().show(out.connector_id, out.recording_id)
    } catch (e) {
      useRecorder.getState().set({ phase: 'stopped', progress: null, error: e instanceof Error ? e.message : String(e) })
      if (!(e instanceof Error && (e.message === tr('recordings.recorder.uploadCancelled') || e.message === tr('api.client.uploadCancelled')))) toastError(e, tr('recordings.newRecording.notUploaded'))
    } finally {
      abortRef.current = null
    }
  }

  const title = s.recovered ? tr('recordings.newRecording.uploadAKeptRecording') : recording ? (s.phase === 'paused' ? tr('recordings.newRecording.recordingPaused') : tr('recordings.newRecording.recording')) : tr('common.newRecording')
  const footer = (
    <>
      {(s.phase === 'stopped' || recording) && (
        <button type="button" className="btn text rx-danger" onClick={() => setDiscarding(true)} disabled={busy}>
          <Trash2 size={16} aria-hidden />{' '}{tr('common.discard')}
        </button>
      )}
      <span className="spacer" />
      {busy ? (
        <button type="button" className="btn text" onClick={() => abortRef.current?.()}>{tr('recordings.newRecording.cancelUpload')}</button>
      ) : (
        <button type="button" className="btn text" onClick={close}>{recording ? tr('recordings.newRecording.minimise') : s.phase === 'stopped' ? tr('recordings.newRecording.later') : tr('common.cancel')}</button>
      )}
      <button type="button" className="btn filled" data-testid="rx-upload" disabled={s.phase !== 'stopped' || !s.blob || !on} onClick={() => void doUpload()}>
        <Upload size={16} aria-hidden /> {busy ? tr('recordings.newRecording.uploading') : tr('recordings.newRecording.uploadToSpeakr')}
      </button>
    </>
  )

  return (
    <>
      <Dialog open={s.dialog} onClose={close} title={title} footer={footer} wide modal initialFocus=".rx-big">
        <div className="rx-new" data-testid="new-recording">
          <section className={`rx-deck ${s.phase}`} aria-label={tr('recordings.newRecording.recorder')}>
            <div className="rx-deck-main">
              {s.phase === 'idle' || s.phase === 'asking' ? (
                <button type="button" className="rx-big" onClick={() => void recorder.start()} disabled={s.phase === 'asking' || !recorder.supported()} aria-label={tr('recordings.newRecording.record')}>
                  <Mic size={28} aria-hidden />
                </button>
              ) : recording ? (
                <button type="button" className="rx-big stop" onClick={() => void recorder.stop()} aria-label={tr('common.stop')}>
                  <Square size={24} aria-hidden fill="currentColor" />
                </button>
              ) : (
                <span className="rx-big done" aria-hidden><Check size={28} /></span>
              )}
              <div className="rx-deck-info">
                <div className="rx-clock" aria-live="off" data-testid="rx-clock">{clock(s.elapsedMs)}</div>
                <div className="rx-state" role="status">
                  {s.phase === 'idle' && (recorder.supported() ? tr('recordings.newRecording.pressRecordTheBrowserAsks') : tr('recordings.newRecording.thisBrowserCannotRecordAudio'))}
                  {s.phase === 'asking' && tr('recordings.newRecording.waitingForTheMicrophone')}
                  {s.phase === 'recording' && <><span className="rx-dot" aria-hidden />{' '}{tr('recordings.newRecording.recordingTheAudioIsKept')}</>}
                  {s.phase === 'paused' && tr('recordings.newRecording.paused')}
                  {s.phase === 'stopped' && (s.recovered ? tr('recordings.newRecording.keptFromReadyToUpload', { value: new Date(s.startedAt ?? Date.now()).toLocaleString(appLocale(), { weekday: 'short', hour: 'numeric', minute: '2-digit', ...clockOpts() }) }) : tr('recordings.newRecording.stoppedReadyToUpload'))}
                  {s.phase === 'uploading' && tr('recordings.newRecording.uploading2', { round: Math.round((s.progress ?? 0) * 100) })}
                </div>
              </div>
              {recording && (
                s.phase === 'paused' ? (
                  <button type="button" className="btn tonal" onClick={() => recorder.resume()}><Play size={16} aria-hidden />{' '}{tr('common.resume')}</button>
                ) : (
                  <button type="button" className="btn tonal" onClick={() => recorder.pause()}><Pause size={16} aria-hidden />{' '}{tr('common.pause')}</button>
                )
              )}
            </div>
            {(recording || s.phase === 'asking') && <LevelMeter level={s.level} live={s.phase === 'recording'} />}
            {s.phase === 'stopped' && s.blob && <AudioPreview blob={s.blob} />}
            {busy && (
              <div className="rx-progress" role="progressbar" aria-label={tr('recordings.newRecording.upload')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((s.progress ?? 0) * 100)}>
                <span style={{ width: `${Math.round((s.progress ?? 0) * 100)}%` }} />
              </div>
            )}
            {s.error && <p className="error-text rx-error" role="alert">{s.error}</p>}
            {!on && status.isFetched && <p className="error-text rx-error" role="alert">{tr('recordings.newRecording.noSpeakrAccountIsConnected')}</p>}
          </section>

          <div className="rx-fields">
            <div className="field">
              <label htmlFor="rx-title">{tr('common.title')}</label>
              <input id="rx-title" className="input" value={form.title} placeholder={tr('recordings.newRecording.weeklySync')} maxLength={500} onChange={(e) => s.setForm({ title: e.target.value })} disabled={busy} />
            </div>
            <div className="field">
              <label htmlFor="rx-event"><CalendarDays size={14} aria-hidden />{' '}{tr('recordings.newRecording.linkToEvent')}</label>
              <select id="rx-event" className="select" value={form.event ? `${form.event.uid}|${form.event.occurrence}` : ''} onChange={(e) => pickEvent(e.target.value)} disabled={busy}>
                <option value="">{tr('recordings.newRecording.noEvent')}</option>
                {form.event && !(meetings.data ?? []).some((e) => e.uid === form.event!.uid) && (
                  <option value={`${form.event.uid}|${form.event.occurrence}`}>{fmtEvent(form.event)}</option>
                )}
                {(meetings.data ?? []).map((e) => (
                  <option key={`${e.uid}|${e.occurrence ?? ''}`} value={`${e.uid}|${e.occurrence ?? ''}`}>{fmtEvent(e)}</option>
                ))}
              </select>
            </div>
            <ParticipantsField value={form.participants} onChange={(participants) => s.setForm({ participants })} disabled={busy} />
            <div className="rx-row2">
              <div className="field">
                <label htmlFor="rx-when">{tr('recordings.newRecording.meetingDate')}</label>
                <input id="rx-when" className="input" type="datetime-local" value={form.meetingDate || (s.startedAt ? toLocalInput(s.startedAt) : '')}
                  onChange={(e) => s.setForm({ meetingDate: e.target.value })} disabled={busy} />
              </div>
              <div className="field">
                <label htmlFor="rx-folder">{tr('recordings.newRecording.folder')}</label>
                <select id="rx-folder" className="select" value={form.folder} onChange={(e) => s.setForm({ folder: e.target.value })} disabled={busy || !folders.data?.folders.length}>
                  <option value="">{folders.data && !folders.data.folders.length ? tr('recordings.newRecording.noFoldersInSpeakr') : tr('recordings.newRecording.noFolder')}</option>
                  {(folders.data?.folders ?? []).map((f) => <option key={f.id} value={String(f.id)}>{f.name}</option>)}
                </select>
              </div>
            </div>
            {(tags.data?.tags.length ?? 0) > 0 && (
              <div className="field">
                <span className="rx-label" id="rx-tags-label">{tr('common.tags')}</span>
                <div className="rx-tagpick" role="group" aria-labelledby="rx-tags-label">
                  {tags.data!.tags.map((t) => {
                    const id = String(t.id)
                    const onTag = form.tags.includes(id)
                    return (
                      <button key={t.id} type="button" className={`chip rx-tagchip${onTag ? ' on' : ''}`} aria-pressed={onTag} disabled={busy}
                        style={t.color ? ({ '--tag': t.color } as React.CSSProperties) : undefined}
                        onClick={() => s.setForm({ tags: onTag ? form.tags.filter((x) => x !== id) : [...form.tags, id] })}>
                        <span className="rx-tagdot" aria-hidden />{t.name}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
            <div className="field rx-notes">
              <div className="rx-notes-head">
                <label htmlFor="rx-notes">{tr('common.notes')}</label>
                <span className="muted">{tr('recordings.newRecording.markdown')}</span>
                <span className="spacer" />
                <div className="rx-seg" role="tablist" aria-label={tr('recordings.newRecording.notesView')}>
                  <button type="button" role="tab" aria-selected={notesView === 'write'} className={notesView === 'write' ? 'on' : ''} onClick={() => setNotesView('write')}>{tr('recordings.newRecording.write')}</button>
                  <button type="button" role="tab" aria-selected={notesView === 'preview'} className={notesView === 'preview' ? 'on' : ''} onClick={() => setNotesView('preview')}>{tr('common.preview')}</button>
                </div>
              </div>
              {notesView === 'write' ? (
                <textarea id="rx-notes" className="input" rows={6} value={form.notes} maxLength={20000} disabled={busy}
                  placeholder={tr('recordings.newRecording.decisionsFollowUpsForDana')} onChange={(e) => s.setForm({ notes: e.target.value })} />
              ) : (
                <div className="rx-notes-preview rec-md">{form.notes.trim() ? <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeDirAuto]} skipHtml components={{ img: BlockedImage }}>{form.notes}</ReactMarkdown> : <p className="muted">{tr('recordings.newRecording.nothingWrittenYet')}</p>}</div>
              )}
            </div>
          </div>
        </div>
      </Dialog>
      <ConfirmDialog open={discarding} title={tr('recordings.newRecording.discardThisRecording')} danger confirmLabel={tr('common.discard')}
        body={tr('recordings.newRecording.theAudioAndTheNotes')}
        onClose={() => setDiscarding(false)}
        onConfirm={() => {
          setDiscarding(false)
          void recorder.discard()
        }} />
    </>
  )
}

function AudioPreview({ blob }: { blob: Blob }) {
  const url = useMemo(() => URL.createObjectURL(blob), [blob])
  useEffect(() => () => URL.revokeObjectURL(url), [url])
  return (
    <div className="rx-preview">
      <audio controls src={url} preload="metadata" aria-label={tr('recordings.newRecording.listenToTheRecording')} />
      <span className="muted">{tr('recordings.newRecording.mb', { value: (blob.size / 1_048_576).toFixed(1) })}</span>
    </div>
  )
}

/** Names, with People suggesting as you type (Enter or a comma adds what you typed). */
function ParticipantsField({ value, onChange, disabled }: { value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const people = usePeople(text.trim().length >= 2 ? text.trim() : '')
  const label = (p: { name: string; email: string }) => personName(p.name) || p.email
  const options = (people.data ?? []).filter((p) => !value.some((v) => v.toLowerCase() === label(p).toLowerCase())).slice(0, 6)
  const add = (name: string) => {
    const n = name.trim().replace(/,$/, '').trim()
    if (n && !value.some((v) => v.toLowerCase() === n.toLowerCase())) onChange([...value, n])
    setText('')
    setHi(0)
  }
  return (
    <div className="field rx-people">
      <label htmlFor="rx-people">{tr('common.participants')}</label>
      <div className="rx-chips-input">
        {value.map((v) => (
          <span key={v} className="chip rx-person">
            <Avatar who={{ name: v, email: '' }} size="xs" />
            {v}
            <button type="button" className="rx-chip-x" aria-label={tr('recordings.newRecording.remove', { v })} onClick={() => onChange(value.filter((x) => x !== v))} disabled={disabled}><X size={12} /></button>
          </span>
        ))}
        <input id="rx-people" className="rx-bare" value={text} disabled={disabled} placeholder={value.length ? '' : tr('recordings.newRecording.typeAName')} autoComplete="off"
          role="combobox" aria-expanded={open && options.length > 0} aria-controls="rx-people-list" aria-autocomplete="list"
          onFocus={() => setOpen(true)} onBlur={() => window.setTimeout(() => setOpen(false), 150)}
          onChange={(e) => { setText(e.target.value); setOpen(true); setHi(0); if (e.target.value.endsWith(',')) add(e.target.value) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' && options.length) { e.preventDefault(); setHi((hi + 1) % options.length) }
            else if (e.key === 'ArrowUp' && options.length) { e.preventDefault(); setHi((hi - 1 + options.length) % options.length) }
            else if (e.key === 'Enter') {
              e.preventDefault()
              if (open && options[hi]) add(label(options[hi]))
              else if (text.trim()) add(text)
            } else if (e.key === 'Backspace' && !text && value.length) onChange(value.slice(0, -1))
          }} />
      </div>
      {open && options.length > 0 && (
        <ul className="rx-suggest" id="rx-people-list" role="listbox" aria-label={tr('common.people')}>
          {options.map((p, i) => (
            <li key={p.email} role="option" aria-selected={i === hi} className={i === hi ? 'hi' : ''} onMouseDown={(e) => { e.preventDefault(); add(label(p)) }}>
              <Avatar who={{ name: p.name, email: p.email }} size="xs" />
              <span>{label(p)}</span>
              {p.name && <span className="muted">{p.email}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** The bar a minimised recording shows at the bottom of the window. */
export function RecorderBar() {
  const s = useRecorder()
  const live = s.phase === 'recording' || s.phase === 'paused'
  if (s.dialog || (!live && s.phase !== 'stopped' && s.phase !== 'uploading')) return null
  return (
    <div className={`rx-bar ${s.phase}`} role="region" aria-label={tr('recordings.newRecording.recordingInProgress')} data-testid="recorder-bar">
      {live ? <span className={`rx-dot${s.phase === 'paused' ? ' paused' : ''}`} aria-hidden /> : <Check size={16} aria-hidden />}
      <span className="rx-bar-clock">{clock(s.elapsedMs)}</span>
      <span className="rx-bar-title">{s.form.title || (live ? tr('common.recording') : tr('recordings.newRecording.recordingReadyToUpload'))}</span>
      {live && <LevelMeter level={s.level} bars={8} live={s.phase === 'recording'} />}
      {s.phase === 'recording' && <button type="button" className="icon-btn sm" aria-label={tr('common.pause')} data-tip={tr('common.pause')} onClick={() => recorder.pause()}><Pause size={16} /></button>}
      {s.phase === 'paused' && <button type="button" className="icon-btn sm" aria-label={tr('common.resume')} data-tip={tr('common.resume')} onClick={() => recorder.resume()}><Play size={16} /></button>}
      {live && <button type="button" className="icon-btn sm" aria-label={tr('common.stop')} data-tip={tr('common.stop')} onClick={() => { void recorder.stop().then(() => s.set({ dialog: true })) }}><Square size={14} fill="currentColor" /></button>}
      <button type="button" className="btn text sm" onClick={() => s.set({ dialog: true })}>{live ? tr('common.open') : tr('recordings.newRecording.upload2')}</button>
    </div>
  )
}
