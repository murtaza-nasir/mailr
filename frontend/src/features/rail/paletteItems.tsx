// The Calendar and Tasks entries of the command palette (web.palette): "New
// event: …" and "New task: …" with the quick-add grammar and its chips before Enter; "Go to 12 Dec";
// view switching; showing, hiding or isolating a calendar; going to a list or a tag; searching
// events; find a time; and the commands of the app that is open.

import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { CalendarDays, CalendarPlus, CalendarRange, CalendarSearch, CircleCheck, Columns3, Eye, EyeOff, Hash, List, ListPlus, ListTodo, Search, Square, Sun } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import { useAppRoute } from '../../hooks/useAppRoute'
import { formatDate, todayISO } from '../../lib/dates'
import { calendarPath, calendarSearchPath, eventPath, taskPath, taskSearchPath, tasksPath, type CalendarView } from '../../lib/routes'
import { toast, toastError } from '../../stores/ui'
import { calApi, useCollections } from '../calendar/data'
import { useCalendarCommands } from '../calendar/commands'
import { useCalPrefs, useRememberView } from '../calendar/prefs'
import { useCalUI } from '../calendar/store'
import { useHiddenCalendars, saveVisibility } from '../calendar/visibility'
import type { ApiEvent, Collection } from '../calendar/types'
import { useDefaults, useServerTags, useTaskLists } from '../tasks/api'
import { useTaskCommands } from '../tasks/commands'
import { listKey } from '../tasks/model'
import { createCommand, goToDate, parseLine, quickChips, quickEventBody, quickTaskBody, type QuickCtx } from './quickCreate'
import { t as tr } from '../../i18n'

export type PaletteSection = 'Create' | 'Calendar' | 'Tasks'

export interface PaletteItem {
  id: string
  section: PaletteSection
  label: string
  hint?: string
  icon: ReactNode
  keys?: string
  /** Chips under the label (the quick-add parse). */
  chips?: string[]
  /** Keep the palette open (the item fills the input). */
  stay?: boolean
  /** Guidance, not a command: no ↵ (CommandPalette). */
  info?: boolean
  run: () => void
}

const VIEWS: { view: CalendarView; label: string; icon: ReactNode }[] = [
  { view: 'today', label: tr('common.today'), icon: <Sun /> },
  { view: 'day', label: tr('common.day'), icon: <Square /> },
  { view: 'workweek', label: tr('common.workWeek'), icon: <Columns3 /> },
  { view: 'week', label: tr('common.week'), icon: <CalendarRange /> },
  { view: 'month', label: tr('common.month'), icon: <CalendarDays /> },
  { view: 'schedule', label: tr('common.schedule'), icon: <List /> },
]

const urlOf = (c: Collection) => c.url ?? c.id

/** ACT calendar.toggle_visibility @web.palette, ACT calendar.show_only @web.palette: the same path as the calendar app (calendar/visibility.ts). */
const setHidden = (qc: QueryClient, targets: Collection[], hide: boolean, after?: Promise<unknown>) => saveVisibility(qc, targets, hide, after)

/** "New list: Groceries": the name of a new task list, or null. */
export function newListName(q: string): string | null {
  const m = /^\s*(?:new|add|create)\s+list\s*:\s*(.*)$/i.exec(q)
  return m ? m[1].trim() : null
}

/** ACT task.search @web.palette: open tasks matching the text (with the search operators), a few at a time. */
function useTaskMatches(q: string, enabled: boolean) {
  const [text, setText] = useState(q)
  useEffect(() => {
    const t = setTimeout(() => setText(q.trim()), 200)
    return () => clearTimeout(t)
  }, [q])
  return useQuery<{ uid: string; summary: string; due: string | null }[]>({
    queryKey: ['taskPalette', text],
    queryFn: async ({ signal }) => {
      const r = await api.get<{ items: { uid: string; summary: string; due: string | null }[] }>('/tasks', { q: text, status: 'open', limit: 5 }, signal)
      return (r.items ?? []).slice(0, 5)
    },
    enabled: enabled && text.length >= 2,
    staleTime: 15_000,
  })
}

