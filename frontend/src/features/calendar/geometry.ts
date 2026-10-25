// Pure calendar maths for the views: which days a view shows, moving between periods, the
// period title, the pixel ↔ minute mapping with snapping, drag results, scope decisions, typed
// time input and overlap checks. No React here, so vitest covers all of it (geometry.test.ts).

import {
  addDaysISO, addMonthsISO, daysFrom, diffDaysISO, formatDate, fromZoned, MONTH_SHORT, monthGrid, parseISODate,
  startOfMonthISO, startOfWeekISO, weekdayOf, zoneParts, type ISODate, type Weekday,
} from '../../lib/dates'
import type { CalendarView } from '../../lib/routes'
import { t as tr } from '../../i18n'

export const MIN = 60_000

// ---------- the days a view shows ----------

export interface ViewOptions {
  weekStart: Weekday
  /** N for the N-days view (2–7). */
  ndays: number
  hideWeekends: boolean
  /** Working days, 0 = Sunday … 6 = Saturday (Mon–Fri by default). */
  workDays: number[]
}

export const DEFAULT_WORK_DAYS = [1, 2, 3, 4, 5]

/** The day columns of a time-grid view, or the days of a month grid (whole weeks). */
export function viewDays(view: CalendarView, date: ISODate, o: ViewOptions): ISODate[] {
  switch (view) {
    case 'today':
    case 'day':
      return [date]
    case 'ndays':
      return daysFrom(date, Math.min(7, Math.max(1, o.ndays)))
    case 'workweek': {
      const days = daysFrom(startOfWeekISO(date, o.weekStart), 7).filter((d) => o.workDays.includes(weekdayOf(d)))
      return days.length ? days : daysFrom(startOfWeekISO(date, 1), 5)
    }
    case 'week': {
      const days = daysFrom(startOfWeekISO(date, o.weekStart), 7)
      if (!o.hideWeekends) return days
      const kept = days.filter((d) => weekdayOf(d) !== 0 && weekdayOf(d) !== 6)
      return kept
    }
    case 'month':
      return monthGrid(date, o.weekStart).flat()
    case 'schedule':
      return daysFrom(date, 30)
  }
}

/** The date range a view loads: [first, last] inclusive. */
export function viewSpan(view: CalendarView, date: ISODate, o: ViewOptions): { first: ISODate; last: ISODate } {
  const days = view === 'week' && o.hideWeekends ? daysFrom(startOfWeekISO(date, o.weekStart), 7) : viewDays(view, date, o)
  return { first: days[0], last: days[days.length - 1] }
}

/** The anchor date one period away (dir = 1 next, -1 previous). */
export function shiftDate(view: CalendarView, date: ISODate, dir: number, o: Pick<ViewOptions, 'ndays'>): ISODate {
  switch (view) {
    case 'today':
    case 'day':
      return addDaysISO(date, dir)
    case 'ndays':
      return addDaysISO(date, dir * Math.min(7, Math.max(1, o.ndays)))
    case 'workweek':
    case 'week':
      return addDaysISO(date, dir * 7)
    case 'month':
      return addMonthsISO(startOfMonthISO(date), dir)
    case 'schedule':
      return addDaysISO(date, dir * 30)
  }
}

/** ISO 8601 week number of a date (weeks start on Monday; week 1 holds the first Thursday). */
export function isoWeek(date: ISODate): number {
  const thursday = addDaysISO(date, 3 - ((weekdayOf(date) + 6) % 7))
  const { year } = parseISODate(thursday)
  return Math.floor(diffDaysISO(`${year}-01-01`, thursday) / 7) + 1
}

/**
 * The period title: "Thu 1 Oct 2026" (a day), "28 Sep – 4 Oct 2026" (several days),
 * "28 Dec 2026 – 3 Jan 2027" across years, "October 2026" (a month).
 */
export function periodTitle(view: CalendarView, date: ISODate, days: ISODate[]): string {
  if (view === 'month') return formatDate(date, 'monthYear')
  if (view === 'day' || days.length === 1) return formatDate(days[0] ?? date, 'dayYear')
  const a = parseISODate(days[0])
  const b = parseISODate(days[days.length - 1])
  if (a.year !== b.year) return `${formatDate(days[0], 'shortYear')} – ${formatDate(days[days.length - 1], 'shortYear')}`
  if (a.month === b.month) return `${a.day} – ${b.day} ${MONTH_SHORT[b.month - 1]} ${b.year}`
  return `${formatDate(days[0], 'short')} – ${formatDate(days[days.length - 1], 'short')} ${b.year}`
}

// ---------- pixels, minutes, snapping ----------

/** Rounds minutes to the nearest step (15 by default; Alt gives 5). */
export function snap(minutes: number, step: number): number {
  return Math.round(minutes / step) * step
}

