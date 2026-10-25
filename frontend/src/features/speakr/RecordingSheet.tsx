// The recording sheet (web.recording): a read-only view of one Speakr recording.
// Header: title, meeting date and length, participants, tags, folder, status, and Open in Speakr.
// Tabs: Summary (Speakr's Markdown, no raw HTML), Transcript (speaker turns with times; a time
// opens Speakr there; the turn at the cited moment is marked), Notes, Events (meetings Speakr found
// in the recording, each offered to the calendar through the event editor), People (speakers mapped
// to people), Links (the events, conversations and tasks it is linked to, suggestions to confirm or
// dismiss, events to link, linked notes and tables, and Make a note from this recording).
// Actions: Follow-up email, Add tasks and Ask assistant (the panel opens with the recording
// attached; the sheet closes so the panel shows); Edit (title, participants, tags, a line added to
// the notes) and Share link (a public Speakr link, after a confirmation that says so) when the
// connector card allows them; Public links lists the recording's links and revokes them when
// Speakr has v1 share links. A recording another user shared shows whose it is. Content loads
// when the sheet opens, through mailr.
//
// RecordingSheetHost is the side sheet over any app (citation chips, the event detail). It loads
// the first time a recording or its action items open (SheetSlot.tsx, beside the recorder). In the Recordings app
// (features/recordings, /recordings/<cid>/<rid>?t=<s>) the same view sits where the theme puts a
// detail.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { useQueryClient } from '@tanstack/react-query'
import { Bot, CalendarDays, CalendarPlus, CheckSquare, Copy, ExternalLink, Link2, Lock, Mail, Pencil, Play, Search, StickyNote, X } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { BlockedImage } from '../../lib/markdownImages'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router'
import { Skeleton, Spinner } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { ScrollTabs } from '../../components/ScrollTabs'
import { calendarPath, eventPath, notePath, taskPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { toast, toastError } from '../../stores/ui'
import { ActionItemsDialog, FollowUpButtons, type EventRef } from './actions'
import { editRecording, linkRecording, revokeShareLink, shareRecording, speakrKeys, unlinkRecording, useRecording, useRecordingEvents, useShareLinks, useTranscript, type Recording, type RecordingLink, type Segment, type ShareOut } from './api'
import { extractedEventSeed, fmtDuration, fmtT, speakrAt } from './format'
import { useCalUI } from '../calendar/store'
import { openAsk, useAskTarget } from '../ask/askStore'
import { useRecordingSheet } from './store'
import { SpeakerPeople } from './SpeakerPeople'
import { noteFromRecording } from '../notes/actions'
import { LinkedPanel, refreshLinked } from '../notes/LinkedPanel'
import './speakr.css'
import { t as tr, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

// react-markdown loads when a summary or notes first show (GfmText.tsx).
const GfmText = lazy(() => import('./GfmText'))

type Tab = 'summary' | 'transcript' | 'notes' | 'events' | 'people' | 'links'

function when(r: Recording): string {
  const v = r.meeting_date ?? r.created_at
  if (!v) return ''
  return new Date(v).toLocaleString(appLocale(), { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() })
}

/** The event a recording is linked to (confirmed first), for follow-ups and tasks. */
export function eventOf(r: Recording | undefined): EventRef | null {
  const ev = (r?.links ?? []).filter((l) => l.kind === 'event').sort((a, b) => (a.state === 'confirmed' ? -1 : 0) - (b.state === 'confirmed' ? -1 : 0))[0]
  return ev ? { uid: ev.target, occurrence: ev.occurrence } : null
}

// ACT recording.view @web.recording @web.recordings, ACT recording.open_speakr @web.recording
export function RecordingView({ cid, rid, t, onClose, page }: { cid: number; rid: number; t: number | null; onClose?: () => void; page?: boolean }) {
  const q = useRecording(cid, rid)
  const [tab, setTab] = useState<Tab>(t !== null ? 'transcript' : 'summary')
  useEffect(() => setTab(t !== null ? 'transcript' : 'summary'), [cid, rid, t])
  const r = q.data
  const title = r?.title || tr('common.recording')
  const ev = eventOf(r)
  useAskTarget({ kind: 'recording', cid, rid, title })
  const [editing, setEditing] = useState(false)
  const [sharing, setSharing] = useState(false)
  const [links, setLinks] = useState(false)
  const tabs: [Tab, string][] = [['summary', tr('common.summary')], ['transcript', tr('speakr.recordingSheet.transcript')], ['notes', tr('common.notes')], ['events', tr('common.events')], ['people', tr('common.people')], ['links', tr('common.links')]]
  return (
    <div className={`rec-view${page ? ' page' : ''}`} data-testid="recording-sheet">
      <div className="rec-head">
        <div className="rec-title-row">
          <Play size={18} aria-hidden className="rec-mark" />
          <h2 id="rec-title">{q.isLoading ? <Skeleton width={220} height={20} /> : title}</h2>
          {onClose && (
            <button type="button" className="icon-btn sm" aria-label={tr('common.close')} data-tip={tr('common.close')} onClick={onClose}><X size={18} /></button>
          )}
        </div>
        {q.isError ? (
          <p className="error-text" role="alert">{(q.error as Error).message}</p>
        ) : r ? (
          <>
            <div className="rec-meta">
              {[when(r), fmtDuration(r.duration_s), r.folder].filter(Boolean).join(' · ')}
              {r.status !== 'completed' && <span className="pill warn">{r.status || 'processing'}</span>}
              {r.local_only && <span className="pill warn" data-tip={tr('speakr.recordingSheet.onlyLocalModelsReadThis')}><Lock size={11} />{' '}{tr('common.localOnly')}</span>}
              {r.is_shared && <span className="pill info" data-tip={r.can_edit ? tr('speakr.recordingSheet.sharedWithYouYouMay') : tr('speakr.recordingSheet.sharedWithYouToRead')}>{tr('speakr.recordingSheet.sharedBy', { ownerName: r.owner_name || tr('speakr.recordingSheet.anotherUser') })}</span>}
            </div>
            {r.participants.length > 0 && <div className="rec-meta">{tr('speakr.recordingSheet.with', { participants: r.participants.join(', ') })}</div>}
            {r.tags.length > 0 && <div className="rec-tags">{r.tags.map((x) => <span key={x} className="chip">{x}</span>)}</div>}
            <div className="rec-actions">
              {/* ACT recording.follow_up @web.recording */}
              <FollowUpButtons cid={cid} rid={rid} ev={ev} disabled={!r.has_summary} />
              <button type="button" className="btn text sm" onClick={() => useRecordingSheet.getState().showTasks({ cid, rid, title, ev })} disabled={!r.has_summary && !r.has_transcript}>
                <CheckSquare size={16} aria-hidden />{' '}{tr('common.addTasks')}
              </button>
              {/* ACT recording.ask @web.recording, ACT agent.ask_about @web.recording: the ask capture about this recording. */}
              <button type="button" className="btn text sm" data-tip={tr('speakr.recordingSheet.askShiftA', { name: assistantNameNow() })} onClick={(e) => {
                openAsk({ kind: 'recording', cid, rid, title }, e.currentTarget)
                onClose?.()
              }}>
                <Bot size={16} aria-hidden />{' '}{tr('speakr.recordingSheet.ask', { name: assistantNameNow() })}
              </button>
              <a className="btn text sm" href={speakrAt(r.url, t)} target="_blank" rel="noreferrer">
                <ExternalLink size={16} aria-hidden /> {t !== null ? tr('speakr.recordingSheet.playInSpeakrAt', { t: fmtT(t) }) : tr('speakr.recordingSheet.openInSpeakr')}
              </a>
              {/* ACT recording.edit @web.recording */}
              {r.write_back && (
                <button type="button" className="btn text sm" onClick={() => setEditing(true)}><Pencil size={16} aria-hidden />{' '}{tr('common.edit2')}</button>
              )}
              {/* ACT recording.share_link @web.recording */}
              {r.share_links && (
                <button type="button" className="btn text sm" onClick={() => setSharing(true)}><Link2 size={16} aria-hidden />{' '}{tr('speakr.recordingSheet.shareLink')}</button>
              )}
              {r.share_links && r.share_list && (
                <button type="button" className="btn text sm" onClick={() => setLinks(true)}>{tr('speakr.recordingSheet.publicLinks')}</button>
              )}
            </div>
            {editing && <EditDialog r={r} onClose={() => setEditing(false)} />}
            <ShareDialog r={r} open={sharing} onClose={() => setSharing(false)} />
            {links && <ShareLinksDialog r={r} onClose={() => setLinks(false)} />}
          </>
        ) : null}
      </div>
      <ScrollTabs className="rec-tabs" kind="tablist" label={tr('common.recording')} revealKey={tab}>
        {tabs.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={`rec-tab${tab === id ? ' on' : ''}`} onClick={() => setTab(id)}>
            {label}{id === 'links' && r?.links?.length ? ` (${r.links.length})` : ''}
          </button>
        ))}
      </ScrollTabs>
      <div className="rec-body" role="tabpanel" aria-label={tabs.find((x) => x[0] === tab)?.[1]}>
        {q.isLoading ? <Skeleton height={80} /> : !r ? null : tab === 'summary' ? (
          r.unavailable?.summary ? <p className="muted">{tr('speakr.recordingSheet.speakrDidNotAnswer', { summary: r.unavailable.summary })}</p>
            : r.summary ? <div className="rec-md"><Suspense fallback={<p style={{ whiteSpace: 'pre-wrap' }}>{r.summary}</p>}><GfmText text={r.summary} img={BlockedImage} /></Suspense></div>
              : <p className="muted">{tr('speakr.recordingSheet.noSummaryYet')}</p>
        ) : tab === 'transcript' ? (
          <TranscriptTab cid={cid} rid={rid} t={t} url={r.url} />
        ) : tab === 'notes' ? (
          r.notes ? <div className="rec-md"><Suspense fallback={<p style={{ whiteSpace: 'pre-wrap' }}>{r.notes}</p>}><GfmText text={r.notes} img={BlockedImage} /></Suspense></div> : <p className="muted">{tr('speakr.recordingSheet.noNotes')}</p>
        ) : tab === 'events' ? (
          <EventsTab r={r} onNavigate={onClose} />
        ) : tab === 'people' ? (
          // ACT speaker.map @web.recording (the People tab, SpeakerPeople.tsx)
          <SpeakerPeople r={r} />
        ) : (
          <LinksTab r={r} onNavigate={onClose} />
        )}
      </div>
    </div>
  )
}

