// The calendar side panel (web.panel): a quick glance at one day from anywhere in
// mail, with the Calendar app's rows and detail, the quick-add line for a new event, and "Open
// Calendar" in the panel header. Edit opens the app's editor over the page. The eye button shows
// or hides calendars here and in the app alike.
// ACT event.create @web.panel, ACT event.edit @web.panel, ACT event.rsvp @web.panel, ACT event.open_email @web.panel, ACT calendar.toggle_visibility @web.panel

import { CalendarPlus, ChevronLeft, ChevronRight, Eye } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { EmptyState, Skeleton } from '../../components/bits'
import { Popover } from '../../components/Popover'
import { addDaysISO, formatDate, startOfDay, todayISO, type ISODate } from '../../lib/dates'
import type { QuickResult } from '../../lib/quickadd'
import { useCal } from '../calendar/context'
import { displayColour } from '../calendar/colour'
import { useCollections } from '../calendar/data'
import { useVisibility } from '../calendar/LeftColumn'
import { EventDetail } from '../calendar/EventDetail'
import { useCalPrefs } from '../calendar/prefs'
import { EventRow } from '../calendar/ScheduleView'
import { useCalUI } from '../calendar/store'
import type { CalEvent } from '../calendar/types'
import { NextMeetingLastTime } from '../speakr/LastTime'
import { CalHost, CalOverlays } from './CalHost'
import { QuickLine } from './QuickLine'
import { quickEventBody, type QuickCtx } from './quickCreate'
import './panels.css'
import { t as tr } from '../../i18n'

export function CalendarPanel() {
  const prefs = useCalPrefs()
  const [day, setDay] = useState<ISODate>(() => todayISO(prefs.zone))
  const [detail, setDetail] = useState<{ e: CalEvent; rect: DOMRect | null } | null>(null)
  const from = useMemo(() => startOfDay(day, prefs.zone), [day, prefs.zone])
  const to = useMemo(() => startOfDay(addDaysISO(day, 1), prefs.zone), [day, prefs.zone])
  const onOpen = useCallback((e: CalEvent, anchor?: Element | null) => setDetail({ e, rect: anchor ? anchor.getBoundingClientRect() : null }), [])
  return (
    <CalHost from={from} to={to} date={day} onOpen={onOpen}>
      <PanelBody day={day} setDay={setDay} />
      {detail && <PanelDetail k={detail.e.key} fallback={detail.e} rect={detail.rect} onClose={() => setDetail(null)} />}
      <CalOverlays />
    </CalHost>
  )
}

/** The app's event detail at the row; it follows the event while it changes. */
function PanelDetail({ k, fallback, rect, onClose }: { k: string; fallback: CalEvent; rect: DOMRect | null; onClose: () => void }) {
  const cal = useCal()
  const e = cal.events.find((x) => x.key === k) ?? fallback
  return <EventDetail e={e} mode={cal.narrow ? 'sheet' : rect ? 'popover' : 'card'} anchor={rect} onClose={onClose} />
}

