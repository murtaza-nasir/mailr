// The Calendar app (web.calendar). Contract with the shells
// (router.tsx): a props-free component rendered in each theme's main region for every /calendar/*
// URL. The URL holds the view and date (/calendar/week/2026-10-01), an open occurrence
// (/calendar/event/:uid/:occ, over the view it was opened from) and search (/calendar/search?q=).
// The header, left column, views, detail, quick create, editor and prompts share one context
// (context.ts); keys come through useAppShortcuts('calendar', …).

import { SubscribeButton } from './Subscriptions'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/react/shallow'
import { CloudOff, Eye, Globe, RefreshCw, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { NoCalendarConnected } from './BuiltinCalendarHint'
import { api } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { ItemBoundary, Skeleton } from '../../components/bits'
import { useAppRoute } from '../../hooks/useAppRoute'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useTheme } from '../../hooks/useTheme'
import { addDaysISO, formatDate, minutesOfDay, startOfDay, toISODate, todayISO, zoneAbbr, zoneOffset, type ISODate } from '../../lib/dates'
import { calendarPath, eventPath, type CalendarView } from '../../lib/routes'
import { useMe, useSettings } from '../../api/hooks'
import { TodayView } from './TodayView'
import { useThemeDef } from '../../themes/useUITheme'
import { toast, toastError } from '../../stores/ui'
import { useAppShortcuts } from '../shortcuts/useAppShortcuts'
import { busyOf, runLastUndo, toWrite, useCalActions } from './actions'
import { CalContext, type CalCtx } from './context'
import {
  calApi, collectionColour, isMissingRoute, normalizeEvent, useCollections, useNormalized, useRange, useSettledError, useSyncStatus, useTasksInRange,
} from './data'
import { EventDetail, type DetailMode } from './EventDetail'
import { EventEditor, type EditorMode } from './EventEditor'
import { parseDateText } from './fields'
import { FindTimeDialog } from './FindTime'
import { gridInstant, keyboardDrag, moveDays, periodTitle, shiftDate, viewDays, viewSpan, type ViewOptions } from './geometry'
import { focusSearch } from '../search/SearchBar'
import { Header } from './Header'
import { LeftColumn } from './LeftColumn'
import { saveVisibility, useHiddenCalendars } from './visibility'
import { MonthView } from './MonthView'
import { startViewOf, useCalPrefs, useRememberView, useSavedView } from './prefs'
import { Prompts } from './Prompts'
import { AskDialog } from '../tasks/Dialogs'
import { useTableDates } from './tableDates'
import { nextHalfHour, QuickCreate } from './QuickCreate'
import { ScheduleView, SearchResults } from './ScheduleView'
import { eventOrder, stepEvent, stepRow, tabStopOf, type EventStep } from './eventNav'
import { useCalUI } from './store'
import { TimeGrid } from './TimeGrid'
import type { ApiEvent, CalEvent, Collection } from './types'
import './calendar.css'
import { useOnline } from '../../hooks/useOnline'
import { useNow } from '../../hooks/useNow'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import { t as tr } from '../../i18n'
import { isRtl } from '../../lib/rtl'
import { phoneView } from './phoneView'

interface Base {
  view: CalendarView
  date: ISODate
}


const statusOf = (e: unknown) => (e as { status?: number } | null)?.status

