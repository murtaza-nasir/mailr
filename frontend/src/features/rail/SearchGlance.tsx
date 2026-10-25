// Mail search with events, tasks and notes: above the mail results in the All scope, the top three matching events (upcoming
// first), the top three open tasks and the top three notes, each with "See all", in the order of the
// app the search started in (features/search/scope.ts).
// Mail operators that mean a person pass through as `with:`; other mail-only operators drop out.
// ACT event.search @web.list, ACT task.search @web.list, ACT note.search @web.list

import { useQuery } from '@tanstack/react-query'
import { CalendarDays, CircleCheck, ListChecks, StickyNote, Table2 } from 'lucide-react'
import { Fragment, type ReactNode } from 'react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import { formatDate, formatTime, parseISO, isISODate } from '../../lib/dates'
import { calendarSearchPath, eventPath, notePath, notesPath, tableRoutePath, taskPath, taskSearchPath } from '../../lib/routes'
import { useNoteSearchAll } from '../notes/api'
import { useDatePrefs } from '../tasks/api'
import { isDone } from '../tasks/model'
import type { GlanceGroup } from '../search/scope'
import './panels.css'
import { t as tr } from '../../i18n'

const PERSON = /^-?(from|to|cc|bcc|with):/i
const DROP = /^-?(in|label|is|has|category|filename|larger|smaller|older_than|newer_than|after|before|subject|list|deliveredto|rfc822msgid|size|recording):/i

/** A mail query as a calendar query: free words and quoted phrases stay, people become `with:`. */
export function toCalendarQuery(q: string): string {
  const parts = q.match(/-?\w+:"[^"]*"|-?\w+:\S+|"[^"]*"|\S+/g) ?? []
  const out: string[] = []
  for (const p of parts) {
    if (PERSON.test(p)) {
      const neg = p.startsWith('-')
      const v = p.slice(p.indexOf(':') + 1)
      if (v) out.push(`${neg ? '-' : ''}with:${v}`)
    } else if (!DROP.test(p) && !/^(OR|AND)$/.test(p)) out.push(p)
  }
  return out.join(' ').trim()
}

interface Hit {
  kind: 'event' | 'task'
  uid: string
  summary: string
  start?: string
  end?: string | null
  all_day?: boolean
  occurrence?: string
  location?: string
  due?: string | null
  status?: string
  completed?: boolean
  calendar_color?: string | null
  color?: string | null
}

function useHits(q: string, kind: 'event' | 'task') {
  const query = kind === 'task' ? tr('rail.searchGlance.isDone', { q }) : q
  return useQuery<Hit[]>({
    queryKey: [...qk.calendar.search(query), kind],
    queryFn: async ({ signal }) => {
      const r = await api.get<{ items: Hit[] }>('/calendar/search', { q: query, kind, limit: 3 }, signal)
      return (r.items ?? []).filter((h) => (kind === 'task' ? !isDone({ status: h.status ?? '', completed: h.completed } as never) : true)).slice(0, 3)
    },
    enabled: !!q,
    staleTime: 30_000,
    retry: false,
  })
}

/** The groups' hits for a mail query (shared with the mail list, which says "No mail matches" when
 * only these have hits). */
export function useGlance(q: string, enabled = true) {
  const cq = enabled ? toCalendarQuery(q) : ''
  const ev = useHits(cq, 'event')
  const tk = useHits(cq, 'task')
  const nt = useNoteSearchAll(cq, 3, !!cq)
  const events = ev.data ?? []
  const tasks = tk.data ?? []
  const notes = (nt.data?.notes ?? []).slice(0, 3)
  // Tables share the Notes section: the top 3 of both.
  const tables = (nt.data?.tables ?? []).slice(0, Math.max(0, 3 - notes.length))
  return { cq, events, tasks, notes, tables, total: events.length + tasks.length + notes.length + tables.length }
}

