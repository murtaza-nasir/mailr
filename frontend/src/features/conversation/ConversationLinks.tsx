// The conversation's links panel: every event and
// task linked to this conversation (GET /conversations/{cid}/links), with a live status ("Done ✓",
// "Tomorrow 3 PM", "Cancelled"). A chip opens the event or the task in its app.
// The reverse direction of event.open_email and task.open_email (those open the email from the event or task).

import { useQueries, useQuery } from '@tanstack/react-query'
import { CalendarDays, CheckCircle2, Circle } from 'lucide-react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { useSettings } from '../../api/hooks'
import { diffDaysISO, formatDate, formatTime, isISODate, parseISO, resolveDatePrefs, todayISO, toISODate, type DatePrefs } from '../../lib/dates'
import { eventPath, taskPath } from '../../lib/routes'
import { summarizeRRule } from '../../lib/rrule'
import type { ApiEvent } from '../calendar/types'
import type { TaskItem } from '../tasks/model'
import { t as tr } from '../../i18n'

interface LinkRow {
  id: number
  kind: 'event' | 'task'
  uid: string
  summary: string
}

/** "Today 3 PM", "Tomorrow", "Fri 9 Oct, 10 AM", "Tue 6 Oct" for a date or an instant. */
export function relWhen(s: string | null | undefined, p: Pick<DatePrefs, 'zone' | 'hour12'>, now = new Date()): string {
  if (!s) return ''
  const today = todayISO(p.zone, now)
  const day = isISODate(s) ? s.slice(0, 10) : toISODate(parseISO(s, p.zone) ?? now, p.zone)
  const n = diffDaysISO(today, day)
  const d = n === 0 ? tr('common.today') : n === 1 ? tr('common.tomorrow') : n === -1 ? tr('common.yesterday') : formatDate(day, 'day')
  if (isISODate(s)) return d
  const at = parseISO(s, p.zone)
  return at ? `${d}${n >= -1 && n <= 1 ? ' ' : ', '}${formatTime(at, p, { compact: true })}` : d
}

function useTaskStatus(cid: number, enabled: boolean) {
  const q = (smart: string) => ({
    queryKey: ['tasks', 'list', { smart, conversation: cid }] as const,
    queryFn: async ({ signal }: { signal: AbortSignal }) => {
      const r = await api.get<{ items: TaskItem[] } | TaskItem[]>('/tasks', { smart, limit: 200, ...(smart === 'completed' ? { status: 'completed' } : {}) }, signal)
      return (Array.isArray(r) ? r : r.items ?? []).filter((t) => Number(t.conversation_id) === cid)
    },
    enabled,
    staleTime: 30_000,
  })
  const res = useQueries({ queries: [q('email'), q('completed')] })
  const by = new Map<string, TaskItem>()
  for (const r of res) for (const t of r.data ?? []) if (!by.has(t.uid)) by.set(t.uid, t)
  return by
}

/** `inline`: a span of chips that flows on the reader's subject line. */
export function ConversationLinks({ cid, inline }: { cid: number; inline?: boolean }) {
  const prefs = resolveDatePrefs(useSettings() as unknown as { timezone?: string; time_format?: string })
  const links = useQuery<LinkRow[]>({
    queryKey: ['calendar', 'links', cid],
    queryFn: ({ signal }) => api.get<LinkRow[]>(`/conversations/${cid}/links`, undefined, signal),
    staleTime: 30_000,
    retry: false,
  })
  const rows = (links.data ?? []).slice(0, 8)
  const eventRows = rows.filter((r) => r.kind === 'event')
  const events = useQueries({
    queries: eventRows.map((r) => ({
      queryKey: ['calendar', 'event', r.uid, null] as const,
      queryFn: async ({ signal }: { signal: AbortSignal }) => {
        try {
          return await api.get<ApiEvent>(`/calendar/events/${encodeURIComponent(r.uid)}`, undefined, signal)
        } catch {
          return null
        }
      },
      staleTime: 30_000,
    })),
  })
  const tasks = useTaskStatus(cid, rows.some((r) => r.kind === 'task'))
  if (!rows.length) return null
  const Tag = inline ? 'span' : 'div'

  return (
    <Tag className="cl no-print" role="list" aria-label={tr('conversation.conversationLinks.linkedEventsAndTasks')}>
      {rows.map((r) => {
        if (r.kind === 'event') {
          const ev = events[eventRows.indexOf(r)]?.data
          const gone = ev === null
          const cancelled = (ev?.status ?? '').toUpperCase() === 'CANCELLED'
          const status = gone ? tr('common.deleted') : cancelled ? tr('common.cancelled') : ev ? (ev.rrule ? summarizeRRule(ev.rrule, ev.start.slice(0, 10)) : relWhen(ev.start, prefs)) : ''
          const past = !!ev && !ev.rrule && new Date(ev.end ?? ev.start).getTime() < Date.now()
          const title = ev?.summary || r.summary || tr('conversation.conversationLinks.noTitle')
          const body = (
            <>
              <CalendarDays size={15} aria-hidden style={{ color: ev?.color ?? ev?.calendar_color ?? undefined }} />
              <span className="cl-title" data-chip-main>{title}</span>
              {status && <span className="cl-status" data-chip-extra>{status}</span>}
            </>
          )
          // data-chip-fold: on a narrow subject line the status gives way, never the title (hooks/useChipFit).
          const tip = status ? `${title} · ${status}` : title
          return gone ? (
            <span key={r.id} role="listitem" className="cl-chip gone" title={tip} data-chip-fold>{body}</span>
          ) : (
            <Link key={r.id} role="listitem" className={`cl-chip${cancelled || past ? ' past' : ''}`} title={tip} data-chip-fold to={eventPath(r.uid, ev?.occurrence ?? ev?.start ?? '')}>{body}</Link>
          )
        }
        const t = tasks.get(r.uid)
        const done = !!t && (t.status === 'completed' || t.completed)
        const title = t?.summary || r.summary
        const status = done ? 'Done ✓' : t?.due ? tr('conversation.conversationLinks.due', { relWhen: relWhen(t.due, prefs) }) : t ? tr('common.open') : ''
        return (
          <Link key={r.id} role="listitem" className={`cl-chip${done ? ' done' : ''}`} title={status ? `${title} · ${status}` : title} data-chip-fold to={taskPath(r.uid)}>
            {done ? <CheckCircle2 size={15} aria-hidden /> : <Circle size={15} aria-hidden />}
            <span className="cl-title" data-chip-main>{title}</span>
            <span className="cl-status" data-chip-extra>{status}</span>
          </Link>
        )
      })}
    </Tag>
  )
}
