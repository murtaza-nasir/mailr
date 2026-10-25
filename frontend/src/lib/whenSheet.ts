// The when sheet: the preset groups
// (Later, Same time, Next, Before it starts), the custom amount + unit with its base (From now |
// From due time), and the live result line. The Android twin is data/reminders/QuickChoices.kt
// (WhenSheet); both follow the same table and both have tests for it. Times are local to the
// browser, as in quickChoices.ts. ACT task.snooze @web.sheet, ACT task.reschedule @web.sheet, ACT event.snooze @web.sheet,
// ACT event.reschedule @web.sheet, ACT event.remind_before @web.sheet.

import { offsetLabel, offsets } from './quickChoices'
import { t as tr, appLocale } from '../i18n'
import { clockOpts } from './dates'
import { isRtl } from './rtl'

export type Verb = 'snooze' | 'reschedule' | 'remind'
export type Base = 'now' | 'due'
export type Unit = 'minutes' | 'hours' | 'days' | 'weeks'

export interface Tile {
  id: string
  label: string
  at: Date
  /** A date-only due (tasks): the time part of [at] is not used. */
  dateOnly: boolean
  /** Remind before: the offset in minutes. */
  minutes?: number
}

export interface Group {
  id: 'later' | 'same' | 'next' | 'before'
  label: string
  tiles: Tile[]
}

export interface SheetSpec {
  verb: Verb
  kind: 'task' | 'event'
  now: Date
  /** The due (a task) or the start (an event); null for a task without a due. */
  anchor: Date | null
  /** The task's due is date-only. */
  dateOnly?: boolean
  morningHour?: number
}

const MIN = 60_000
export const EVENING = 18

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i)
}

export const UNITS: { id: Unit; label: string; amounts: number[] }[] = [
  // Each unit covers its whole useful range on its own (the Android twin is SpanUnit).
  { id: 'minutes', label: 'minutes', amounts: [1, 2, 3, 4, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55] },
  { id: 'hours', label: 'hours', amounts: range(1, 23) },
  { id: 'days', label: 'days', amounts: range(1, 30) },
  { id: 'weeks', label: 'weeks', amounts: range(1, 8) },
]

/** The amount kept when the unit changes: [n] itself when [unit] has it, else the closest. */
export function nearest(unit: Unit, n: number): number {
  const list = UNITS.find((u) => u.id === unit)!.amounts
  return list.reduce((a, b) => (Math.abs(b - n) < Math.abs(a - n) ? b : a), list[0])
}

function atTime(day: Date, h: number, m = 0): Date {
  const x = new Date(day)
  x.setHours(h, m, 0, 0)
  return x
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}

/** One month later; the 31st becomes the last day of a shorter month. */
function addMonths(d: Date, n: number): Date {
  const x = new Date(d)
  const day = x.getDate()
  x.setDate(1)
  x.setMonth(x.getMonth() + n)
  const last = new Date(x.getFullYear(), x.getMonth() + 1, 0).getDate()
  x.setDate(Math.min(day, last))
  return x
}

/** The next given weekday (0 = Sunday), strictly after today. */
function nextDow(d: Date, dow: number): Date {
  return addDays(d, ((dow - d.getDay() + 7) % 7) || 7)
}

const trunc = (d: Date) => {
  const x = new Date(d)
  x.setSeconds(0, 0)
  return x
}

const startOfDay = (d: Date) => atTime(d, 0)

/** Leaves out tiles in the past and tiles equal to an earlier one (across groups). */
function tidy(groups: Group[], now: Date): Group[] {
  const seen = new Set<string>()
  return groups
    .map((g) => ({
      ...g,
      tiles: g.tiles.filter((t) => {
        const past = t.dateOnly ? startOfDay(t.at).getTime() < startOfDay(now).getTime() : t.at.getTime() <= now.getTime()
        const key = t.dateOnly ? `d${startOfDay(t.at).getTime()}` : `t${t.at.getTime()}`
        if (past || seen.has(key)) return false
        seen.add(key)
        return true
      }),
    }))
    .filter((g) => g.tiles.length > 0)
}