function PanelBody({ day, setDay }: { day: ISODate; setDay: (d: ISODate) => void }) {
  const cal = useCal()
  const cols = useCollections()
  const [saving, setSaving] = useState(false)
  const isToday = day === cal.today
  const events = cal.events.filter((e) => e.firstDay <= day && e.lastDay >= day)
  const allDay = events.filter((e) => e.allDay || e.firstDay !== e.lastDay)
  const timed = events.filter((e) => !allDay.includes(e))
  // ACT event.last_recording @web.panel: today's next meeting (or the one going on) shows "Last time" from Speakr.
  const next = isToday ? timed.find((e) => e.endAt.getTime() > cal.now.getTime() && e.status !== 'CANCELLED') : undefined
  const writable = useMemo(() => cal.collections.filter((c) => c.events !== false && !c.read_only && !c.error), [cal.collections])
  const qctx: QuickCtx = useMemo(() => ({
    prefs: cal.prefs, now: cal.now, defaultDuration: cal.prefs.defaultDuration, morningHour: cal.prefs.morningHour, videoTemplate: cal.prefs.videoTemplate,
    calendars: writable.map((c) => ({ id: c.url ?? c.id, name: c.name })),
  }), [cal.prefs, cal.now, writable])

  const save = async (r: QuickResult, editor: boolean) => {
    const body = quickEventBody(r, qctx)
    // A line without a day lands on the day the panel shows (at the typed time, else 9:00).
    const typedDay = r.tokens.some((t) => t.kind === 'date' || t.kind === 'span' || t.kind === 'repeat')
    if (!typedDay && !isToday && !body.all_day) {
      const timeTyped = r.start ? Number(r.start.slice(11, 13)) * 60 + Number(r.start.slice(14, 16)) : 9 * 60
      const s = new Date(startOfDay(day, cal.prefs.zone).getTime() + timeTyped * 60_000)
      const len = new Date(body.end!).getTime() - new Date(body.start!).getTime()
      body.start = s.toISOString()
      body.end = new Date(s.getTime() + len).toISOString()
    }
    if (editor) {
      const guests = r.guests.filter((g) => g.email).map((g) => ({ email: g.email! }))
      useCalUI.getState().set({
        editor: {
          mode: 'new',
          seed: {
            summary: body.summary, start: body.all_day ? body.start! : new Date(body.start!), end: body.all_day ? body.end ?? body.start! : new Date(body.end ?? body.start!),
            allDay: !!body.all_day, location: body.location ?? '', rrule: body.recurrence ?? null, guests, video: r.video, ...(body.calendar ? { calendar: body.calendar } : {}),
          },
        },
      })
      return true
    }
    setSaving(true)
    try {
      return !!(await cal.actions.create(body))
    } finally {
      setSaving(false)
    }
  }

  const noCalendar = cols.isSuccess && !(cols.data ?? []).some((c) => c.events !== false && !c.error)
  return (
    <div className="agenda cp-panel" data-testid="calendar-panel">
      <div className="agenda-day">
        <div className="cp-day-title">
          <h3>{isToday ? tr('common.today') : formatDate(day, 'weekdayLong')}</h3>
          <span className="muted cp-day-date">{formatDate(day, 'short')}</span>
        </div>
        <button type="button" className="icon-btn sm" aria-label={tr('rail.calendarPanel.previousDay')} data-tip={tr('rail.calendarPanel.previousDay')} onClick={() => setDay(addDaysISO(day, -1))}>
          <ChevronLeft size={18} />
        </button>
        <button type="button" className="icon-btn sm" aria-label={tr('rail.calendarPanel.nextDay')} data-tip={tr('rail.calendarPanel.nextDay')} onClick={() => setDay(addDaysISO(day, 1))}>
          <ChevronRight size={18} />
        </button>
        {/* Always in the row, hidden on today itself: the arrows stay where they are, so repeated
            clicks keep landing on them. */}
        <button type="button" className={`btn text sm cp-today${isToday ? ' is-hidden' : ''}`} onClick={() => setDay(cal.today)}
          disabled={isToday} aria-hidden={isToday || undefined} tabIndex={isToday ? -1 : undefined}>{tr('common.today')}</button>
        <CalendarsMenu />
      </div>
      {cols.isError || noCalendar ? (
        <EmptyState icon={<CalendarPlus />} title={tr('rail.calendarPanel.noCalendarConnected')}>
          <p>{tr('rail.calendarPanel.connectACaldavCalendarTo')}</p>
          <Link className="btn tonal" to="/settings/calendar?add=1">{tr('rail.calendarPanel.connectCalendar')}</Link>
        </EmptyState>
      ) : (
        <>
          <QuickLine ctx={qctx} busy={saving} placeholder={isToday ? tr('rail.calendarPanel.addAnEventLunchWith') : tr('rail.calendarPanel.addAnEventOn', { date: formatDate(day, 'day') })} onSave={save} />
          {next && <NextMeetingLastTime uid={next.uid} occurrence={next.occurrence} title={next.summary} start={next.startAt} />}
          {cal.loading && !events.length ? (
            <div style={{ display: 'grid', gap: 8, marginTop: 8 }}><Skeleton height={44} /><Skeleton height={44} /></div>
          ) : events.length === 0 ? (
            <p className="muted cp-empty">{tr('rail.calendarPanel.nothing', { value: isToday ? 'today' : 'on this day' })}</p>
          ) : (
            <div className="cp-rows" role="list" aria-label={tr('rail.calendarPanel.eventsOn', { date: formatDate(day, 'day') })}>
              {allDay.map((e) => <EventRow key={e.key} e={e} day={day} />)}
              {timed.map((e) => <EventRow key={e.key} e={e} day={day} />)}
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** ACT calendar.toggle_visibility: which calendars the panel (and the app) show. */
function CalendarsMenu() {
  const cal = useCal()
  const vis = useVisibility()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const cals = cal.collections.filter((c) => c.events !== false && !c.error)
  if (cals.length < 1) return null
  const hidden = cals.filter((c) => cal.hidden.has(c.url ?? c.id)).length
  return (
    <>
      <button type="button" className="icon-btn sm cp-cals" aria-label={tr('rail.calendarPanel.calendarsShown')} data-tip={hidden ? tr('rail.calendarPanel.hidden', { hidden }) : tr('rail.calendarPanel.calendarsShown')} aria-haspopup="dialog" onClick={(e) => setAnchor(e.currentTarget)}>
        <Eye size={18} />{hidden > 0 && <span className="cp-cals-dot" aria-hidden />}
      </button>
      <Popover anchor={anchor} open={!!anchor} onClose={() => setAnchor(null)} role="dialog" label={tr('rail.calendarPanel.calendarsShown')} className="cp-cals-pop">
        <div className="cp-cals-list">
          {cals.map((c) => {
            const url = c.url ?? c.id
            return (
              <label key={url} className="cp-cal">
                <input type="checkbox" className="cal-swatch-box" style={{ '--sw': displayColour(cal.colourOf(c), cal.dark) } as React.CSSProperties}
                  checked={!cal.hidden.has(url)} aria-label={tr('rail.calendarPanel.show', { itemName: c.name })} onChange={() => void vis.toggle(c)} />
                <span>{c.name}</span>
              </label>
            )
          })}
          {hidden > 0 && <button type="button" className="btn text sm" onClick={() => vis.showAll()}>{tr('common.showAll')}</button>}
        </div>
      </Popover>
    </>
  )
}