/** Rounds down to the step (the slot a click falls in). */
export function snapDown(minutes: number, step: number): number {
  return Math.floor(minutes / step) * step
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n))
}

/** A rubber band from press to pointer, snapped, at least one step long, inside the day. */
export function rubberBand(pressMin: number, pointerMin: number, step: number): { start: number; end: number } {
  const a = snapDown(clamp(pressMin, 0, 1440 - step), step)
  const b = snap(clamp(pointerMin, 0, 1440), step)
  if (b > a) return { start: a, end: Math.max(a + step, b) }
  const s = snapDown(clamp(pointerMin, 0, 1440), step)
  return { start: Math.min(s, a), end: a + step }
}

/** A timed event on the grid: the column it starts in and its wall minutes (end may pass 1440). */
export interface GridSpan {
  day: number
  start: number
  end: number
}

export type DragKind = 'move' | 'resize-end' | 'resize-start'

/** The minimum length a resize may leave (5 minutes). */
export const MIN_RESIZE = 5

/**
 * Where a drag lands. `dMin` is the pointer's vertical movement in minutes, `dDay` the column
 * change. Moves keep the duration and snap the start; resizes snap the moved edge and keep at
 * least MIN_RESIZE minutes. The start of a move stays inside the day it lands in.
 */
export function dragResult(kind: DragKind, orig: GridSpan, dMin: number, dDay: number, step: number, days: number): GridSpan {
  const dur = orig.end - orig.start
  if (kind === 'move') {
    const day = clamp(orig.day + dDay, 0, days - 1)
    const start = clamp(snap(orig.start + dMin, step), 0, 1440 - Math.min(step, dur))
    return { day, start, end: start + dur }
  }
  if (kind === 'resize-end') {
    const end = Math.max(orig.start + MIN_RESIZE, snap(orig.end + dMin, step))
    return { ...orig, end: Math.min(end, 1440 * 2) }
  }
  const start = Math.min(orig.end - MIN_RESIZE, snap(orig.start + dMin, step))
  return { ...orig, start: Math.max(0, start) }
}

/** Keyboard drag (Shift+arrows): one step, or a day; Alt+Shift+↑/↓ resizes the end. */
export function keyboardDrag(span: GridSpan, key: 'up' | 'down' | 'left' | 'right', resize: boolean, step: number, days: number): GridSpan {
  if (resize) {
    const end = span.end + (key === 'down' ? step : key === 'up' ? -step : 0)
    return { ...span, end: Math.max(span.start + MIN_RESIZE, end) }
  }
  if (key === 'left' || key === 'right') return { ...span, day: clamp(span.day + (key === 'right' ? 1 : -1), 0, days - 1) }
  const d = key === 'down' ? step : -step
  const dur = span.end - span.start
  const start = clamp(span.start + d, 0, 1440 - Math.min(step, dur))
  return { ...span, start, end: start + dur }
}

/** The instant for a wall time on a grid day (minutes may pass midnight: 1500 = 01:00 next day). */
export function gridInstant(day: ISODate, minutes: number, zone: string): Date {
  const extra = Math.floor(minutes / 1440)
  const m = minutes - extra * 1440
  const { year, month, day: d } = parseISODate(addDaysISO(day, extra))
  return fromZoned({ year, month, day: d, hour: Math.floor(m / 60), minute: m % 60 }, zone)
}

/** A timed event moved by whole days in a zone: the same wall-clock start on the new day and
 *  the same length, also across a daylight-saving change (n × 24 h would move it an hour). */
export function moveDays(start: Date, end: Date, firstDay: ISODate, n: number, zone: string): { start: Date, end: Date } {
  const p = zoneParts(start, zone)
  const moved = gridInstant(addDaysISO(firstDay, n), p.hour * 60 + p.minute, zone)
  return { start: moved, end: new Date(moved.getTime() + (end.getTime() - start.getTime())) }
}