export function SearchGlance({ q, order = ['notes', 'events', 'tasks'] }: { q: string; order?: GlanceGroup[] }) {
  const prefs = useDatePrefs()
  const { cq, events, tasks, notes, tables, total } = useGlance(q)
  if (!cq || !total) return null
  const when = (s: string | null | undefined, allDay?: boolean) => {
    if (!s) return ''
    if (allDay || isISODate(s)) return formatDate(s.slice(0, 10), 'day')
    const d = parseISO(s, prefs.zone)
    return d ? `${formatDate(d, 'day', prefs.zone)}, ${formatTime(d, prefs, { compact: true })}` : ''
  }
  const cols: Record<GlanceGroup, ReactNode> = {
    notes: (
      <>
      {notes.length + tables.length > 0 && (
        <div className="sg-col" data-testid="search-notes">
          <div className="sg-head">
            <StickyNote size={16} aria-hidden /> <b>{tr('common.notes')}</b>
            <Link className="btn text sm" to={notesPath('search', cq)}>{tr('rail.searchGlance.seeAllInNotes')}</Link>
          </div>
          {notes.map((n) => (
            <Link key={n.id} className="sg-row" to={notePath(n.id, n.matches?.find((m) => m.item_id)?.item_id ?? null)}>
              {n.kind === 'checklist' ? <ListChecks size={14} aria-hidden /> : <StickyNote size={14} aria-hidden />}
              <span className="sg-title">{n.title || (n.kind === 'checklist' ? tr('common.untitledList') : tr('common.untitledNote'))}</span>
              <span className="sg-when">{n.matches?.[0]?.text ?? (n.kind === 'text' ? n.body.slice(0, 80) : '')}</span>
            </Link>
          ))}
          {/* Tables that match, after the notes. */}
          {tables.map((t) => (
            <Link key={t.id} className="sg-row" to={tableRoutePath(t.id, null, t.matches?.[0]?.row_id ?? null)}>
              <Table2 size={14} aria-hidden />
              <span className="sg-title">{t.name || tr('common.untitledTable')}</span>
              <span className="sg-when">{t.matches?.[0]?.text ?? ''}</span>
            </Link>
          ))}
        </div>
      )}
      </>
    ),
    events: (
      <>
      {events.length > 0 && (
        <div className="sg-col">
          <div className="sg-head">
            <CalendarDays size={16} aria-hidden /> <b>{tr('common.events')}</b>
            <Link className="btn text sm" to={calendarSearchPath(cq)}>{tr('rail.searchGlance.seeAllInCalendar')}</Link>
          </div>
          {events.map((h) => (
            <Link key={`${h.uid}|${h.occurrence ?? h.start}`} className="sg-row" to={eventPath(h.uid, h.occurrence ?? h.start ?? '')}>
              <i className="sg-dot" style={{ background: h.color || h.calendar_color || 'var(--primary)' }} />
              <span className="sg-title">{h.summary || tr('rail.searchGlance.noTitle')}</span>
              <span className="sg-when">{when(h.start, h.all_day)}{h.location ? ` · ${h.location}` : ''}</span>
            </Link>
          ))}
        </div>
      )}
      </>
    ),
    tasks: (
      <>
      {tasks.length > 0 && (
        <div className="sg-col">
          <div className="sg-head">
            <CircleCheck size={16} aria-hidden /> <b>{tr('common.tasks')}</b>
            <Link className="btn text sm" to={taskSearchPath(cq)}>{tr('rail.searchGlance.seeAllInTasks')}</Link>
          </div>
          {tasks.map((h) => (
            <Link key={h.uid} className="sg-row" to={taskPath(h.uid)}>
              <i className="sg-ring" />
              <span className="sg-title">{h.summary}</span>
              <span className="sg-when">{h.due ? tr('rail.searchGlance.due', { when: when(h.due) }) : ''}</span>
            </Link>
          ))}
        </div>
      )}
      </>
    ),
  }
  return (
    <section className="sg" role="region" aria-label={tr('rail.searchGlance.eventsTasksAndNotes')}>
      {order.map((g) => <Fragment key={g}>{cols[g]}</Fragment>)}
    </section>
  )
}