export function CalendarApp() {
  const route = useAppRoute().calendar
  const location = useLocation()
  const nav = useNavigate()
  const qc = useQueryClient()
  const prefs = useCalPrefs()
  const saved = useSavedView()
  const settings = useSettings()
  const meLoaded = useMe().isSuccess
  // The fields this page draws, not the whole store: a keyboard drag (kbdDrag) changes on every
  // key and re-rendered the whole app.
  const ui = useCalUI(useShallow((st) => ({ focus: st.focus, drawer: st.drawer, editor: st.editor, detailAnchor: st.detailAnchor, quick: st.quick, set: st.set })))
  const theme = useThemeDef()
  const dark = useTheme() === 'dark'
  const narrow = useMediaQuery('(max-width: 720px)')
  const online = useOnline()
  const now = useNow()
  const today = todayISO(prefs.zone, now)
  useDocumentTitle(tr('common.calendar'))

  // ---------- where we are ----------

  const state = location.state as { base?: Base } | null
  const occDate = useMemo(() => {
    if (!route?.event?.occ) return null
    const o = route.event.occ
    if (/^\d{4}-\d{2}-\d{2}$/.test(o)) return o
    const d = new Date(o)
    return Number.isNaN(d.getTime()) ? null : toISODate(d, prefs.zone)
  }, [route?.event?.occ, prefs.zone])
  // The view Calendar opens on: the one this person chose last, on any device (setting
  // calendar_view), else this device's, else Today.
  const startView = startViewOf(settings, saved.view)
  const base: Base = route?.event
    ? state?.base ?? { view: startView, date: occDate ?? today }
    : { view: phoneView(route?.view ?? startView, narrow), date: route?.date ?? today }
  const view = base.view
  const date = base.date
  const searching = route?.search != null

  useEffect(() => {
    if (route?.view && route.view !== saved.view) saved.set({ view: route.view })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route?.view])

  const opts: ViewOptions = { weekStart: prefs.weekStart, ndays: narrow ? 3 : saved.ndays, hideWeekends: saved.hideWeekends, workDays: prefs.workDays }
  const days = useMemo(() => viewDays(view, date, opts),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [view, date, opts.weekStart, opts.ndays, opts.hideWeekends, opts.workDays.join()])
  const span = viewSpan(view, date, opts)
  const from = useMemo(() => startOfDay(span.first, prefs.zone), [span.first, prefs.zone])
  const to = useMemo(() => startOfDay(addDaysISO(span.last, 1), prefs.zone), [span.last, prefs.zone])

  // ---------- data ----------

  const cols = useCollections()
  const collections = useMemo(() => cols.data ?? [], [cols.data])
  const collectionByUrl = useMemo(() => new Map(collections.map((c) => [c.url ?? c.id, c])), [collections])
  // A visibility change still being saved wins over both stored copies (visibility.ts).
  const hidden = useHiddenCalendars(collections)
  const range = useRange(from, to, view !== 'schedule' && !searching)
  const events = useNormalized(range.data?.events, prefs.zone, collections, hidden)
  const tasksQ = useTasksInRange(span.first, span.last, prefs.zone, saved.tasksOnCalendar)
  // A table's dates the viewer put on their calendar ride with the date-only tasks (tableDates.tsx).
  const tableDates = useTableDates(span.first, span.last, view !== 'schedule' && !searching)
  const tasks = useMemo(() => [...(tasksQ.data ?? []).filter((t) => !t.list || !saved.hiddenLists.includes(t.list)), ...(tableDates.data ?? [])],
    [tasksQ.data, saved.hiddenLists, tableDates.data])
  const busyCols = useCallback((url?: string) => (url ? collectionByUrl.get(url)?.busy !== false : true), [collectionByUrl])
  const busy = useMemo(() => busyOf(events, busyCols), [events, busyCols])
  const actions = useCalActions(prefs, busy)
  const sync = useSyncStatus()
  const colourOf = useCallback((c: Collection | undefined) => collectionColour(c, c ? collections.indexOf(c) : 0), [collections])

  // ---------- navigation ----------

  // The account remembers the view the person picks (a key, the view menu, a mini-month click), not
  // every address they open, so a bookmark or a link does not change where Calendar opens.
  const remember = useRememberView()
  const go = useCallback((v: CalendarView, d?: ISODate | null) => {
    useCalUI.getState().set({ focus: null, slot: null, kbdDrag: null })
    if (meLoaded) remember(v)
    nav(calendarPath(v, d ?? null))
  }, [nav, meLoaded, remember])
  const shift = (dir: number) => go(view, shiftDate(view, date, dir, opts))

  const openEvent = useCallback((e: CalEvent, anchor?: Element | null) => {
    useCalUI.getState().set({ focus: e.key, detailAnchor: anchor ? anchor.getBoundingClientRect() : null, quick: null })
    nav(eventPath(e.uid, e.occurrence), { state: { base: { view, date } } })
  }, [nav, view, date])

  const closeDetail = useCallback(() => {
    useCalUI.getState().set({ detailAnchor: null })
    nav(calendarPath(view, date))
    // Focus returns to the event it came from.
    const key = useCalUI.getState().focus
    if (key) setTimeout(() => (document.querySelector(`[data-key="${CSS.escape(key)}"]`) as HTMLElement | null)?.focus(), 0)
  }, [nav, view, date])

  // ---------- the open occurrence ----------

  const wantKey = route?.event ? `${route.event.uid}|${route.event.occ}` : null
  const inRange = wantKey ? events.find((e) => e.key === wantKey) ?? null : null
  const fetched = useQuery({
    queryKey: ['calendar', 'event', route?.event?.uid ?? '', route?.event?.occ ?? null],
    queryFn: async () => {
      try {
        return await calApi.get(route!.event!.uid)
      } catch (e) {
        if (statusOf(e) === 404) return null
        throw e
      }
    },
    enabled: !!route?.event && !inRange && !range.isLoading,
    retry: false,
  })
  const [lastShown, setLastShown] = useState<CalEvent | null>(null)
  const detailEvent: CalEvent | null = inRange ?? (fetched.data && route?.event
    ? normalizeEvent({ ...fetched.data, occurrence: route.event.occ || fetched.data.occurrence } as ApiEvent, { zone: prefs.zone, collections: collectionByUrl })
    : null)
  useEffect(() => {
    if (detailEvent && detailEvent.key !== lastShown?.key) setLastShown(detailEvent)
  }, [detailEvent, lastShown])
  // An open detail whose event vanished (deleted in another client) says so.
  const deletedElsewhere = !!route?.event && !detailEvent && lastShown?.key === wantKey && fetched.data === null

  // ---------- keys ----------

  const focused = (): CalEvent | null => events.find((e) => e.key === useCalUI.getState().focus) ?? null
  // Arrow keys between events (eventNav.ts). Lists (Schedule, search) step through their rows;
  // the grids through the events drawn (Month's "+n more" hides some).
  const listView = view === 'schedule' || view === 'today' || searching
  const drawn = () => events.filter((e) => viewRef.current?.querySelector(`[data-key="${CSS.escape(e.key)}"]`))
  const moveFocus = (step: EventStep): false | void => {
    if (listView) {
      const row = stepRow(viewRef.current, step)
      if (!row) return false
      row.focus()
      return
    }
    if (!focusEvent(stepEvent(drawn(), days, view, useCalUI.getState().focus, step))) return false
  }
  const focusEvent = (e: CalEvent | undefined) => {
    if (!e) return false
    useCalUI.getState().set({ focus: e.key, slot: null })
    setTimeout(() => (document.querySelector(`[data-key="${CSS.escape(e.key)}"]`) as HTMLElement | null)?.focus(), 0)
    return true
  }
  const quickAtSlot = () => {
    const slot = useCalUI.getState().slot
    const start = slot ? gridInstant(slot.day, slot.min, prefs.zone) : nextHalfHour(now)
    useCalUI.getState().set({
      quick: { start, end: new Date(start.getTime() + prefs.defaultDuration * 60_000), allDay: false, anchor: slot ? slotRect(slot.day, slot.min) : null },
    })
  }

  const [syncing, setSyncing] = useState(false)
  const syncNow = () => {
    setSyncing(true)
    // ACT calendar.sync @web.calendar
    calApi.sync().then(() => {
      setTimeout(() => {
        void qc.invalidateQueries({ queryKey: ['calendar'] })
        setSyncing(false)
      }, 800)
    }, (e) => {
      setSyncing(false)
      toastError(e, tr('calendar.calendarApp.couldNotSync'))
    })
  }

  useAppShortcuts('calendar', {
    'calendar.today': () => go(view, null),
    'calendar.next': () => shift(1),
    'calendar.prev': () => shift(-1),
    'calendar.viewToday': () => go('today', date),
    'calendar.viewDay': () => go('day', date),
    'calendar.viewNDays': () => go('ndays', date),
    'calendar.viewWeek': () => go('week', date),
    'calendar.viewWorkWeek': () => go('workweek', date),
    'calendar.viewMonth': () => go('month', date),
    'calendar.viewSchedule': () => go('schedule', date),
    'calendar.create': () => quickAtSlot(), // ACT event.create @web.calendar
    'calendar.open': () => {
      const e = focused()
      if (!e) return false
      if (route?.event) {
        if (!e.readOnly) useCalUI.getState().set({ editor: { mode: 'edit', event: e } }) // ACT event.edit @web.calendar
      } else openEvent(e, document.querySelector(`[data-key="${CSS.escape(e.key)}"]`))
    },
    'calendar.delete': () => {
      const e = focused()
      if (!e) return false
      void actions.remove(e) // ACT event.delete @web.calendar
    },
    'calendar.duplicate': () => {
      const e = focused()
      if (!e) return false
      useCalUI.getState().set({ editor: { mode: 'duplicate', event: e } }) // ACT event.duplicate @web.calendar
    },
    'calendar.findTime': () => useCalUI.getState().set({ findTime: true }), // ACT event.find_time @web.calendar
    // The one search box, on the Calendar scope.
    'calendar.search': () => focusSearch('calendar'),
    'calendar.goToDate': (first) => useCalUI.getState().set({ goTo: first ?? '' }),
    'calendar.toggleLeft': () => (narrow ? useCalUI.getState().set({ drawer: !useCalUI.getState().drawer }) : saved.set({ leftOpen: !saved.leftOpen })),
    'calendar.nextEvent': () => moveFocus('next'),
    'calendar.prevEvent': () => moveFocus('prev'),
    'calendar.eventDown': () => moveFocus('down'),
    'calendar.eventUp': () => moveFocus('up'),
    // ACT event.rsvp @web.calendar
    'calendar.rsvpYes': () => { const e = focused(); if (!e?.my_status) return false; void actions.rsvp(e, 'accepted') },
    'calendar.rsvpMaybe': () => { const e = focused(); if (!e?.my_status) return false; void actions.rsvp(e, 'tentative') },
    'calendar.rsvpNo': () => { const e = focused(); if (!e?.my_status) return false; void actions.rsvp(e, 'declined') },
    'calendar.undo': () => (runLastUndo() ? undefined : false),
    'calendar.sync': () => syncNow(),
    'calendar.escape': () => {
      if (route?.event) closeDetail()
      else if (searching) go(saved.view, null)
      else if (useCalUI.getState().focus || useCalUI.getState().slot) useCalUI.getState().set({ focus: null, slot: null })
      else return false
    },
  })

  // Grid keys: arrows move the focused slot; Shift+arrows drag the focused event.
  const timedView = view === 'day' || view === 'ndays' || view === 'workweek' || view === 'week'
  const onGridKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const s = useCalUI.getState()
    // The day columns run right to left in a right-to-left page: there ArrowLeft goes to the next day.
    const rtl = isRtl()
    const arrow = ({ ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: rtl ? 'right' : 'left', ArrowRight: rtl ? 'left' : 'right' } as const)[e.key as 'ArrowUp'] as 'up' | 'down' | 'left' | 'right' | undefined
    const ev = focused()
    if (s.kbdDrag) {
      if (arrow && e.shiftKey) {
        e.preventDefault()
        s.set({ kbdDrag: { ...s.kbdDrag, span: keyboardDrag(s.kbdDrag.span, arrow, s.kbdDrag.resize, prefs.snap, days.length) } })
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const k = s.kbdDrag
        s.set({ kbdDrag: null })
        const target = events.find((x) => x.key === k.key)
        if (target) {
          const start = gridInstant(days[k.span.day], k.span.start, prefs.zone)
          const end = gridInstant(days[k.span.day], k.span.end, prefs.zone)
          void actions.reschedule(target, start, end, false, k.resize ? tr('calendar.calendarApp.resize') : tr('common.move')) // ACT event.reschedule @web.calendar, ACT event.resize @web.calendar
        }
      } else if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        s.set({ kbdDrag: null })
      }
      return
    }
    if (ev && arrow && e.shiftKey) {
      e.preventDefault()
      if (ev.readOnly || !online) {
        toast({ message: ev.readOnly ? tr('common.readOnlyCalendar') : tr('calendar.calendarApp.offlineTheCalendarIsRead'), error: true })
        return
      }
      if (timedView && !ev.allDay) {
        const day = Math.max(0, days.indexOf(ev.firstDay))
        const startMin = minutesOfDay(ev.startAt, prefs.zone)
        const dur = Math.round((ev.endAt.getTime() - ev.startAt.getTime()) / 60_000)
        s.set({ kbdDrag: { key: ev.key, resize: e.altKey, span: keyboardDrag({ day, start: startMin, end: startMin + dur }, arrow, e.altKey, prefs.snap, days.length) } })
      } else {
        // All-day and month items move by a day (←/→) or a week (↑/↓); the time is kept.
        const n = arrow === 'left' ? -1 : arrow === 'right' ? 1 : arrow === 'up' ? -7 : 7
        if (ev.allDay) void actions.reschedule(ev, addDaysISO(ev.firstDay, n), addDaysISO(ev.lastDay, n + 1), true)
        else {
          const to = moveDays(ev.startAt, ev.endAt, ev.firstDay, n, prefs.zone)
          void actions.reschedule(ev, to.start, to.end, false)
        }
      }
      return
    }
    // With an event focused, the plain arrows move between events (the calendar.*Event keys);
    // with none, they move the selected time slot.
    if (timedView && arrow && !e.shiftKey && !e.altKey && !ev) {
      e.preventDefault()
      const cur = s.slot ?? { day: days.includes(today) ? today : days[0], min: Math.floor(minutesOfDay(now, prefs.zone) / prefs.snap) * prefs.snap }
      let i = Math.max(0, days.indexOf(cur.day))
      let min = cur.min
      if (arrow === 'up') min = Math.max(0, min - prefs.snap)
      if (arrow === 'down') min = Math.min(1440 - prefs.snap, min + prefs.snap)
      if (arrow === 'left') i = Math.max(0, i - 1)
      if (arrow === 'right') i = Math.min(days.length - 1, i + 1)
      s.set({ slot: { day: days[i], min }, focus: null })
      ;(document.activeElement as HTMLElement | null)?.blur?.()
      viewRef.current?.focus({ preventScroll: true })
      return
    }
    if (timedView && e.key === 'Enter' && s.slot && !ev) {
      e.preventDefault()
      quickAtSlot()
    }
  }
  const viewRef = useRef<HTMLDivElement>(null)

  // ---------- sync status ----------

  const lastSync = sync.data?.last_sync_at ?? range.data?.sync?.last_sync_at ?? null
  const syncState = sync.data?.state ?? range.data?.sync?.state ?? null
  const ago = lastSync ? Math.max(0, Math.round((now.getTime() - new Date(lastSync).getTime()) / 60_000)) : null
  const syncText = syncing || syncState === 'syncing' ? tr('calendar.calendarApp.syncing') : ago == null ? (syncState ? tr('calendar.calendarApp.synced') : null) : ago >= 5 ? tr('calendar.calendarApp.syncedAgo', { value: ago < 120 ? tr('calendar.calendarApp.min', { ago }) : `${Math.round(ago / 60)} h` }) : tr('calendar.calendarApp.synced')
  const syncBehind = ago != null && ago >= 5

  // ---------- search ----------

  const searchQ = useQuery({
    queryKey: ['calendar', 'search', route?.search ?? ''],
    enabled: searching && !!route?.search,
    queryFn: async ({ signal }) => {
      // ACT event.search @web.calendar
      try {
        const r = await api.get<{ items: (ApiEvent & { kind?: string })[] }>('/calendar/search', { q: route!.search!, kind: 'event', limit: 100 }, signal)
        return r.items.filter((i) => !i.kind || i.kind === 'event')
      } catch (e) {
        if (!isMissingRoute(e)) throw e
        const r = await api.get<{ items: ApiEvent[] }>('/calendar/events/search', { q: route!.search!, from: addDaysISO(today, -365), to: addDaysISO(today, 365) }, signal)
        return r.items
      }
    },
    staleTime: 15_000,
  })
  const noHidden = useMemo(() => new Set<string>(), [])
  const searchItems = useNormalized(searchQ.data, prefs.zone, collections, noHidden)

  // ---------- states ----------

  // The last error until a request succeeds: a refetch in flight does not hide the banner.
  const rangeError = useSettledError(range)
  const rangeStatus = statusOf(rangeError)
  const notConnected = range.data?.connected === false || (cols.isSuccess && collections.length === 0 && !cols.isFetching)
    || (rangeError != null && rangeStatus != null && rangeStatus < 500 && rangeStatus !== 401)
  const eventCals = collections.filter((c) => c.events !== false && !c.error)
  const allHidden = eventCals.length > 0 && eventCals.every((c) => hidden.has(c.url ?? c.id))
  const accountErrors = [
    ...collections.filter((c) => c.error).map((c) => ({ id: c.url ?? c.id, auth: false, detail: undefined as string | undefined, text: `${c.name || tr('calendar.calendarApp.aCalendar')}: ${c.error}` })),
    ...(sync.data?.connectors ?? []).filter((c) => c.status === 'error').map((c) => ({
      id: String(c.id), auth: c.error_kind === 'auth', detail: c.detail ?? undefined,
      text: c.error_kind === 'auth' ? tr('calendar.calendarApp.passwordRefused', { name: c.name }) : `${c.name}: ${c.detail ?? tr('calendar.calendarApp.syncFailed')}`,
    })),
  ]
  const zoneDiffers = prefs.zone !== prefs.homeZone
  // A server error or an unreachable server is not "no events": say so, with Retry.
  // Offline has its own banner; a 4xx "not connected" shows the connect screen.
  const rangeFailed = rangeError != null && !notConnected && online && rangeStatus !== 401

  // One context object per change of what it holds, so the views under it re-render with the data,
  // not with every render of the page.
  const loadingPh = range.isPlaceholderData
  const tabStop = useMemo(() => tabStopOf(eventOrder(events, days), ui.focus), [events, days, ui.focus])
  const ctx: CalCtx = useMemo(() => ({
    prefs, dark, view, date, today, now, events, tasks, busy, collections, collectionByUrl, hidden, colourOf, actions,
    readOnly: !online, loading: loadingPh, openEvent, go, narrow, tabStop,
  }), [prefs, dark, view, date, today, now, events, tasks, busy, collections, collectionByUrl, hidden, colourOf, actions, online, loadingPh, openEvent, go, narrow, tabStop])

  // ACT calendar.toggle_visibility @web.calendar ("Show all" from the banner)
  const showAll = () => {
    saved.set({ hiddenLocal: [], beforeShowOnly: null })
    void saveVisibility(qc, collections.filter((x) => hidden.has(x.url ?? x.id)), false)
  }

  const detailMode: DetailMode = narrow ? 'sheet' : theme.apps.eventDetail
  const editorMode: EditorMode = narrow || theme.apps.eventDetail === 'sheet' ? 'sheet' : theme.apps.eventDetail === 'pane' ? 'pane' : 'modal'
  const leftShown = narrow ? ui.drawer : saved.leftOpen
  // On a phone a one-day title is short ("6 Oct"), so it fits beside Today, the arrows and the view menu.
  const title = searching ? tr('common.search') : narrow && view === 'day' ? formatDate(date, 'short')
    : periodTitle(view, date, view === 'schedule' ? [date] : days)

  const restore = () => {
    if (!lastShown) return
    // "Restore as new": the last copy shown, as a new event.
    const { uid: _u, ...body } = toWrite(lastShown)
    void _u
    void actions.create(body).then((out) => out && closeDetail())
  }

  if (notConnected && !range.isLoading) {
    return (
      // data-view: the view the address asks for, as on the full app (e2e waits on it).
      <section className="app-frame calendar-app cal-app" data-testid="calendar-app" data-view={searching ? 'search' : view} aria-label={tr('common.calendar')}>
        <div className="cal-connect" data-testid="cal-not-connected">
          {/* A holiday or school calendar needs no account of one's own (calendar.subscribe). */}
          <NoCalendarConnected app="calendar" extra={<SubscribeButton />} />
        </div>
      </section>
    )
  }

  const shownDetail = detailEvent ?? (deletedElsewhere ? lastShown : null)

  return (
    <CalContext.Provider value={ctx}>
      <section
        className={`app-frame calendar-app cal-app${narrow ? ' narrow' : ''}${leftShown ? ' left-open' : ''}`}
        data-testid="calendar-app"
        data-view={searching ? 'search' : view}
        aria-label={tr('common.calendar')}
      >
        <Header
          title={title}
          days={days}
          onPrev={() => shift(-1)}
          onNext={() => shift(1)}
          onToday={() => go(view, null)}
          syncText={syncText}
          syncBehind={syncBehind}
          onSync={syncNow}
          syncing={syncing}
        />
        <div className="cal-banners">
          {!online && <div className="cal-banner offline" role="status"><CloudOff size={16} aria-hidden />{' '}{tr('calendar.calendarApp.offlineTheCalendarIsRead2')}</div>}
          {rangeFailed && (
            <div className="cal-banner error" role="alert" data-testid="cal-load-error">
              <TriangleAlert size={16} aria-hidden /> {range.data ? tr('calendar.calendarApp.couldntRefreshTheCalendarShowing') : tr('calendar.calendarApp.couldntLoadEvents')}
              <button type="button" className="btn text sm" onClick={() => void range.refetch()}><RefreshCw size={14} aria-hidden />{' '}{tr('common.retry')}</button>
            </div>
          )}
          {accountErrors.map((a) => (
            <div key={a.id} className="cal-banner error" role="alert" title={a.detail}><TriangleAlert size={16} aria-hidden /> {a.text}
              {/* A refused password: Retry cannot work; the way out is the account's password. */}
              {a.auth
                ? <Link className="btn text sm" to="/settings/calendar">{tr('calendar.calendarApp.checkThePassword')}</Link>
                : <button type="button" className="btn text sm" onClick={syncNow}><RefreshCw size={14} aria-hidden />{' '}{tr('common.retry')}</button>}
            </div>
          ))}
          {allHidden && !searching && (
            <div className="cal-banner" role="status" data-testid="cal-all-hidden"><Eye size={16} aria-hidden />{' '}{tr('calendar.calendarApp.allCalendarsAreHidden')}<button type="button" className="btn text sm" onClick={showAll}>{tr('common.showAll')}</button></div>
          )}
          {zoneDiffers && (
            <div className="cal-banner zone" role="status">
              <Globe size={16} aria-hidden />{' '}{tr('calendar.calendarApp.viewingInTime', { replace: prefs.zone.split('/').pop()!.replace(/_/g, ' '), utcLabel: utcLabel(now, prefs.zone) })}
              <button type="button" className="btn text sm" onClick={() => saved.set({ displayZone: null })}>{tr('calendar.calendarApp.use', { zoneAbbr: zoneAbbr(now, prefs.homeZone) })}</button>
            </div>
          )}
          {syncState === 'initial' && <div className="cal-banner" role="status"><RefreshCw size={16} className="spin" aria-hidden />{' '}{tr('calendar.calendarApp.firstSyncInProgressCalendars')}</div>}
        </div>
        <div className="cal-main">
          {leftShown && (
            <>
              {narrow && <div className="cal-drawer-scrim" onClick={() => ui.set({ drawer: false })} />}
              <LeftColumn shownDays={days} onCloseDrawer={narrow ? () => ui.set({ drawer: false }) : undefined} />
            </>
          )}
          <div className="cal-view" ref={viewRef} onKeyDown={onGridKey} data-testid="cal-view" tabIndex={-1}>
            {range.isLoading && !range.data && view !== 'schedule' && view !== 'today' && !searching ? (
              <GridSkeleton days={view === 'month' ? 7 : days.length} month={view === 'month'} />
            ) : searching ? (
              <SearchResults q={route?.search ?? ''} items={searchItems} loading={searchQ.isFetching} error={searchQ.isError ? tr('calendar.calendarApp.searchFailed') : null} />
            ) : (
              // A failure in the view stays in the view (each event has its own boundary too).
              <ItemBoundary key={view} what="items">
                {view === 'month' ? (
                  <MonthView />
                ) : view === 'schedule' ? (
                  <ScheduleView />
                ) : view === 'today' ? (
                  <TodayView loading={range.isLoading && !range.data} />
                ) : (
                  <TimeGrid days={days} />
                )}
              </ItemBoundary>
            )}
          </div>
          {route?.event && detailMode === 'pane' && shownDetail && (
            <EventDetail e={shownDetail} mode="pane" anchor={null} onClose={closeDetail} deleted={deletedElsewhere} onRestore={restore} />
          )}
          {ui.editor && editorMode === 'pane' && <EventEditor key={ui.editor.event?.key ?? 'new'} init={ui.editor} mode="pane" />}
        </div>
        {route?.event && detailMode !== 'pane' && shownDetail && (
          <EventDetail
            e={shownDetail}
            mode={detailMode === 'popover' && !ui.detailAnchor ? 'card' : detailMode}
            anchor={ui.detailAnchor}
            onClose={closeDetail}
            deleted={deletedElsewhere}
            onRestore={restore}
          />
        )}
        {route?.event && !shownDetail && fetched.data === null && (
          <Dialog open onClose={closeDetail} title={tr('calendar.calendarApp.eventNotFound')}>
            <p>{tr('calendar.calendarApp.thisEventIsNotIn')}</p>
          </Dialog>
        )}
        {ui.quick && <QuickCreate key={String(ui.quick.start)} q={ui.quick} />}
        {ui.editor && editorMode !== 'pane' && <EventEditor key={ui.editor.event?.key ?? 'new'} init={ui.editor} mode={editorMode} />}
        <FindTimeDialog />
        <GoToDate onGo={(d) => go(view === 'schedule' ? 'schedule' : view, d)} />
        <Prompts />
        <AskDialog />
      </section>
    </CalContext.Provider>
  )
}

