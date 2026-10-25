// ACT mail.archive: Archive on a nudged follow-up in "Mail waiting on a reply".
// Today: the first view of Calendar. One calm page with what the day
// holds: a day line (the hours, today's events on it, and now), the agenda, the tasks due (a
// tick completes one, with Undo), mail waiting on a reply, requests waiting for a decision, and
// missed reminders. Every item acts in place. A section shows only when it has something, except
// the agenda and the tasks, which say so when they are empty.
//
// Data: the day's events are the calendar's own range (CalendarApp), tasks come from GET /tasks,
// reminders and approvals from the hooks the bell uses (one cache, so acting here updates the bell),
// and mail waiting on a reply from GET /today.
//
// ACT calendar.today_view @web.calendar

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Clock, Mail, Moon, Sun, Sunrise, Sunset } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { useMe } from '../../api/hooks'
import { addDaysISO, formatDate, formatHour, formatTime, minutesOfDay, type ISODate } from '../../lib/dates'
import { formatRelative } from '../../lib/format'
import { taskPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { toast, toastError } from '../../stores/ui'
import { ApprovalsSection } from '../approvals/ApprovalsSection'
import { useApprovals } from '../approvals/approvals'
import { useActiveReminders, useReminderUI } from '../reminders/api'
import { RemindersSection } from '../reminders/RemindersSection'
import { displayColour } from './colour'
import { useCal } from './context'
import { isCancelled, isDeclined } from './eventInfo'
import { EventRow } from './ScheduleView'
import { dayPart, greeting, lineSpan, summary, type DayPart } from './today'
import type { ApiTask, CalEvent } from './types'
import './today.css'
import { t as tr } from '../../i18n'

interface FollowUp {
  conversation_id: number
  subject: string
  who: string
  since: string | null
  due_at: string | null
  nudged: boolean
}

const PART_ICON: Record<DayPart, typeof Sun> = { night: Moon, morning: Sunrise, afternoon: Sun, evening: Sunset }

/** Skeletons wait this long, so a quick load never flashes one. */
const SKELETON_DELAY = 300

function useDelayed(on: boolean): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!on) {
      setShown(false)
      return
    }
    const t = setTimeout(() => setShown(true), SKELETON_DELAY)
    return () => clearTimeout(t)
  }, [on])
  return shown
}

