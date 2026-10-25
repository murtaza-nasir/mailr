// Schedule and search results: a list grouped by day with
// sticky date headers, only the days that have items, and a "Now" marker. Schedule scrolls both
// ways without end: it loads 30 days at a time forwards, and earlier days when scrolled to the top.

import { Link2, Mail, Repeat, Search, Users, Video } from 'lucide-react'
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQueries, type UseQueryResult } from '@tanstack/react-query'
import { addDaysISO, diffDaysISO, formatDate, formatTime, startOfDay, utcIsoformat, type ISODate } from '../../lib/dates'
import { qk } from '../../api/hooks'
import { ItemBoundary, Spinner } from '../../components/bits'
import { displayColour } from './colour'
import { useCal } from './context'
import { fetchRange, useNormalized, type RangeData } from './data'
import { conferenceLink, isCancelled, isDeclined, needsReply, RSVP_LABEL, titleOf } from './eventInfo'
import { useCalUI } from './store'
import type { ApiEvent, CalEvent } from './types'
import { t as tr } from '../../i18n'

const PAGE = 30

/** "All day", "Day 2 of 3", "3:00 – 4:00 PM", "Until 11:00 AM". */
export function rowTime(e: CalEvent, day: ISODate, p: ReturnType<typeof useCal>['prefs']): string {
  const multi = e.firstDay !== e.lastDay
  if (e.allDay) return multi ? tr('calendar.scheduleView.dayOf', { value: diffDaysISO(e.firstDay, day) + 1, value2: diffDaysISO(e.firstDay, e.lastDay) + 1 }) : tr('common.allDay')
  if (multi && day !== e.firstDay && day !== e.lastDay) return tr('common.allDay')
  if (multi && day === e.lastDay && day !== e.firstDay) return tr('calendar.scheduleView.until', { time: formatTime(e.endAt, p) })
  if (multi && day === e.firstDay) return tr('calendar.scheduleView.from', { time: formatTime(e.startAt, p) })
  return `${formatTime(e.startAt, p, { compact: true })} – ${formatTime(e.endAt, p, { compact: true })}`
}

function highlight(text: string, terms: string[]): ReactNode {
  if (!terms.length || !text) return text
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig')
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : <Fragment key={i}>{part}</Fragment>))
}

/** The one Tab stop into a list of events: the focused row, else the first from today on,
 *  else the first. Arrows move between the rows; Tab leaves the list. */
function useListTabStop(rows: { key: string; day: ISODate }[], today: ISODate): string | null {
  const focus = useCalUI((s) => s.focus)
  if (focus && rows.some((r) => r.key === focus)) return focus
  return (rows.find((r) => r.day >= today) ?? rows[0])?.key ?? null
}

type EventRowProps = { e: CalEvent; day: ISODate; terms?: string[]; showDate?: boolean; tabStop?: string | null }

/** One event in a list; a failure shows "Couldn't show this event" in its place. */
export function EventRow(props: EventRowProps) {
  return (
    <ItemBoundary what="event" compact>
      <EventRowBody {...props} />
    </ItemBoundary>
  )
}

function EventRowBody({ e, day, terms = [], showDate, tabStop }: EventRowProps) {
  const cal = useCal()
  const focus = useCalUI((s) => s.focus)
  const colour = displayColour(e.colour, cal.dark)
  const status = e.my_status ? RSVP_LABEL[e.my_status.toUpperCase()] : null
  return (
    <div
      className={`cal-row${isCancelled(e) ? ' cancelled' : ''}${isDeclined(e) ? ' declined' : ''}${focus === e.key ? ' focused' : ''}${cal.prefs.dimPast && e.endAt < cal.now ? ' past' : ''}`}
      role="button"
      tabIndex={(tabStop !== undefined ? tabStop === e.key : focus === e.key) ? 0 : -1}
      data-key={e.key}
      data-testid="cal-event"
      data-shortcuts=""
      onClick={(ev) => cal.openEvent(e, ev.currentTarget)}
      onFocus={() => useCalUI.getState().set({ focus: e.key })}
      onKeyDown={(ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault()
          cal.openEvent(e, ev.currentTarget)
        }
      }}
    >
      <span className={`cal-row-bar${needsReply(e) ? ' hollow' : ''}`} style={{ background: needsReply(e) ? 'transparent' : colour, borderColor: colour }} />
      <span className="cal-row-time">{showDate ? `${formatDate(day, 'dayYear')} · ` : ''}{rowTime(e, day, cal.prefs)}</span>
      <span className="cal-row-main">
        <span className="cal-row-title" dir="auto">{highlight(titleOf(e), terms)}</span>
        {e.location && <span className="cal-row-loc" dir="auto">{highlight(e.location, terms)}</span>}
      </span>
      <span className="cal-row-meta" aria-hidden>
        {conferenceLink(e) && <Video />}
        {e.recurring && <Repeat />}
        {(e.attendee_list?.length ?? 0) > 0 && <Users />}
        {e.conversation_id && <Mail />}
        {e.url && !conferenceLink(e) && <Link2 />}
      </span>
      {status && <span className={`cal-row-rsvp ${e.my_status?.toLowerCase()}`}>{status}</span>}
    </div>
  )
}