/** "2026-10-01T15:30" for an instant in a zone (the input the overlap layout's splitDays reads). */
export function wallString(instant: Date, zone: string): string {
  const p = zoneParts(instant, zone)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`
}

// ---------- scope ----------

export interface ScopeFacts {
  recurring: boolean
  /** The series has one occurrence (COUNT=1). */
  single?: boolean
  /** The change is to the rule or the series end. */
  ruleChange?: boolean
}

/** Whether a change asks "This event / This and following / All events". */
export function needsScope(f: ScopeFacts): boolean {
  return f.recurring && !f.single && !f.ruleChange
}

/** Deleting (or changing) from the first occurrence with "following" means all of them. */
export function resolveScope(scope: 'this' | 'following' | 'all', isFirst: boolean): 'this' | 'following' | 'all' {
  return scope === 'following' && isFirst ? 'all' : scope
}

/** The series has exactly one occurrence. */
export function singleOccurrence(rrule: string | null | undefined): boolean {
  return !!rrule && /(^|;)COUNT=1(;|$)/i.test(rrule)
}

// ---------- typed times (the web's typed when fields) ----------

/**
 * A typed time as minutes since midnight: "3p", "3pm", "15", "1530", "3:30 pm", "15:30", "noon",
 * "midnight", "9a". Null when it is not a time. A bare "3" reads as 3:00 (the caller may prefer PM
 * for working hours; see preferAfternoon).
 */
export function parseTimeText(text: string): number | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, ' ').replace(/\./g, '')
  if (!t) return null
  if (t === 'noon' || t === 'midday') return 720
  if (t === 'midnight') return 0
  const m = /^(\d{1,2})(?::?(\d{2}))?\s*(a|am|p|pm)?$/.exec(t)
  if (!m) return null
  let h = Number(m[1])
  const min = m[2] ? Number(m[2]) : 0
  // "1530" reads as 15:30 and "930" as 9:30 through the optional colon.
  if (min > 59) return null
  const mer = m[3]?.[0]
  if (mer) {
    if (h < 1 || h > 12) return null
    if (mer === 'a') h = h === 12 ? 0 : h
    else h = h === 12 ? 12 : h + 12
  } else if (h > 23) return null
  return h * 60 + min
}

/** "1 h 30 min", "45 min", "2 h", "1 day". */
export function formatDuration(minutes: number): string {
  if (minutes <= 0) return tr('calendar.geometry.n0Min')
  if (minutes % 1440 === 0) return tr('calendar.geometry.day', { count: minutes / 1440 })
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (!h) return tr('calendar.geometry.min', { m })
  return m ? tr('calendar.geometry.hMin', { h, m }) : `${h} h`
}

/** Every `step` minutes of a day, for the time popup. */
export function timeSlots(step = 15): number[] {
  return Array.from({ length: Math.floor(1440 / step) }, (_, i) => i * step)
}

// ---------- overlaps (time conflicts) ----------

export interface Busy {
  key: string
  summary: string
  start: Date
  end: Date
  allDay?: boolean
  transparent?: boolean
  declined?: boolean
  cancelled?: boolean
  calendarName?: string
}

/** Busy, non-declined, timed events that overlap [start, end), excluding the event itself. */
export function overlapping(items: Busy[], start: Date, end: Date, exclude?: string): Busy[] {
  return items
    .filter((b) => b.key !== exclude && !b.allDay && !b.transparent && !b.declined && !b.cancelled)
    .filter((b) => b.start < end && b.end > start)
    .sort((a, b) => a.start.getTime() - b.start.getTime())
}

/** Weeks of a month grid that the month needs: 5 when it fits, else 6. */
export function monthWeeks(date: ISODate, weekStart: Weekday): ISODate[][] {
  return monthGrid(date, weekStart)
}

// ---------- multi-day bars (month rows, the all-day strip) ----------

export interface BarInput {
  key: string
  first: ISODate
  last: ISODate
  /** Sort hint: all-day first, then earlier start, then longer. */
  allDay: boolean
  start: number
}

export interface Bar {
  key: string
  lane: number
  /** Column range inside the row, inclusive. */
  from: number
  to: number
  /** It began before the row / goes on after it. */
  clippedStart: boolean
  clippedEnd: boolean
}

/** Packs items into lanes across a row of days (a week, or the all-day strip's columns). */
export function layoutBars(items: BarInput[], days: ISODate[]): Bar[] {
  if (!days.length) return []
  const first = days[0]
  const last = days[days.length - 1]
  const index = new Map(days.map((d, i) => [d, i]))
  const col = (d: ISODate, edge: 'from' | 'to') => {
    if (index.has(d)) return index.get(d)!
    // Days hidden from the row (weekends): the nearest shown column inside the span.
    if (edge === 'from') return days.findIndex((x) => x >= d)
    for (let i = days.length - 1; i >= 0; i--) if (days[i] <= d) return i
    return -1
  }
  const sorted = items
    .filter((i) => i.last >= first && i.first <= last)
    .sort((a, b) => a.first.localeCompare(b.first) || diffDaysISO(b.first, b.last) - diffDaysISO(a.first, a.last) ||
      Number(b.allDay) - Number(a.allDay) || a.start - b.start || a.key.localeCompare(b.key))
  const lanes: number[][] = []
  const out: Bar[] = []
  for (const it of sorted) {
    const from = col(it.first < first ? first : it.first, 'from')
    const to = col(it.last > last ? last : it.last, 'to')
    if (from < 0 || to < 0 || to < from) continue
    let lane = lanes.findIndex((cols) => cols.every((c) => c < from || c > to))
    if (lane < 0) {
      lane = lanes.length
      lanes.push([])
    }
    for (let c = from; c <= to; c++) lanes[lane].push(c)
    out.push({ key: it.key, lane, from, to, clippedStart: it.first < first, clippedEnd: it.last > last })
  }
  return out
}
