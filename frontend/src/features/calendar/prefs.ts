// What the Calendar app needs to know about the user: date preferences and calendar settings from
// the account (Settings → Calendar & tasks; every key optional, with the spec's defaults), and the
// saved view (the last view, the visible calendars, density), kept on this device.

import { useCallback, useMemo } from 'react'
import { create } from 'zustand'
import { useSettings, useUpdateSettings } from '../../api/hooks'
import type { Settings } from '../../api/types'
import { resolveDatePrefs, type DatePrefs, type Weekday } from '../../lib/dates'
import { isCalendarView, type CalendarView } from '../../lib/routes'
import { DEFAULT_WORK_DAYS } from './geometry'

/** Account settings the app reads. W3's settings tab writes them; absent keys use these defaults. */
export interface CalSettings {
  /** Default event length in minutes (30 or 60). */
  calendar_default_duration?: number
  /** Drag and click snap in minutes (5, 10, 15 or 30). */
  calendar_snap?: number
  /** Working hours, shaded outside: "09:00"–"17:00" on Mon–Fri. */
  calendar_working_hours?: { start: string; end: string; days: number[] }
  calendar_dim_past?: boolean
  calendar_week_numbers?: boolean
  /** Show a "Overlaps …" line in quick create and the editor. */
  calendar_conflicts?: boolean
  /** The video link template; {id} becomes a random room name. */
  calendar_video_template?: string
  /** The hour "On the day at 9:00" reminders use. */
  morning_hour?: number
  timezone?: string
  week_start?: number | string
  time_format?: string
  /** The server's calendar settings object (find-time reads `calendar.working_hours` from it). */
  calendar?: { working_hours?: { start: string; end: string; days: number[] } } & Record<string, unknown>
  working_hours?: { start: string; end: string; days: number[] }
}

export interface CalPrefs extends DatePrefs {
  /** The home zone (settings); `zone` is the display zone, which the zone chip may change. */
  homeZone: string
  secondaryZone: string | null
  defaultDuration: number
  snap: number
  workStart: number
  workEnd: number
  workDays: number[]
  dimPast: boolean
  weekNumbers: boolean
  conflicts: boolean
  videoTemplate: string
  morningHour: number
}

const hm = (s: string | undefined, dflt: number) => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(s ?? '')
  return m ? Number(m[1]) * 60 + Number(m[2]) : dflt
}

export function resolveCalPrefs(s: CalSettings, view: Pick<SavedView, 'displayZone' | 'secondaryZone'>): CalPrefs {
  const base = resolveDatePrefs(s)
  const wh = s.calendar_working_hours ?? s.calendar?.working_hours ?? s.working_hours
  return {
    ...base,
    homeZone: base.zone,
    zone: view.displayZone ?? base.zone,
    secondaryZone: view.secondaryZone,
    defaultDuration: [15, 30, 45, 60, 90, 120].includes(s.calendar_default_duration ?? 0) ? s.calendar_default_duration! : 60,
    snap: [5, 10, 15, 30].includes(s.calendar_snap ?? 0) ? s.calendar_snap! : 15,
    workStart: hm(wh?.start, 9 * 60),
    workEnd: hm(wh?.end, 17 * 60),
    workDays: wh?.days?.length ? wh.days.map((d) => d % 7) : DEFAULT_WORK_DAYS,
    dimPast: s.calendar_dim_past ?? false,
    weekNumbers: s.calendar_week_numbers ?? false,
    conflicts: s.calendar_conflicts ?? true,
    videoTemplate: s.calendar_video_template || 'https://meet.jit.si/mailr-{id}',
    morningHour: s.morning_hour ?? 9,
  }
}

// ---------- the saved view (this device) ----------

export interface SavedView {
  view: CalendarView
  ndays: number
  hideWeekends: boolean
  leftOpen: boolean
  tasksOnCalendar: boolean
  /** Task lists hidden from the grid (by list URL). */
  hiddenLists: string[]
  /** Calendars hidden on this device when the server cannot store visibility (by URL). */
  hiddenLocal: string[]
  /** The set before "Show only this", for "Show all". */
  beforeShowOnly: string[] | null
  displayZone: string | null
  secondaryZone: string | null
}

const KEY = 'mailr.calendar.view'

const DEFAULTS: SavedView = {
  view: 'today', ndays: 3, hideWeekends: false, leftOpen: true, tasksOnCalendar: false, hiddenLists: [],
  hiddenLocal: [], beforeShowOnly: null, displayZone: null, secondaryZone: null,
}

function load(): SavedView {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<SavedView>) } : DEFAULTS
  } catch {
    return DEFAULTS
  }
}

interface ViewStore extends SavedView {
  set: (patch: Partial<SavedView>) => void
}

export const useSavedView = create<ViewStore>((set, get) => ({
  ...load(),
  set: (patch) => {
    set(patch)
    const { set: _omit, ...rest } = { ...get() }
    void _omit
    try {
      localStorage.setItem(KEY, JSON.stringify(rest))
    } catch {
      /* private mode */
    }
  },
}))

export function useCalPrefs(): CalPrefs {
  const settings = useSettings() as unknown as CalSettings
  const displayZone = useSavedView((s) => s.displayZone)
  const secondaryZone = useSavedView((s) => s.secondaryZone)
  return useMemo(() => resolveCalPrefs(settings, { displayZone, secondaryZone }), [settings, displayZone, secondaryZone])
}

export type { Weekday }

/** The view Calendar opens on: the account's choice (setting calendar_view, any device), else this
 *  device's last view, else Today. */
export function startViewOf(settings: unknown, local: CalendarView): CalendarView {
  const v = (settings as { calendar_view?: string } | null)?.calendar_view
  return isCalendarView(v) ? v : local
}

/** Remember the view the person picks, on the account (calendar_view), so Calendar opens on it on
 *  every device. Called when they choose a view (a key, the view menu, the ribbon, the palette),
 *  not for every address opened, so a link or a bookmark does not change it. */
export function useRememberView(): (v: CalendarView) => void {
  const last = (useSettings() as { calendar_view?: string }).calendar_view
  const { mutate } = useUpdateSettings()
  return useCallback((v: CalendarView) => {
    if (v !== last) mutate({ calendar_view: v } as Partial<Settings>)
  }, [last, mutate])
}
