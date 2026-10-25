// The event detail's "Meeting prep" section (event.prep; backend calendar/meeting_prep.py): for an
// event with guests, the last recording of the same meeting and its open action items, the latest
// threads with the guests, open questions, threads waiting on them and what you promised, related
// tasks, the assistant's memories and recent files. The pack loads when the section opens (or at once with
// ?prep=1, the reminder's Prep link). Summarise asks the user's chat model for one paragraph; a
// pack with local-only mail is summarised only by a local model.

import { ChevronDown, ClipboardList, FileText, ListTodo, MessageSquare, NotebookPen, Sparkles, Video } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { taskPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { useRecordingSheet } from '../speakr/store'
import { fetchPrepSummary, guestName, prepCounts, prepEmpty, usePrep, type PrepPack, type PrepSummary } from './prep'
import { t as tr, appLocale } from '../../i18n'

const day = (s: string | null | undefined) => (s ? new Date(s).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' }) : '')

// ACT event.prep @web.calendar @web.sheet
export function MeetingPrep({ uid, occurrence, initiallyOpen, onLeave }: {
  uid: string
  occurrence: string | null | undefined
  /** Opened from the reminder's Prep link (?prep=1). */
  initiallyOpen?: boolean
  /** Leaving the detail (a popover closes before it navigates). */
  onLeave?: () => void
}) {
  const [open, setOpen] = useState(!!initiallyOpen)
  const q = usePrep(uid, occurrence, open)
  const pack = q.data
  return (
    <section className="cal-prep" aria-label={tr('calendar.meetingPrep.meetingPrep')} data-testid="meeting-prep">
      <button type="button" className="cal-prep-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ClipboardList size={14} aria-hidden />
        <span>{tr('calendar.meetingPrep.meetingPrep')}</span>
        {pack && <span className="muted cal-prep-counts">{prepEmpty(pack) ? tr('calendar.meetingPrep.nothingYet') : prepCounts(pack)}</span>}
        <ChevronDown className={`cal-chev${open ? ' open' : ''}`} size={16} aria-hidden />
      </button>
      {open && q.isLoading && <p className="muted cal-prep-note">{tr('calendar.meetingPrep.gathering')}</p>}
      {open && q.isError && <p className="muted cal-prep-note">{tr('calendar.meetingPrep.thePrepCouldNotBe')}</p>}
      {open && pack && <Body pack={pack} uid={uid} occurrence={occurrence} onLeave={onLeave} />}
    </section>
  )
}

function Body({ pack, uid, occurrence, onLeave }: { pack: PrepPack; uid: string; occurrence: string | null | undefined; onLeave?: () => void }) {
  const nav = useNavigate()
  const [sum, setSum] = useState<PrepSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const go = (path: string) => {
    onLeave?.()
    nav(path)
  }
  const openConv = (cid: number) => go(mailPath('all', cid))
  const rec = pack.recording
  const oi = pack.open_items
  if (prepEmpty(pack)) {
    return (
      <p className="muted cal-prep-note" data-testid="prep-empty">
        {tr('calendar.meetingPrep.nothingToPrepareYetNo', { value: pack.hidden ? tr('calendar.meetingPrep.someLocalOnlyItemsAre') : '' })}
      </p>
    )
  }
  const summarise = () => {
    setBusy(true)
    fetchPrepSummary(uid, occurrence)
      .then(setSum)
      .catch(() => setSum({ status: 'error', text: null, message: tr('calendar.meetingPrep.theSummaryCouldNotBe') }))
      .finally(() => setBusy(false))
  }
  return (
    <div className="cal-prep-body">
      <div className="cal-prep-sum" data-testid="prep-summary">
        {sum?.status === 'ok' && sum.text ? <p>{sum.text}</p> : sum ? <p className="muted">{sum.message}</p> : null}
        {!sum?.text && (
          <button type="button" className="btn text sm" disabled={busy} onClick={summarise}>
            <Sparkles size={14} aria-hidden /> {busy ? tr('calendar.meetingPrep.summarising') : tr('calendar.meetingPrep.summarise')}
          </button>
        )}
      </div>

      {rec && (
        <div className="cal-prep-sec" data-testid="prep-recording">
          <h4><Video size={13} aria-hidden />{' '}{tr('calendar.meetingPrep.lastTime')}</h4>
          <button type="button" className="cal-prep-link" onClick={() => { onLeave?.(); useRecordingSheet.getState().show(rec.connector_id, rec.recording_id) }}>
            {rec.title || tr('common.recording')}
          </button>
          <span className="muted"> {day(rec.meeting_date ?? rec.created_at)}</span>
          {rec.summary_line && <p className="cal-prep-quote" dir="auto">{rec.summary_line}</p>}
          {rec.action_items.open.length > 0 && (
            <ul className="cal-prep-list">
              {rec.action_items.open.map((t) => (
                <li key={t.uid}>
                  <button type="button" className="cal-prep-link" onClick={() => go(taskPath(t.uid))} dir="auto">{t.summary}</button>
                  {t.due && <span className="muted">{' '}{tr('calendar.meetingPrep.due', { day: day(t.due) })}</span>}
                </li>
              ))}
            </ul>
          )}
          <p className="muted cal-prep-small">
            {tr('calendar.meetingPrep.openActionItems', { count: rec.action_items.open.length, value: rec.action_items.done ? tr('calendar.meetingPrep.done', { done: rec.action_items.done }) : '' })}
          </p>
        </div>
      )}

      {(oi.questions.length > 0 || oi.waiting_on.length > 0 || oi.promised.length > 0) && (
        <div className="cal-prep-sec" data-testid="prep-open">
          <h4>{tr('calendar.meetingPrep.openItems')}</h4>
          <ul className="cal-prep-list">
            {oi.questions.map((x) => (
              <li key={`q${x.message_id}${x.question}`}>
                <span className="cal-prep-tag">{tr('calendar.meetingPrep.asked')}</span> <button type="button" className="cal-prep-link" onClick={() => openConv(x.conversation_id)}>“{x.question}”</button>
                <span className="muted"> · {guestName(pack, x.from)}</span>
              </li>
            ))}
            {oi.waiting_on.map((x) => (
              <li key={`w${x.message_id}`}>
                <span className="cal-prep-tag">{tr('common.waiting')}</span> <button type="button" className="cal-prep-link" onClick={() => openConv(x.conversation_id)}>{x.subject || tr('common.noSubject')}</button>
                {x.since && <span className="muted">{' '}{tr('calendar.meetingPrep.since', { day: day(x.since) })}</span>}
              </li>
            ))}
            {oi.promised.map((x) => (
              <li key={`p${x.message_id}${x.text}`}>
                <span className="cal-prep-tag">{tr('calendar.meetingPrep.youSaid')}</span> <button type="button" className="cal-prep-link" onClick={() => openConv(x.conversation_id)}>“{x.text}”</button>
                {x.date && <span className="muted"> · {day(x.date)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {pack.threads.length > 0 && (
        <div className="cal-prep-sec" data-testid="prep-threads">
          <h4><MessageSquare size={13} aria-hidden />{' '}{tr('calendar.meetingPrep.recentThreads')}</h4>
          <ul className="cal-prep-list">
            {pack.threads.map((t) => (
              <li key={t.conversation_id}>
                <button type="button" className="cal-prep-link" onClick={() => openConv(t.conversation_id)} dir="auto">{t.subject || tr('common.noSubject')}</button>
                <span className="muted"> · {day(t.last_date)}{t.from_me ? tr('calendar.meetingPrep.youWroteLast') : ''}</span>
                {t.snippet && <div className="cal-prep-snippet muted" dir="auto">{t.snippet}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {pack.tasks.length > 0 && (
        <div className="cal-prep-sec" data-testid="prep-tasks">
          <h4><ListTodo size={13} aria-hidden />{' '}{tr('common.tasks')}</h4>
          <ul className="cal-prep-list">
            {pack.tasks.map((t) => (
              <li key={t.uid}>
                <button type="button" className="cal-prep-link" onClick={() => go(taskPath(t.uid))} dir="auto">{t.summary}</button>
                {t.due && <span className="muted">{' '}{tr('calendar.meetingPrep.due', { day: day(t.due) })}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {pack.notes.length > 0 && (
        <div className="cal-prep-sec" data-testid="prep-notes">
          <h4><NotebookPen size={13} aria-hidden />{' '}{tr('calendar.meetingPrep.memories')}</h4>
          <ul className="cal-prep-list">
            {pack.notes.map((n) => (
              <li key={n.entity.id}>
                <b>{n.entity.name}</b>{n.entity.summary && <span className="muted"> · {n.entity.summary}</span>}
                {n.notes.length > 0 && <ul className="cal-prep-sub">{n.notes.map((m) => <li key={m.id}>{m.text}</li>)}</ul>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {pack.attachments.length > 0 && (
        <div className="cal-prep-sec" data-testid="prep-files">
          <h4><FileText size={13} aria-hidden />{' '}{tr('calendar.meetingPrep.files')}</h4>
          <ul className="cal-prep-list">
            {pack.attachments.map((a) => (
              <li key={`${a.message_id}${a.filename}`}>
                <button type="button" className="cal-prep-link" onClick={() => openConv(a.conversation_id)}>{a.filename}</button>
                <span className="muted"> · {a.direction === 'sent' ? tr('calendar.meetingPrep.youSent') : a.from_name || guestName(pack, a.from)} · {day(a.date)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {pack.hidden > 0 && <p className="muted cal-prep-small">{tr('calendar.meetingPrep.someLocalOnlyItemsAre2')}</p>}
    </div>
  )
}