function TranscriptTab({ cid, rid, t, url }: { cid: number; rid: number; t: number | null; url: string }) {
  const q = useTranscript(cid, rid, true)
  const [find, setFind] = useState('')
  const marked = useRef<HTMLLIElement | null>(null)
  const segs = useMemo(() => {
    const words = find.toLowerCase().split(/\s+/).filter(Boolean)
    return (q.data?.segments ?? []).filter((s) => words.every((w) => s.text.toLowerCase().includes(w) || s.speaker.toLowerCase().includes(w)))
  }, [q.data, find])
  // The turn being said at the cited moment.
  const at = useMemo(() => {
    if (t === null || !q.data) return null
    let best: Segment | null = null
    for (const s of q.data.segments) if (s.t !== null && s.t <= t + 1) best = s
    return best
  }, [q.data, t])
  useEffect(() => {
    marked.current?.scrollIntoView({ block: 'center' })
  }, [at])
  if (q.isLoading) return <div className="rec-wait"><Spinner small /> <span className="muted">{tr('speakr.recordingSheet.loadingTheTranscript')}</span></div>
  if (q.isError) return <p className="error-text">{(q.error as Error).message}</p>
  return (
    <div className="rec-transcript">
      <label className="rec-find">
        <Search size={16} aria-hidden />
        <input className="input" placeholder={tr('speakr.recordingSheet.findInTranscript')} aria-label={tr('speakr.recordingSheet.findInTranscript')} value={find} onChange={(e) => setFind(e.target.value)} />
      </label>
      {q.data?.truncated && <p className="muted">{tr('speakr.recordingSheet.theTranscriptIsLongThe')}</p>}
      <ol className="rec-turns">
        {segs.map((s, i) => (
          <li key={`${s.t}-${i}`} ref={s === at ? marked : undefined} className={s === at ? 'at' : ''} data-at={s === at ? 'true' : undefined}>
            {s.t !== null ? (
              <a className="rec-time" href={speakrAt(url, s.t)} target="_blank" rel="noreferrer" data-tip={tr('speakr.recordingSheet.playInSpeakrFromHere')}>{fmtT(s.t)}</a>
            ) : <span className="rec-time" />}
            <span className="rec-say">
              {s.speaker && <b>{s.speaker}</b>} {s.text}
            </span>
          </li>
        ))}
      </ol>
      {!segs.length && <p className="muted">{find ? tr('common.nothingMatches') : tr('speakr.recordingSheet.noTranscriptYet')}</p>}
    </div>
  )
}