/** The calendar and task entries for the palette's text `q`. */
export function usePaletteItems(q: string, setQ: (s: string) => void): { fixed: PaletteItem[]; dynamic: PaletteItem[] } {
  const nav = useNavigate()
  const remember = useRememberView()
  const qc = useQueryClient()
  const route = useAppRoute()
  const prefs = useCalPrefs()
  const cols = useCollections().data
  const lists = useTaskLists().data
  const tags = useServerTags(true).data
  const defaults = useDefaults().data
  const hidden = useHiddenCalendars(cols)
  const calCommands = useCalendarCommands().commands
  const taskCommands = useTaskCommands().commands
  const listName = newListName(q)
  const plain = !!q.trim() && !createCommand(q) && listName === null && !/^\s*(go\s*to|goto|jump\s+to)\s/i.test(q)
  const matches = useTaskMatches(q, plain).data

  const base: Omit<QuickCtx, 'now'> = useMemo(() => ({
    prefs, defaultDuration: prefs.defaultDuration, morningHour: prefs.morningHour, videoTemplate: prefs.videoTemplate,
    calendars: (cols ?? []).filter((c) => c.events !== false && !c.read_only && !c.error).map((c) => ({ id: urlOf(c), name: c.name })),
    lists: (lists ?? []).map((l) => ({ id: l.id, name: l.name })),
  }), [prefs, cols, lists])

  const fixed = useMemo<PaletteItem[]>(() => {
    const out: PaletteItem[] = [
      { id: 'create:event', section: 'Create', label: tr('rail.paletteItems.newEvent'), hint: tr('rail.paletteItems.typeItInOneLine'), icon: <CalendarPlus />, stay: true, run: () => setQ(tr('rail.paletteItems.newEvent2')) },
      { id: 'create:task', section: 'Create', label: tr('rail.paletteItems.newTask'), hint: tr('rail.paletteItems.typeItInOneLine'), icon: <ListPlus />, stay: true, run: () => setQ(tr('rail.paletteItems.newTask2')) },
      // ACT list.create @web.palette
      { id: 'create:list', section: 'Create', label: tr('rail.paletteItems.newTaskList'), hint: tr('rail.paletteItems.typeItsName'), icon: <ListTodo />, stay: true, run: () => setQ(tr('rail.paletteItems.newList')) },
      { id: 'cal:goto', section: 'Calendar', label: tr('rail.paletteItems.goToADate'), hint: tr('rail.paletteItems.goTo12Dec'), icon: <CalendarDays />, stay: true, run: () => setQ(tr('rail.paletteItems.goTo')) },
      // ACT event.find_time @web.palette
      { id: 'cal:findtime', section: 'Calendar', label: tr('common.findATime'), icon: <CalendarSearch />, keys: 'f', run: () => { useCalUI.getState().set({ findTime: true }); if (route.app !== 'calendar') nav(calendarPath(null)) } },
    ]
    if (route.app !== 'calendar')
      for (const v of VIEWS)
        out.push({ id: `cal:view:${v.view}`, section: 'Calendar', label: tr('rail.paletteItems.calendarView', { label: v.label }), icon: v.icon, run: () => { remember(v.view); nav(calendarPath(v.view, null)) } })
    if (route.app === 'calendar')
      for (const c of calCommands) if (c.enabled) out.push({ id: `cal:cmd:${c.id}`, section: 'Calendar', label: c.id.startsWith('view-') ? tr('rail.paletteItems.calendarView', { label: c.label }) : c.label, hint: c.on ? 'showing' : undefined, icon: c.icon, keys: c.shortcut, run: () => c.run(null) })
    if (route.app === 'tasks')
      for (const c of taskCommands) if (c.enabled) out.push({ id: `task:cmd:${c.id}`, section: 'Tasks', label: c.label, icon: c.icon, keys: c.shortcut, run: () => c.run(null) })
    const eventCals = (cols ?? []).filter((c) => c.events !== false && !c.error)
    for (const c of eventCals) {
      const off = hidden.has(urlOf(c))
      out.push({ id: `cal:toggle:${urlOf(c)}`, section: 'Calendar', label: tr('rail.paletteItems.calendar', { value: off ? tr('common.show') : tr('common.hide'), itemName: c.name }), icon: off ? <Eye /> : <EyeOff />, run: () => void setHidden(qc, [c], !off) })
      if (eventCals.length > 1)
        out.push({
          id: `cal:only:${urlOf(c)}`, section: 'Calendar', label: tr('rail.paletteItems.showOnlyCalendar', { itemName: c.name }), icon: <Eye />,
          run: () => void setHidden(qc, [c], false, setHidden(qc, eventCals.filter((x) => urlOf(x) !== urlOf(c)), true)),
        })
    }
    for (const l of lists ?? [])
      out.push({ id: `task:list:${l.id}`, section: 'Tasks', label: tr('rail.paletteItems.goToList', { itemName: l.name }), icon: <ListTodo />, run: () => nav(tasksPath({ kind: 'list', id: listKey(l) })) })
    for (const t of tags ?? [])
      out.push({ id: `task:tag:${t.name}`, section: 'Tasks', label: tr('rail.paletteItems.goToTag', { itemName: t.name }), hint: t.count ? tr('rail.paletteItems.open', { count: t.count }) : undefined, icon: <Hash />, run: () => nav(tasksPath({ kind: 'tag', id: t.name })) })
    return out
  }, [route.app, calCommands, taskCommands, cols, hidden, lists, tags, nav, qc, setQ, remember])

  const dynamic = useMemo<PaletteItem[]>(() => {
    // "Now" for the grammar is read at each keystroke.
    const ctx: QuickCtx = { ...base, now: new Date() }
    const out: PaletteItem[] = []
    const cmd = createCommand(q)
    if (cmd && cmd.text.trim()) {
      const r = parseLine(cmd.text, cmd.mode, ctx)
      const chips = quickChips(r, cmd.mode, ctx)
      if (cmd.mode === 'event') {
        const body = quickEventBody(r, ctx)
        out.push({
          id: 'create:event:now', section: 'Create', label: tr('rail.paletteItems.newEvent3', { summary: body.summary }), icon: <CalendarPlus />, chips,
          run: () => {
            // ACT event.create @web.palette
            void api.post<ApiEvent>('/calendar/events', body as Record<string, unknown>).then((ev) => {
              void qc.invalidateQueries({ queryKey: qk.calendar.all })
              // Open and Undo, as every create toast.
              toast({
                message: tr('rail.paletteItems.eventCreated', { summary: ev.summary }), actionLabel: tr('common.open'), onAction: () => nav(eventPath(ev.uid, ev.occurrence ?? ev.start)),
                secondaryLabel: tr('common.undo'), onSecondary: () => void calApi.remove(ev.uid, { etag: ev.etag, sendUpdates: false })
                  .then(() => qc.invalidateQueries({ queryKey: qk.calendar.all }), (e) => toastError(e, tr('common.couldNotUndo'))),
              })
            }, (e) => toastError(e, tr('rail.paletteItems.eventNotCreated')))
          },
        })
        out.push({
          id: 'create:event:editor', section: 'Create', label: tr('rail.paletteItems.addDetailsInTheEditor'), icon: <CalendarPlus />,
          run: () => {
            const start = body.all_day ? body.start! : new Date(body.start!)
            const end = body.all_day ? body.end ?? body.start! : new Date(body.end ?? body.start!)
            const guests = r.guests.filter((g) => g.email).map((g) => ({ email: g.email! }))
            useCalUI.getState().set({ editor: { mode: 'new', seed: { summary: body.summary, start, end, allDay: !!body.all_day, location: body.location ?? '', rrule: body.recurrence ?? null, guests, reminders: body.reminders, video: r.video, ...(body.calendar ? { calendar: body.calendar } : {}) } } })
            if (route.app !== 'calendar') nav(calendarPath(null))
          },
        })
      } else {
        const body = quickTaskBody(r, ctx, defaults?.task_list ?? null)
        if (body)
          out.push({
            id: 'create:task:now', section: 'Create', label: tr('rail.paletteItems.newTask3', { string: String(body.summary) }), icon: <ListPlus />, chips,
            run: () => {
              // ACT task.create @web.palette
              void api.post<{ uid: string; summary: string }>('/tasks', body).then((t) => {
                void qc.invalidateQueries({ queryKey: qk.tasks.all })
                toast({
                  message: tr('rail.paletteItems.taskAdded', { summary: t.summary }), actionLabel: tr('common.open'), onAction: () => nav(taskPath(t.uid)),
                  secondaryLabel: tr('common.undo'), onSecondary: () => void api.del(`/tasks/${encodeURIComponent(t.uid)}`)
                    .then(() => qc.invalidateQueries({ queryKey: qk.tasks.all }), (e) => toastError(e, tr('common.couldNotUndo'))),
                })
              }, (e) => toastError(e, tr('rail.paletteItems.taskNotAdded')))
            },
          })
      }
    }
    const day = goToDate(q, ctx)
    if (day) {
      const v = route.calendar?.view ?? (route.app === 'calendar' ? null : 'day')
      out.push({ id: 'cal:goto:date', section: 'Calendar', label: tr('rail.paletteItems.goTo2', { date: formatDate(day, day.slice(0, 4) === todayISO(prefs.zone).slice(0, 4) ? 'day' : 'dayYear') }), icon: <CalendarDays />, run: () => nav(calendarPath(v ?? 'week', day)) })
    }
    if (listName !== null) {
      if (listName) {
        // ACT list.create: on the account of the default task list.
        const home = (lists ?? []).find((l) => l.id === defaults?.task_list) ?? (lists ?? [])[0]
        out.push({
          id: 'list:create:now', section: 'Create', label: tr('rail.paletteItems.newTaskList2', { listName }), icon: <ListPlus />,
          run: () => {
            void api.post<{ id?: string; url?: string; calendar_id?: number; name: string }>('/calendar/collections', { name: listName, kind: 'tasks', ...(home ? { connector_id: home.connector_id } : {}) }).then((l) => {
              void qc.invalidateQueries({ queryKey: qk.tasks.all })
              void qc.invalidateQueries({ queryKey: qk.calendar.all })
              toast({ message: tr('rail.paletteItems.listCreated', { itemName: l.name }), actionLabel: tr('common.open'), onAction: () => nav(tasksPath({ kind: 'list', id: l.calendar_id != null ? String(l.calendar_id) : l.url ?? l.id ?? '' })) })
            }, (e) => toastError(e, tr('rail.paletteItems.listNotCreated')))
          },
        })
      } else out.push({ id: 'list:create:hint', section: 'Create', label: tr('rail.paletteItems.typeTheNameOfThe'), icon: <ListPlus />, stay: true, info: true, run: () => {} })
    }
    if (q.trim() && !cmd && !day && listName === null) {
      // ACT event.search @web.palette
      out.push({ id: 'cal:search', section: 'Calendar', label: tr('rail.paletteItems.searchEventsFor', { q: q.trim() }), icon: <Search />, run: () => nav(calendarSearchPath(q.trim())) })
      // ACT task.search: the first matches open in Tasks; the last item runs the search there.
      for (const t of plain ? matches ?? [] : [])
        out.push({ id: `task:match:${t.uid}`, section: 'Tasks', label: t.summary || '(untitled)', hint: 'task', icon: <CircleCheck />, run: () => nav(taskPath(t.uid)) })
      out.push({ id: 'task:search', section: 'Tasks', label: tr('rail.paletteItems.searchTasksFor', { q: q.trim() }), icon: <Search />, run: () => nav(taskSearchPath(q.trim())) })
    }
    return out
  }, [q, base, qc, nav, defaults?.task_list, route.app, route.calendar?.view, prefs.zone, listName, lists, plain, matches])

  return { fixed, dynamic }
}

/** "New event: …" / "New task: …": the palette shows only the create items (or a hint while empty). */
export function createMode(q: string): 'event' | 'task' | null {
  return createCommand(q)?.mode ?? null
}

/** The hint while "New event: " has no text yet. */
export function createHint(mode: 'event' | 'task'): PaletteItem {
  return {
    id: `create:hint:${mode}`, section: 'Create', stay: true, info: true, run: () => {},
    icon: mode === 'event' ? <CalendarPlus /> : <ListPlus />,
    label: mode === 'event' ? tr('rail.paletteItems.typeTheEventInOne') : tr('rail.paletteItems.typeTheTaskInOne'),
  }
}