/** Rows grouped by day: each event listed on every day it covers. */
export function groupByDay(events: CalEvent[], from: ISODate, to: ISODate): [ISODate, CalEvent[]][] {
  const map = new Map<ISODate, CalEvent[]>()
  for (const e of events) {
    let d = e.firstDay < from ? from : e.firstDay
    const last = e.lastDay > to ? to : e.lastDay
    for (let i = 0; d <= last && i < 400; i++, d = addDaysISO(d, 1)) map.set(d, [...(map.get(d) ?? []), e])
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]))
}

function DayGroups({ events, from, to, terms, nowMarker }: { events: CalEvent[]; from: ISODate; to: ISODate; terms?: string[]; nowMarker?: boolean }) {
  const cal = useCal()
  const groups = groupByDay(events, from, to)
  const tabStop = useListTabStop(groups.flatMap(([day, list]) => list.map((e) => ({ key: e.key, day }))), cal.today)
  let nowShown = !nowMarker
  return (
    <>
      {groups.map(([day, list]) => {
        const out: ReactNode[] = []
        if (!nowShown && day >= cal.today) {
          nowShown = true
          if (day > cal.today) out.push(<NowMarker key="now" />)
        }
        out.push(
          <section key={day} className={`cal-sday${day === cal.today ? ' today' : ''}${day < cal.today ? ' past' : ''}`} aria-label={formatDate(day, 'long')}>
            <h3 className="cal-sday-head">
              <button type="button" onClick={() => cal.go('day', day)}>
                <span className="cal-sday-num">{Number(day.slice(8))}</span>
                <span className="cal-sday-wd">{formatDate(day, 'weekday')}</span>
                <span className="cal-sday-month">{formatDate(day, 'monthYear')}</span>
              </button>
            </h3>
            <div className="cal-sday-rows">
              {list.map((e, i) => (
                <Fragment key={e.key}>
                  {day === cal.today && nowMarker && i === list.findIndex((x) => x.startAt > cal.now) && <NowMarker />}
                  <EventRow e={e} day={day} terms={terms} tabStop={tabStop} />
                </Fragment>
              ))}
            </div>
          </section>,
        )
        return out
      })}
    </>
  )
}

function NowMarker() {
  const cal = useCal()
  return (
    <div className="cal-snow" role="separator" aria-label={tr('calendar.scheduleView.now')}>
      <span>{tr('calendar.scheduleView.now2', { time: formatTime(cal.now, cal.prefs) })}</span>
    </div>
  )
}

/** The schedule's pages as one list. An event over two pages comes in both: once by its occurrence. */
function combinePages(pages: UseQueryResult<RangeData>[]) {
  const seen = new Set<string>()
  const events: ApiEvent[] = []
  for (const p of pages) for (const e of p.data?.events ?? []) {
    const k = `${e.uid}|${e.occurrence ?? e.recurrence_id ?? e.start}`
    if (seen.has(k)) continue
    seen.add(k)
    events.push(e)
  }
  return { events, isFetching: pages.some((p) => p.isFetching), isLoading: pages.some((p) => p.isLoading) && !events.length, isPlaceholderData: false }
}