function utcLabel(now: Date, zone: string): string {
  const off = zoneOffset(now, zone)
  const h = Math.trunc(off / 60)
  const m = Math.abs(off % 60)
  return `UTC${off >= 0 ? '+' : '−'}${Math.abs(h)}${m ? `:${String(m).padStart(2, '0')}` : ''}`
}

function slotRect(day: ISODate, min: number): DOMRect | null {
  const col = document.querySelector(`.cal-col[data-day="${day}"]`)
  if (!col) return null
  const r = col.getBoundingClientRect()
  const hourH = parseFloat(getComputedStyle(col).getPropertyValue('--hour-h')) || 48
  return new DOMRect(r.left + r.width / 2, r.top + (min / 60) * hourH, 1, 1)
}

/** The first load: a skeleton grid. */
function GridSkeleton({ days, month }: { days: number; month: boolean }) {
  return (
    <div className={`cal-skeleton${month ? ' month' : ''}`} aria-busy="true" aria-label={tr('calendar.calendarApp.loadingTheCalendar')} data-testid="cal-skeleton">
      {Array.from({ length: month ? 35 : days }, (_, i) => (
        <div key={i} className="cal-skel-col">
          <Skeleton height={month ? 14 : 18} width="40%" />
          <Skeleton height={month ? 18 : 48} style={{ marginTop: month ? 6 : 60 + (i % 3) * 40 }} />
          {!month && <Skeleton height={32} style={{ marginTop: 40 + (i % 2) * 70 }} />}
        </div>
      ))}
    </div>
  )
}