const KIND_ICON = { event: CalendarDays, conversation: Mail, task: CheckSquare }

// ACT recording.link @web.recording, ACT recording.unlink @web.recording
function LinksTab({ r, onNavigate }: { r: Recording; onNavigate?: () => void }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [making, setMaking] = useState(false)
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: speakrKeys.recording(r.connector_id, r.recording_id) })
    void qc.invalidateQueries({ queryKey: ['speakr', 'event'] })
  }
  const confirm = (l: { kind: RecordingLink['kind']; target: string; occurrence?: string | null }, msg: string) =>
    linkRecording({ connector_id: r.connector_id, recording_id: r.recording_id, kind: l.kind, target: l.target, occurrence: l.occurrence ?? '' })
      .then(() => { refresh(); toast({ message: msg }) })
      .catch((e) => toastError(e))
  const remove = (l: RecordingLink, msg: string) =>
    unlinkRecording(l.id)
      .then(() => { refresh(); toast({ message: msg, actionLabel: tr('common.undo'), onAction: () => void confirm(l, tr('speakr.recordingSheet.linkRestored')) }) })
      .catch((e) => toastError(e))
  const open = (l: RecordingLink) => {
    const to = l.kind === 'event' ? eventPath(l.target, l.occurrence ?? '') : l.kind === 'task' ? taskPath(l.target) : mailPath('all', Number(l.conversation_id ?? l.target))
    onNavigate?.()
    nav(to)
  }
  const links = r.links ?? []
  return (
    <div className="rec-links">
      {links.length === 0 && <p className="muted">{tr('speakr.recordingSheet.notLinkedToAnEvent')}</p>}
      <ul>
        {links.map((l) => {
          const Icon = KIND_ICON[l.kind]
          return (
            <li key={l.id} className={`rec-link ${l.state}`}>
              <Icon size={16} aria-hidden />
              <button type="button" className="btn text sm rec-link-title" onClick={() => open(l)}>
                {l.title || (l.kind === 'event' ? tr('common.event') : l.kind === 'task' ? tr('common.task') : tr('common.conversation'))}
              </button>
              {l.start && <span className="muted">{new Date(l.start).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' })}</span>}
              {l.state === 'suggested' ? (
                <span className="rec-ask">
                  <span className="muted">{tr('speakr.recordingSheet.suggested')}</span>
                  <button type="button" className="btn tonal sm" onClick={() => void confirm(l, l.kind === 'event' ? tr('speakr.recordingSheet.recordingLinkedToTheEvent') : tr('speakr.recordingSheet.linkConfirmed'))}>{tr('common.confirm')}</button>
                  <button type="button" className="btn text sm" onClick={() => void remove(l, 'Suggestion dismissed.')}>{tr('common.dismiss')}</button>
                </span>
              ) : (
                <button type="button" className="btn text sm" onClick={() => void remove(l, 'Link removed.')}>{tr('common.remove')}</button>
              )}
            </li>
          )
        })}
      </ul>
      {(r.candidates ?? []).length > 0 && (
        <>
          <h3 className="rec-sub">{tr('speakr.recordingSheet.eventsAtThatTime')}</h3>
          <ul>
            {r.candidates!.map((c) => (
              <li key={`${c.uid}@${c.occurrence}`} className="rec-link">
                <CalendarDays size={16} aria-hidden />
                <span className="rec-link-title">{c.summary || tr('common.noTitle')}</span>
                {c.start && <span className="muted">{new Date(c.start).toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })}</span>}
                <button type="button" className="btn tonal sm" onClick={() => void confirm({ kind: 'event', target: c.uid, occurrence: c.occurrence }, tr('speakr.recordingSheet.recordingLinkedToTheEvent'))}>{tr('common.link')}</button>
              </li>
            ))}
          </ul>
        </>
      )}
      <h3 className="rec-sub">{tr('common.notesAndTables')}</h3>
      {/* ACT note.link @web.recording, ACT note.unlink @web.recording, ACT table.link @web.recording, ACT table.unlink @web.recording */}
      <LinkedPanel kind="recording" target={{ connector_id: r.connector_id, recording_id: r.recording_id }} title={r.title || tr('common.recording')} />
      {/* ACT note.from_recording @web.recording: the server writes the note (title, date, people, summary,
          action items) and links it; the toast opens it. */}
      <button type="button" className="btn tonal sm rec-make-note" disabled={making} onClick={() => {
        setMaking(true)
        noteFromRecording(qc, { connector_id: r.connector_id, recording_id: r.recording_id })
          .then((n) => {
            refreshLinked(qc)
            toast({ message: tr('speakr.recordingSheet.noteMadeAndLinked'), actionLabel: tr('common.open'), onAction: () => { onNavigate?.(); nav(notePath(n.id)) }, duration: 6000 })
          }, (e) => toastError(e, tr('speakr.recordingSheet.couldNotMakeTheNote')))
          .finally(() => setMaking(false))
      }}>
        <StickyNote size={16} aria-hidden />{' '}{tr('speakr.recordingSheet.makeANoteFromThis')}
      </button>
    </div>
  )
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