export function TodayView({ loading }: { loading: boolean }) {
  const cal = useCal()
  const me = useMe().data
  const date = cal.date
  const isToday = date === cal.today
  const events = useMemo(() => cal.events
    .filter((e) => e.firstDay <= date && e.lastDay >= date && !isCancelled(e) && !isDeclined(e))
    .sort((a, b) => Number(b.allDay) - Number(a.allDay) || a.startAt.getTime() - b.startAt.getTime()), [cal.events, date])

  const tasksQ = useQuery<ApiTask[]>({
    queryKey: ['tasks', 'today-view', date, isToday],
    queryFn: async ({ signal }) => {
      // Today: due today or earlier (overdue). Another day: due that day.
      const r = await api.get<{ items: ApiTask[] }>('/tasks', {
        status: 'open', has_due: 1, due_before: addDaysISO(date, 1), ...(isToday ? {} : { due_after: date }), limit: 100,
      }, signal)
      return r.items ?? []
    },
    staleTime: 30_000,
  })
  const waitingQ = useQuery<FollowUp[]>({
    queryKey: ['today', date],
    queryFn: async ({ signal }) => (await api.get<{ followups: FollowUp[] }>('/today', { date }, signal)).followups ?? [],
    staleTime: 30_000,
  })
  const missed = (useActiveReminders().data ?? []).filter((r) => r.missed)
  const approvals = useApprovals('pending').data ?? []
  const tasks = tasksQ.data ?? []
  const waiting = waitingQ.data ?? []

  const hour = Number(formatTime(cal.now, { ...cal.prefs, hour12: false }).slice(0, 2))
  const Part = PART_ICON[dayPart(hour)]
  const first = (me?.user?.name ?? '').split(' ')[0]
  const counts = { events: events.length, tasks: tasks.length, followups: isToday ? waiting.length : 0, approvals: isToday ? approvals.length : 0, reminders: isToday ? missed.length : 0 }
  const busy = loading || tasksQ.isLoading
  const skeleton = useDelayed(busy)

  return (
    <div className="today-view" data-testid="cal-today">
      <header className="today-head">
        <div className="today-date" aria-hidden>
          <span className="today-num">{Number(date.slice(8))}</span>
          <span className="today-wd">{formatDate(date, 'weekdayLong')}<br />{formatDate(date, 'monthYear')}</span>
        </div>
        <div className="today-hello">
          {isToday ? (
            <h2><Part className={`today-part ${dayPart(hour)}`} aria-hidden />{greeting(hour)}{first ? `, ${first}` : ''}</h2>
          ) : (
            <h2>{formatDate(date, 'long')}</h2>
          )}
          <p className="today-summary" aria-live="polite">{busy ? ' ' : summary(counts, isToday)}</p>
        </div>
      </header>
      <DayLine events={events.filter((e) => !e.allDay)} isToday={isToday} />
      <div className="today-cols">
        <section className="today-sec today-agenda" aria-label={tr('calendar.todayView.agenda')}>
          <h3>{tr('calendar.todayView.agenda')}</h3>
          {skeleton && !events.length ? <Rows n={3} /> : events.length ? (
            <div className="today-rows" role="list">
              {/* Each row is a button, so it sits in a listitem (a list holds only listitems). */}
              {events.map((e) => <div key={e.key} role="listitem"><EventRow e={e} day={date} /></div>)}
            </div>
          ) : !busy ? (
            <p className="today-empty">{tr('calendar.todayView.noEvents', { value: isToday ? ' today' : '' })}{' '}<button type="button" className="btn text sm" onClick={() => cal.go('week', date)}>{tr('calendar.todayView.seeTheWeek')}</button></p>
          ) : <div className="today-reserve" />}
        </section>
        <div className="today-side">
          <section className="today-sec today-tasks" aria-label={tr('calendar.todayView.tasksDue')}>
            <h3>{tr('calendar.todayView.tasksDue')}</h3>
            {skeleton && tasksQ.isLoading ? <Rows n={2} /> : tasks.length ? (
              <ul className="today-list">{tasks.map((t) => <TaskRow key={t.uid} t={t} date={date} />)}</ul>
            ) : !tasksQ.isLoading ? <p className="today-empty">{tr('calendar.todayView.nothingDue', { value: isToday ? ' today' : '' })}</p> : <div className="today-reserve" />}
          </section>
          {isToday && waiting.length > 0 && (
            <section className="today-sec today-waiting" aria-label={tr('calendar.todayView.waitingOnAReply')}>
              <h3>{tr('calendar.todayView.waitingOnAReply')}</h3>
              <ul className="today-list">{waiting.map((f) => <WaitingRow key={f.conversation_id} f={f} />)}</ul>
            </section>
          )}
          {/* Approvals and reminders wait for the replies list, so nothing moves under them. */}
          {isToday && approvals.length > 0 && !waitingQ.isLoading && (
            <div className="today-sec today-approvals">
              <ApprovalsSection target={null} />
            </div>
          )}
          {isToday && missed.length > 0 && !waitingQ.isLoading && (
            <div className="today-sec today-missed">
              <RemindersSection items={missed} onOpen={(key) => useReminderUI.getState().set({ sheet: key, sheetMode: null })} onLeave={() => undefined} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Rows({ n }: { n: number }) {
  return (
    <div className="today-skel" aria-busy="true" aria-label={tr('common.loading')}>
      {Array.from({ length: n }, (_, i) => <span key={i} className="skeleton" />)}
    </div>
  )
}

/** The day's hours as one line: the events on it in their colours, and now. */
function DayLine({ events, isToday }: { events: CalEvent[]; isToday: boolean }) {
  const cal = useCal()
  const zone = cal.prefs.zone
  const mins = events.map((e) => ({
    e,
    start: e.firstDay < cal.date ? 0 : minutesOfDay(e.startAt, zone),
    end: e.lastDay > cal.date ? 1440 : minutesOfDay(e.endAt, zone) || 1440,
  }))
  const span = lineSpan({ start: cal.prefs.workStart, end: cal.prefs.workEnd }, mins)
  const pct = (m: number) => `${((Math.min(span.end, Math.max(span.start, m)) - span.start) / (span.end - span.start)) * 100}%`
  const now = minutesOfDay(cal.now, zone)
  const step = span.end - span.start > 12 * 60 ? 3 : 2
  const hours: number[] = []
  for (let h = Math.ceil(span.start / 60); h * 60 <= span.end; h += step) hours.push(h)
  return (
    <div className="today-line" aria-hidden data-testid="today-line">
      <div className="today-track">
        <div className="today-work" style={{ insetInlineStart: pct(cal.prefs.workStart), width: `calc(${pct(cal.prefs.workEnd)} - ${pct(cal.prefs.workStart)})` }} />
        {mins.map(({ e, start, end }) => (
          <button
            key={e.key}
            type="button"
            tabIndex={-1}
            className={`today-block${isToday && end <= now ? ' past' : ''}`}
            style={{ insetInlineStart: pct(start), width: `max(6px, calc(${pct(end)} - ${pct(start)}))`, background: displayColour(e.colour, cal.dark) }}
            title={`${e.summary || tr('common.noTitle')} ${formatTime(e.startAt, cal.prefs)}`}
            onClick={(ev) => cal.openEvent(e, ev.currentTarget)}
          />
        ))}
        {isToday && now >= span.start && now <= span.end && <div className="today-now" style={{ insetInlineStart: pct(now) }} />}
      </div>
      <div className="today-hours">
        {hours.map((h) => <span key={h} style={{ insetInlineStart: pct(h * 60) }}>{formatHour(h % 24, cal.prefs.hour12)}</span>)}
      </div>
    </div>
  )
}

/** A task due: the tick completes it (ACT task.complete @web.calendar), with Undo. */
function TaskRow({ t, date }: { t: ApiTask; date: ISODate }) {
  const cal = useCal()
  const qc = useQueryClient()
  const nav = useNavigate()
  const [done, setDone] = useState(false)
  const due = t.due ?? ''
  const timed = due.length > 10
  const overdue = timed ? new Date(due) < cal.now && date === cal.today : due < date
  const when = timed ? formatTime(new Date(due), cal.prefs) : due < date ? formatDate(due, 'short') : ''
  const refresh = () => void qc.invalidateQueries({ queryKey: ['tasks'] })
  const complete = async () => {
    if (cal.readOnly) return toast({ message: tr('calendar.todayView.offlineTasksCanBeCompleted'), error: true })
    setDone(true)
    try {
      const out = await api.patch<ApiTask>(`/tasks/${encodeURIComponent(t.uid)}`, { completed: true, ...(t.etag ? { etag: t.etag } : {}) })
      refresh()
      toast({
        message: tr('common.taskCompleted'), actionLabel: tr('common.undo'), duration: 7000,
        onAction: () => void api.patch(`/tasks/${encodeURIComponent(t.uid)}`, { completed: false, ...(out?.etag ? { etag: out.etag } : {}) })
          .then(refresh, (e) => toastError(e, tr('common.couldNotUndo'))),
      })
    } catch (e) {
      setDone(false)
      toastError(e, tr('calendar.todayView.taskNotCompleted'))
    }
  }
  return (
    <li className={`today-task${done ? ' done' : ''}`}>
      <button type="button" className="today-tick" role="checkbox" aria-checked={done} aria-label={tr('calendar.todayView.complete', { summary: t.summary })} onClick={() => void complete()} disabled={done}>
        {done && <Check size={14} aria-hidden />}
      </button>
      <button type="button" className="today-item-main" onClick={() => nav(taskPath(t.uid))}>
        <span className="today-item-title" dir="auto">{t.summary || tr('common.noTitle')}</span>
        {(when || overdue) && <span className={`today-item-meta${overdue ? ' overdue' : ''}`}>{overdue ? tr('calendar.todayView.overdue', { value: when ? ` · ${when}` : '' }) : when}</span>}
      </button>
    </li>
  )
}

/** A conversation waiting on a reply: open it, wait three more days, or stop waiting. */
function WaitingRow({ f }: { f: FollowUp }) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const run = async (action: string, extra: Record<string, unknown>, message: string) => {
    try {
      // ACT mail.follow_up @web.calendar
      const res = await api.post<{ undo?: Record<string, unknown> | null }>('/conversations/actions', { ids: [f.conversation_id], action, ...extra })
      void qc.invalidateQueries({ queryKey: ['today'] })
      void qc.invalidateQueries({ queryKey: ['conversations'] })
      const undo = res?.undo
      toast({
        message, actionLabel: undo ? tr('common.undo') : undefined, duration: 7000,
        onAction: undo ? () => void api.post('/conversations/actions', { ids: [f.conversation_id], ...undo })
          .then(() => qc.invalidateQueries({ queryKey: ['today'] }), (e) => toastError(e, tr('common.couldNotUndo'))) : undefined,
      })
    } catch (e) {
      toastError(e, tr('common.notChanged'))
    }
  }
  const later = new Date(Date.now() + 3 * 86_400_000).toISOString()
  return (
    <li className="today-wait">
      <span className="today-wait-icon" aria-hidden><Mail size={16} /></span>
      <button type="button" className="today-item-main" onClick={() => nav(mailPath('all', f.conversation_id))}>
        <span className="today-item-title" dir="auto">{f.subject || tr('common.noSubject')}</span>
        <span className="today-item-meta">
          {f.nudged ? tr('calendar.todayView.noReply') : tr('calendar.todayView.replyDue')}{f.who ? tr('calendar.todayView.from', { who: f.who }) : ''}{f.since ? tr('calendar.todayView.sent', { relative: formatRelative(f.since) }) : ''}
        </span>
      </button>
      <span className="today-wait-acts">
        <button type="button" className="icon-btn sm" aria-label={tr('calendar.todayView.remindMeIn3Days')} data-tip={tr('calendar.todayView.remindMeIn3Days')}
          onClick={() => void run('follow_up', { until: later }, 'You will be reminded in 3 days')}>
          <Clock size={16} />
        </button>
        <button type="button" className="btn text sm" onClick={() => void run(f.nudged ? 'archive' : 'cancel_follow_up', {}, f.nudged ? 'Archived' : 'Stopped waiting')}>
          {f.nudged ? tr('common.archive') : tr('calendar.todayView.stopWaiting')}
        </button>
      </span>
    </li>
  )
}