/** `g` then a typed date ("g 12 dec"): a small prompt that takes the quick-add date words. */
function GoToDate({ onGo }: { onGo: (d: ISODate) => void }) {
  const goTo = useCalUI((s) => s.goTo)
  if (goTo == null) return null
  return <GoToDateDialog key={goTo} first={goTo} onGo={onGo} />
}

function GoToDateDialog({ first, onGo }: { first: string; onGo: (d: ISODate) => void }) {
  const prefs = useCalPrefs()
  const [text, setText] = useState(first)
  const [bad, setBad] = useState(false)
  const close = () => useCalUI.getState().set({ goTo: null })
  const parsed = text ? parseDateText(text, new Date(), prefs.zone) : null
  const submit = () => {
    if (!parsed) return setBad(true)
    close()
    onGo(parsed)
  }
  return (
    <Dialog open onClose={close} title={tr('calendar.calendarApp.goToDate')} initialFocus="input">
      <input
        className={`input${bad ? ' invalid' : ''}`}
        value={text}
        placeholder={tr('calendar.calendarApp.n12DecNextFri2w')}
        aria-label={tr('common.date')}
        // The character typed after g is already in the field; typing goes on after it.
        onFocus={(e) => e.currentTarget.setSelectionRange(e.currentTarget.value.length, e.currentTarget.value.length)}
        onChange={(e) => { setText(e.target.value); setBad(false) }}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
      />
      <p className="cal-note">{bad ? tr('calendar.calendarApp.notADateMailrKnows') : parsed ? formatDate(parsed, 'long') : tr('calendar.calendarApp.typeADateThenEnter')}</p>
    </Dialog>
  )
}

export default CalendarApp