// ACT recording.event_to_calendar @web.recording
function EventsTab({ r, onNavigate }: { r: Recording; onNavigate?: () => void }) {
  const q = useRecordingEvents(r.connector_id, r.recording_id, true)
  const nav = useNavigate()
  if (q.isLoading) return <div className="rec-wait"><Spinner small /> <span className="muted">{tr('speakr.recordingSheet.askingSpeakr')}</span></div>
  if (q.isError) return <p className="error-text">{errText(q.error)}</p>
  const events = q.data?.events ?? []
  if (!events.length) return <p className="muted">{tr('speakr.recordingSheet.speakrFoundNoMeetingsOr')}</p>
  const add = (ev: (typeof events)[number]) => {
    const seed = extractedEventSeed(ev, r)
    if (!seed) return toastError(new Error(tr('speakr.recordingSheet.thisEventHasNoTime')), tr('speakr.recordingSheet.notAdded'))
    // The event editor opens prefilled; nothing is created until you save it there.
    useCalUI.getState().set({ editor: { mode: 'new', seed } })
    onNavigate?.()
    nav(calendarPath(null))
  }
  return (
    <div className="rec-events">
      <p className="muted" style={{ marginTop: 0 }}>{tr('speakr.recordingSheet.meetingsAndDatesMentionedIn')}</p>
      <ul>
        {events.map((ev, i) => (
          <li key={`${i}-${ev.title}`}>
            <CalendarDays size={16} aria-hidden />
            <span className="grow">
              <b>{ev.title || tr('speakr.recordingSheet.meeting')}</b>
              <span className="muted"> · {ev.start ? (extractedEventSeed(ev, r)?.start ?? new Date(ev.start)).toLocaleString(appLocale(), { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() }) : tr('speakr.recordingSheet.noTime')}{ev.location ? ` · ${ev.location}` : ''}</span>
              {!!ev.attendees?.length && <span className="muted">{' '}{tr('speakr.recordingSheet.with2', { map: ev.attendees.map((a) => a.name || a.email).join(', ') })}</span>}
            </span>
            <button type="button" className="btn tonal sm" disabled={!ev.start} onClick={() => add(ev)}>
              <CalendarPlus size={16} aria-hidden />{' '}{tr('speakr.recordingSheet.addToCalendar')}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ACT recording.edit
function EditDialog({ r, onClose }: { r: Recording; onClose: () => void }) {
  const qc = useQueryClient()
  const [title, setTitle] = useState(r.title)
  const [people, setPeople] = useState(r.participants.join(', '))
  const [note, setNote] = useState('')
  const [tags, setTags] = useState(r.tags.join(', '))
  const [busy, setBusy] = useState(false)
  const list = (v: string) => v.split(',').map((x) => x.trim()).filter(Boolean)
  const save = async () => {
    const body: { title?: string; participants?: string[]; append_notes?: string; tags?: string[] } = {}
    if (title.trim() && title.trim() !== r.title) body.title = title.trim()
    if (list(people).join(', ') !== r.participants.join(', ')) body.participants = list(people)
    if (list(tags).join(', ') !== r.tags.join(', ')) body.tags = list(tags)
    if (note.trim()) body.append_notes = note.trim()
    if (!Object.keys(body).length) return onClose()
    setBusy(true)
    try {
      const out = await editRecording(r.connector_id, r.recording_id, body)
      void qc.invalidateQueries({ queryKey: ['speakr'] })
      const back: { title?: string; participants?: string[]; tags?: string[] } = {}
      if (body.title || body.participants) Object.assign(back, { title: out.previous.title, participants: out.previous.participants })
      if (body.tags && out.previous.tags) back.tags = out.previous.tags
      const undo = Object.keys(back).length
        ? () => void editRecording(r.connector_id, r.recording_id, back)
          .then(() => qc.invalidateQueries({ queryKey: ['speakr'] }))
        : undefined
      toast({ message: tr('speakr.recordingSheet.changedInSpeakr'), ...(undo ? { actionLabel: tr('common.undo'), onAction: undo } : {}) })
      onClose()
    } catch (e) {
      toastError(e, tr('common.notChanged'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onClose={onClose} title={tr('speakr.recordingSheet.editInSpeakr')} modal
      footer={<>
        <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled" disabled={busy} onClick={() => void save()}>{tr('speakr.recordingSheet.saveInSpeakr')}</button>
      </>}>
      <div className="rec-edit">
        <div className="field"><label htmlFor="rec-edit-title">{tr('common.title')}</label>
          <input id="rec-edit-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div className="field"><label htmlFor="rec-edit-people">{tr('common.participants')}</label>
          <input id="rec-edit-people" className="input" value={people} onChange={(e) => setPeople(e.target.value)} placeholder={tr('speakr.recordingSheet.danaEvan')} /></div>
        <div className="field"><label htmlFor="rec-edit-tags">{tr('common.tags')}</label>
          <input id="rec-edit-tags" className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder={tr('speakr.recordingSheet.workClients')} /></div>
        <div className="field"><label htmlFor="rec-edit-note">{tr('speakr.recordingSheet.addToTheNotes')}</label>
          <textarea id="rec-edit-note" className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder={tr('speakr.recordingSheet.aLineAddedAtThe')} /></div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>{tr('speakr.recordingSheet.tagsAreNamesOfTags')}</p>
      </div>
    </Dialog>
  )
}

// ACT recording.share_link
function ShareDialog({ r, open, onClose }: { r: Recording; open: boolean; onClose: () => void }) {
  const [got, setGot] = useState<ShareOut | null>(null)
  const [busy, setBusy] = useState(false)
  const url = got?.share_url ?? null
  const close = () => { setGot(null); onClose() }
  const create = async () => {
    setBusy(true)
    try {
      setGot(await shareRecording(r.connector_id, r.recording_id, false))
    } catch (e) {
      toastError(e, tr('speakr.recordingSheet.noShareLink'))
      close()
    } finally {
      setBusy(false)
    }
  }
  if (url)
    return (
      <Dialog open onClose={close} title={tr('speakr.recordingSheet.publicLink')} footer={<button type="button" className="btn filled" onClick={close}>{tr('common.done')}</button>}>
        <div className="stack">
          <input className="input" readOnly value={url} aria-label={tr('speakr.recordingSheet.shareLink')} onFocus={(e) => e.currentTarget.select()} />
          <button type="button" className="btn tonal sm" onClick={() => void navigator.clipboard?.writeText(url).then(() => toast({ message: tr('common.linkCopied') }))}>
            <Copy size={16} aria-hidden />{' '}{tr('common.copyLink')}
          </button>
          {got?.share_notes && (
            <p className="banner" role="status" style={{ margin: 0 }}>
              {got.flags_differ ? tr('speakr.recordingSheet.speakrReusedAnEarlierLink') : tr('speakr.recordingSheet.thisLinkAlsoShowsYour')}
            </p>
          )}
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            {tr('speakr.recordingSheet.anyoneWithThisLinkCan', { value: got?.existing ? tr('speakr.recordingSheet.anEarlierLinkOfThis') : '', value2: r.share_list ? tr('speakr.recordingSheet.revokeItUnderPublicLinks') : tr('speakr.recordingSheet.revokeItInSpeakr') })}
          </p>
        </div>
      </Dialog>
    )
  return (
    <ConfirmDialog
      open={open}
      title={tr('speakr.recordingSheet.createAPublicLink')}
      body={tr('speakr.recordingSheet.anyoneWithTheLinkCan', { title: r.title })}
      confirmLabel={tr('speakr.recordingSheet.createPublicLink')}
      danger
      busy={busy}
      onClose={close}
      onConfirm={() => void create()}
    />
  )
}

// ACT recording.share_link (the recording's public links, each with Revoke)
function ShareLinksDialog({ r, onClose }: { r: Recording; onClose: () => void }) {
  const q = useShareLinks(r.connector_id, r.recording_id, true)
  const qc = useQueryClient()
  const revoke = (id: number) =>
    revokeShareLink(r.connector_id, r.recording_id, id)
      .then(() => { void qc.invalidateQueries({ queryKey: ['speakr', 'shares', r.connector_id, r.recording_id] }); toast({ message: tr('speakr.recordingSheet.linkRevokedItStopsWorking') }) })
      .catch((e) => toastError(e, tr('speakr.recordingSheet.notRevoked')))
  const rows = q.data?.shares ?? []
  return (
    <Dialog open onClose={onClose} title={tr('speakr.recordingSheet.publicLinks')} footer={<button type="button" className="btn filled" onClick={onClose}>{tr('common.done')}</button>}>
      {q.isLoading ? <Spinner small /> : q.isError ? <p className="error-text">{errText(q.error)}</p> : !rows.length ? (
        <p className="muted" style={{ margin: 0 }}>{tr('speakr.recordingSheet.thisRecordingHasNoPublic')}</p>
      ) : (
        <ul className="rec-links">
          {rows.map((x) => (
            <li key={x.id} className="rec-link">
              <Link2 size={16} aria-hidden />
              <span className="grow">
                <code>{x.share_url}</code>
                <span className="muted"> · {x.share_notes ? tr('speakr.recordingSheet.summaryAndNotes') : x.share_summary ? 'summary' : tr('speakr.recordingSheet.recordingOnly')}
                  {x.created_at ? ` · ${new Date(x.created_at).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' })}` : ''}</span>
              </span>
              <button type="button" className="btn text sm" onClick={() => void revoke(x.id)}>{tr('speakr.recordingSheet.revoke')}</button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  )
}

/** The side sheet over any app, opened through useRecordingSheet. */
export function RecordingSheetHost() {
  const target = useRecordingSheet((s) => s.open)
  const close = useRecordingSheet((s) => s.close)
  const tasks = useRecordingSheet((s) => s.tasks)
  useEffect(() => {
    if (!target) return
    const k = (e: KeyboardEvent) => {
      // A dialog over the sheet (edit, share, the palette) has the focus and closes first.
      const inOther = (e.target as Element | null)?.closest?.('[role="dialog"]:not(.rec-sheet)')
      if (e.key === 'Escape' && !useRecordingSheet.getState().tasks && !inOther) {
        e.stopPropagation()
        close()
      }
    }
    document.addEventListener('keydown', k, true)
    return () => document.removeEventListener('keydown', k, true)
  }, [target, close])
  const dialog = tasks && <ActionItemsDialog cid={tasks.cid} rid={tasks.rid} title={tasks.title} ev={tasks.ev} onClose={() => useRecordingSheet.getState().showTasks(null)} />
  if (!target) return dialog
  return (
    <>
      {createPortal(
        <div className="rec-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) close() }}>
          <aside className="rec-sheet" role="dialog" aria-modal="true" aria-labelledby="rec-title">
            <RecordingView key={`${target.cid}:${target.rid}:${target.t}`} cid={target.cid} rid={target.rid} t={target.t} onClose={close} />
          </aside>
        </div>,
        document.body,
      )}
      {dialog}
    </>
  )
}