/** The preset groups of the when sheet for one verb. */
export function sheetGroups(spec: SheetSpec): Group[] {
  const { verb, kind, now, anchor } = spec
  const morning = spec.morningHour ?? 9
  const dateOnly = !!spec.dateOnly && kind === 'task'
  const plus = (m: number): Tile => ({ id: `${m}m`, label: m < 60 ? tr('lib.whenSheet.inMin', { m }) : tr('lib.whenSheet.inHour', { count: m / 60 }), at: new Date(trunc(now).getTime() + m * MIN), dateOnly: false })

  // ACT event.remind_before: the offsets still ahead.
  if (verb === 'remind') {
    if (!anchor) return []
    return [{ id: 'before', label: tr('lib.whenSheet.beforeItStarts'), tiles: offsets(now, anchor).map((m) => ({ id: `o${m}`, label: offsetLabel(m), at: new Date(anchor.getTime() - m * MIN), dateOnly: false, minutes: m })) }]
  }

  // ACT event.snooze: only Later, counted from now.
  if (kind === 'event' && verb === 'snooze') {
    const tiles = anchor && anchor.getTime() > now.getTime()
      ? [plus(5), plus(10), ...(anchor.getTime() - now.getTime() > 2 * MIN ? [{ id: 'start', label: tr('lib.whenSheet.whenItStarts'), at: anchor, dateOnly: false }] : [])]
      : [plus(5), plus(15)]
    return tidy([{ id: 'later', label: tr('lib.whenSheet.later'), tiles }], now)
  }

  const evening: Tile[] = now.getHours() < 17 ? [{ id: 'evening', label: tr('lib.whenSheet.thisEvening'), at: atTime(now, EVENING), dateOnly: false }] : []
  const later: Tile[] = kind === 'event'
    // ACT event.reschedule: shifts of the start.
    ? (anchor ? [
        { id: 'p15', label: tr('lib.whenSheet.n15MinLater'), at: new Date(anchor.getTime() + 15 * MIN), dateOnly: false },
        { id: 'p60', label: tr('lib.whenSheet.n1HourLater'), at: new Date(anchor.getTime() + 60 * MIN), dateOnly: false },
      ] : [])
    : verb === 'snooze' ? [plus(10), plus(60), plus(180), ...evening] : [plus(60), plus(180), ...evening]

  // Same time: the original time of day, on days counted from today.
  const keepDate = dateOnly && verb === 'reschedule'
  const same: Tile[] = []
  if (anchor && (!dateOnly || keepDate)) {
    const at = (d: Date) => (keepDate ? atTime(d, morning) : atTime(d, anchor.getHours(), anchor.getMinutes()))
    same.push(
      { id: 'same-tomorrow', label: tr('common.tomorrow'), at: at(addDays(now, 1)), dateOnly: keepDate },
      { id: 'same-week', label: tr('common.nextWeek'), at: at(addDays(now, 7)), dateOnly: keepDate },
      { id: 'same-month', label: tr('common.nextMonth'), at: at(addMonths(now, 1)), dateOnly: keepDate },
    )
  }

  const weekend = now.getDay() === 6 || now.getDay() === 0 ? tr('lib.whenSheet.nextWeekend') : tr('common.thisWeekend')
  const next: Tile[] = [
    { id: 'morning', label: tr('common.tomorrowMorning'), at: atTime(addDays(now, 1), morning), dateOnly: keepDate },
    { id: 'monday', label: tr('lib.whenSheet.nextMonday'), at: atTime(nextDow(now, 1), morning), dateOnly: keepDate },
    { id: 'weekend', label: weekend, at: atTime(nextDow(now, 6), morning), dateOnly: keepDate },
  ]
  return tidy([
    { id: 'later', label: tr('lib.whenSheet.later'), tiles: later },
    { id: 'same', label: tr('lib.whenSheet.sameTime'), tiles: same },
    { id: 'next', label: tr('common.next'), tiles: next },
  ], now)
}

/** Whether the From now | From due time toggle is shown. */
export function hasBaseToggle(spec: SheetSpec): boolean {
  if (!spec.anchor || spec.verb === 'remind') return false
  return !(spec.kind === 'event' && spec.verb === 'snooze')
}

/** "From due time" for a task, "From start" for an event. */
export const baseLabel = (kind: 'task' | 'event') => (kind === 'task' ? tr('lib.whenSheet.fromDueTime') : tr('lib.whenSheet.fromStart'))

/** The default base: now for a snooze, the due or start for a reschedule. */
export function defaultBase(spec: SheetSpec): Base {
  return hasBaseToggle(spec) && spec.verb === 'reschedule' ? 'due' : 'now'
}

const BASE_KEY = (verb: Verb) => `mailr.whenSheet.base.${verb}`

/** The last base the user chose for [verb] on this browser, else the default. */
export function rememberedBase(spec: SheetSpec): Base {
  if (!hasBaseToggle(spec)) return 'now'
  try {
    const v = localStorage.getItem(BASE_KEY(spec.verb))
    if (v === 'now' || v === 'due') return v
  } catch {
    // Storage can be unavailable; the default applies.
  }
  return defaultBase(spec)
}

export function rememberBase(verb: Verb, base: Base): void {
  try {
    localStorage.setItem(BASE_KEY(verb), base)
  } catch {
    // Not kept; the default applies next time.
  }
}

/** The start values of the wheels: 15 min (snooze), 1 day (reschedule), 30 min before (remind). */
export function initialAmount(verb: Verb): { amount: number; unit: Unit } {
  return verb === 'reschedule' ? { amount: 1, unit: 'days' } : verb === 'remind' ? { amount: 30, unit: 'minutes' } : { amount: 15, unit: 'minutes' }
}

function addUnit(t: Date, unit: Unit, n: number): Date {
  switch (unit) {
    case 'minutes': return new Date(t.getTime() + n * MIN)
    case 'hours': return new Date(t.getTime() + n * 60 * MIN)
    case 'days': return addDays(t, n)
    case 'weeks': return addDays(t, 7 * n)
  }
}