export function ScheduleView() {
  const cal = useCal()
  const [start, setStart] = useState(() => cal.date)
  const [days, setDays] = useState(PAGE)
  useEffect(() => {
    setStart(cal.date)
    setDays(PAGE)
  }, [cal.date])
  // The list loads in pages of PAGE days, each its own range query: scrolling on fetches the next
  // page only (one query over the whole growing range fetched every earlier day again).
  const zone = cal.prefs.zone
  const end = addDaysISO(start, days)
  const chunks = useMemo(() => Array.from({ length: Math.ceil(days / PAGE) }, (_, i) => {
    const a = addDaysISO(start, i * PAGE)
    return [utcIsoformat(startOfDay(a, zone)), utcIsoformat(startOfDay(addDaysISO(a, PAGE), zone))] as const
  }), [start, days, zone])
  const q = useQueries({
    queries: chunks.map(([f, t]) => ({
      queryKey: qk.calendar.range(f, t),
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchRange(f, t, signal),
      staleTime: 15_000,
    })),
    // One list for the pages, kept while they are unchanged (a stable combine).
    combine: combinePages,
  })
  const events = useNormalized(q.events, zone, cal.collections, cal.hidden)
  const scroller = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const anchor = useRef<{ h: number; top: number } | null>(null)

  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver((entries) => {
      if (entries.some((x) => x.isIntersecting) && !q.isFetching) setDays((n) => Math.min(n + PAGE, 330))
    }, { root: scroller.current, rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [q.isFetching])

  // Earlier days keep the reader's place: the scroll moves by the height added above.
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && anchor.current) {
      el.scrollTop = anchor.current.top + (el.scrollHeight - anchor.current.h)
      anchor.current = null
    }
  }, [events])

  const earlier = () => {
    const el = scroller.current
    if (el) anchor.current = { h: el.scrollHeight, top: el.scrollTop }
    setStart((s) => addDaysISO(s, -PAGE))
    setDays((n) => Math.min(n + PAGE, 330))
  }

  const empty = !q.isLoading && events.length === 0
  return (
    <div className={`cal-schedule${q.isPlaceholderData ? ' is-loading' : ''}`} ref={scroller} data-testid="cal-schedule" role="list" onScroll={(e) => { if (e.currentTarget.scrollTop === 0 && !q.isFetching && events.length) earlier() }}>
      <div className="cal-sched-top">
        <button type="button" className="btn text sm" onClick={earlier}>{tr('calendar.scheduleView.showEarlier')}</button>
      </div>
      {q.isLoading && <div className="cal-sched-loading"><Spinner /></div>}
      {empty ? (
        <div className="cal-empty">
          <p>{tr('calendar.scheduleView.nothingScheduledInTheNext', { count: diffDaysISO(start, end) })}</p>
          <button type="button" className="btn tonal" onClick={() => useCalUI.getState().set({ quick: { start: null, end: null, allDay: false, anchor: null } })}>{tr('common.create')}</button>
        </div>
      ) : (
        <DayGroups events={events} from={start} to={addDaysISO(end, -1)} nowMarker />
      )}
      <div ref={sentinel} className="cal-sched-end">{q.isFetching && !q.isLoading ? <Spinner small /> : null}</div>
    </div>
  )
}

// ---------- search results ----------

export function SearchResults({ q, items, loading, error }: { q: string; items: CalEvent[]; loading: boolean; error?: string | null }) {
  const cal = useCal()
  const terms = q.split(/\s+/).filter((t) => t && !t.includes(':')).map((t) => t.replace(/^"|"$/g, ''))
  const upcoming = items.filter((e) => e.endAt >= cal.now).sort((a, b) => a.startAt.getTime() - b.startAt.getTime())
  const past = items.filter((e) => e.endAt < cal.now).sort((a, b) => b.startAt.getTime() - a.startAt.getTime())
  const tabStop = useListTabStop([...upcoming, ...past].map((e) => ({ key: e.key, day: e.firstDay })), '')
  return (
    <div className="cal-schedule cal-search" data-testid="cal-search" role="list">
      <div className="cal-search-head">
        <Search aria-hidden />
        <span>{tr('calendar.scheduleView.resultsFor', { q })}</span>
        {loading && <Spinner small />}
      </div>
      {error && <p className="cal-note error">{error}</p>}
      {!loading && !items.length && !error && <div className="cal-empty"><p>{tr('calendar.scheduleView.noEventsMatch', { q })}</p></div>}
      {upcoming.length > 0 && (
        <section className="cal-search-group">
          <h2>{tr('common.upcoming')}</h2>
          {upcoming.map((e) => <EventRow key={e.key} e={e} day={e.firstDay} terms={terms} showDate tabStop={tabStop} />)}
        </section>
      )}
      {past.length > 0 && (
        <section className="cal-search-group">
          <h2>{tr('calendar.scheduleView.past')}</h2>
          {past.map((e) => <EventRow key={e.key} e={e} day={e.firstDay} terms={terms} showDate tabStop={tabStop} />)}
        </section>
      )}
    </div>
  )
}