/**
 * The custom moment: [amount] [unit] after the base (now or the anchor), or before the start for
 * Remind before. Minutes and hours are exact; days and weeks keep the time of day. A date-only
 * task stays date-only for days and weeks.
 */
export function customResult(spec: SheetSpec, base: Base, amount: number, unit: Unit): { at: Date; dateOnly: boolean } {
  const anchor = spec.anchor
  if (spec.verb === 'remind' && anchor) {
    const ms = addUnit(spec.now, unit, amount).getTime() - spec.now.getTime()
    return { at: new Date(anchor.getTime() - ms), dateOnly: false }
  }
  const from = base === 'due' && anchor && hasBaseToggle(spec) ? anchor : trunc(spec.now)
  const day = unit === 'days' || unit === 'weeks'
  const dateOnly = !!spec.dateOnly && spec.kind === 'task' && spec.verb === 'reschedule' && day
  return { at: addUnit(from, unit, amount), dateOnly }
}

/** "in 8 days", "in 3 h 20 min", "2 days ago". */
export function relative(at: Date, now: Date, dateOnly = false): string {
  const days = Math.round((startOfDay(at).getTime() - startOfDay(now).getTime()) / 86_400_000)
  if (dateOnly || Math.abs(at.getTime() - now.getTime()) >= 24 * 60 * MIN) {
    if (days === 0) return 'today'
    if (days > 0) return tr('lib.whenSheet.inDays', { count: days })
    return days === -1 ? tr('lib.whenSheet.n1DayAgo') : tr('lib.whenSheet.daysAgo', { value: -days })
  }
  // Whole minutes from the current minute: the custom result counts from now without its
  // seconds, so 12 hours reads "in 12 h", not "in 11 h 59 min".
  const minute = new Date(now)
  minute.setSeconds(0, 0)
  const mins = Math.round((at.getTime() - minute.getTime()) / MIN)
  const a = Math.abs(mins)
  const text = a < 60 ? tr('lib.whenSheet.min', { a }) : a % 60 ? tr('lib.whenSheet.hMin', { floor: Math.floor(a / 60), value: a % 60 }) : `${a / 60} h`
  return mins >= 0 ? tr('lib.whenSheet.in', { text }) : tr('lib.whenSheet.ago', { text })
}

/** "Today, 4:40 PM", "Tomorrow, 9:00 AM", "Fri 9 Oct, 12:00 PM". */
export function moment(at: Date, now: Date, dateOnly = false, time?: (d: Date) => string): string {
  const days = Math.round((startOfDay(at).getTime() - startOfDay(now).getTime()) / 86_400_000)
  const day = days === 0 ? tr('common.today') : days === 1 ? tr('common.tomorrow') : at.toLocaleDateString(appLocale(), { weekday: 'short', day: 'numeric', month: 'short' })
  if (dateOnly) return day
  return `${day}, ${time ? time(at) : at.toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })}`
}

/** The live result: "→ Fri 9 Oct, 12:00 PM · in 8 days" (the arrow points toward the end: "←" right to left). */
export function resultLine(at: Date, now: Date, dateOnly = false, time?: (d: Date) => string): string {
  return `${isRtl() ? '←' : '→'} ${moment(at, now, dateOnly, time)} · ${relative(at, now, dateOnly)}`
}

/** The inline status badge for an overdue due: "Overdue 4 h", "Overdue 25 min", "Overdue 2 d". */
export function overdueText(due: Date, now: Date, dateOnly = false): string | null {
  if (dateOnly) {
    const days = Math.round((startOfDay(now).getTime() - startOfDay(due).getTime()) / 86_400_000)
    return days > 0 ? tr('lib.whenSheet.overdueD', { days }) : null
  }
  const mins = Math.floor((now.getTime() - due.getTime()) / MIN)
  if (mins < 1) return null
  if (mins < 60) return tr('lib.whenSheet.overdueMin', { mins })
  if (mins < 24 * 60) return tr('lib.whenSheet.overdueH', { floor: Math.floor(mins / 60) })
  return tr('lib.whenSheet.overdueD2', { floor: Math.floor(mins / (24 * 60)) })
}

/** What the verb changes, under the header. */
export function verbHint(spec: SheetSpec, anchorText: string | null): string {
  if (spec.verb === 'remind') return tr('lib.whenSheet.addsAReminderTheEvent')
  if (spec.verb === 'snooze') {
    if (spec.kind === 'event') return tr('lib.whenSheet.onlyTheReminderMovesThe')
    return anchorText ? tr('lib.whenSheet.onlyTheReminderMovesThe2', { anchorText }) : tr('lib.whenSheet.onlyTheReminderMovesThe3')
  }
  return spec.kind === 'task' ? tr('lib.whenSheet.theDueMovesItsReminders') : tr('lib.whenSheet.theEventMovesItsLength')
}
