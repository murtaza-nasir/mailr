// Zone-aware date helpers for the Calendar and Tasks apps (plan §8). Intl only, no dependency:
// every IANA zone the browser knows works, and DST comes from the browser's tz database.
//
// Two kinds of value appear here:
// - an instant is a JS Date (an absolute moment);
// - an ISODate is a plain calendar date "YYYY-MM-DD" with no zone (all-day values, grid days).
// Wall-clock maths (start of day, +1 day, week ranges) always names the zone it runs in.

import { appLocale, language, PSEUDO, t as tr } from '../i18n'

export type ISODate = string

/** Weekday numbers follow Date#getUTCDay: 0 = Sunday … 6 = Saturday. */
export type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6

export interface DatePrefs {
  /** IANA zone, e.g. "America/Chicago". */
  zone: string
  /** 12-hour clock with AM/PM (true) or 24-hour (false). */
  hour12: boolean
  /** First day of the week. */
  weekStart: Weekday
}

export interface WallTime {
  year: number
  month: number // 1-12
  day: number
  hour?: number
  minute?: number
  second?: number
  millisecond?: number
}

export interface ZonedParts extends Required<WallTime> {
  weekday: Weekday
}

const MIN = 60_000
const DAY = 86_400_000

/** The weekday names en-US formatToParts gives (zoneParts reads them back); never shown. */
const PARTS_WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] // i18n-ignore: Intl en-US output, not text

/** Names of the weekdays (Sunday first) or months in the app's language (src/i18n, Intl). */
function names(opts: Intl.DateTimeFormatOptions, n: number, at: (i: number) => number): readonly string[] {
  let f: Intl.DateTimeFormat
  try {
    f = new Intl.DateTimeFormat(appLocale(), { ...opts, timeZone: 'UTC' })
  } catch {
    f = new Intl.DateTimeFormat('en-US', { ...opts, timeZone: 'UTC' })
  }
  return Array.from({ length: n }, (_, i) => f.format(new Date(at(i))))
}

// 1 Jan 2023 was a Sunday.
export const WEEKDAY_SHORT = names({ weekday: 'short' }, 7, (i) => Date.UTC(2023, 0, 1 + i))
export const WEEKDAY_LONG = names({ weekday: 'long' }, 7, (i) => Date.UTC(2023, 0, 1 + i))
export const MONTH_SHORT = names({ month: 'short' }, 12, (i) => Date.UTC(2023, i, 15))
export const MONTH_LONG = names({ month: 'long' }, 12, (i) => Date.UTC(2023, i, 15))

/** English keeps the app's own date order ("Tue 6 Oct"); another language gets its own (Intl). */
const ownStyle = () => language() === 'en' || language() === PSEUDO

// ---------- zones ----------

// Every zone a formatter gets passes through here (9 Oct 2026). Outlook's Windows names such as
// "Central Standard Time" reached the browser, whose formatter throws a RangeError on them, and
// the whole screen failed. The rule (eslint no-restricted-syntax): a `timeZone:` option is written
// only in this file; other code calls safeZone / dateTimeFormat / formatInstant.

const fmtCache = new Map<string, Intl.DateTimeFormat>()

/** The en-US parts formatter for a zone; throws a RangeError for a zone the browser refuses. */
function rawPartsFormat(zone: string): Intl.DateTimeFormat {
  let f = fmtCache.get(zone)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone: zone, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', second: 'numeric', weekday: 'short',
    })
    fmtCache.set(zone, f)
  }
  return f
}

/** The browser's own zone (UTC when the browser does not say). */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function isValidZone(zone: string | null | undefined): zone is string {
  if (!zone || typeof zone !== 'string') return false
  try {
    rawPartsFormat(zone)
    return true
  } catch {
    return false
  }
}

let userZone: string | null = null
const reported = new Set<string>()

/** The account's zone, set by the signed-in shell; the fallback for a zone the browser refuses. */
export function setUserZone(zone: string | null | undefined): void {
  userZone = isValidZone(zone) ? zone : null
}

/**
 * `zone` when the browser accepts it, else the user's zone (then the browser's). A refused name
 * is logged once to the console with its value, so a bad record can be traced.
 */
export function safeZone(zone: string | null | undefined): string {
  if (isValidZone(zone)) return zone
  if (zone && !reported.has(String(zone))) {
    reported.add(String(zone))
    console.warn(`[dates] unknown time zone ${JSON.stringify(zone)}; showing the user's zone`) // i18n-ignore: console only
  }
  return isValidZone(userZone) ? userZone : browserZone()
}

/** Intl options without a zone: the zone is its own argument, so it is always checked. */
export type ZonelessFormatOptions = Omit<Intl.DateTimeFormatOptions, 'timeZone'>

/**
 * An Intl.DateTimeFormat in `zone` checked by safeZone (no `zone`: the browser's own). A locale
 * the engine refuses falls back to en-US. Every formatter that names a zone is made here.
 */
export function dateTimeFormat(locale: string | string[] | undefined, opts: ZonelessFormatOptions = {}, zone?: string | null): Intl.DateTimeFormat {
  const o: Intl.DateTimeFormatOptions = zone === undefined ? { ...opts } : { ...opts, timeZone: safeZone(zone) }
  try {
    return new Intl.DateTimeFormat(locale, o)
  } catch {
    return new Intl.DateTimeFormat('en-US', o)
  }
}

/** An instant formatted with Intl options in a checked zone: the safe form of
 *  `date.toLocaleString(locale, { …, timeZone })` (and of toLocaleDateString / toLocaleTimeString,
 *  whose default fields the caller spells out). */
export function formatInstant(instant: Date, locale: string | string[] | undefined, opts: ZonelessFormatOptions = {}, zone?: string | null): string {
  return dateTimeFormat(locale, opts, zone).format(instant)
}

/** The parts formatter for a zone, never throwing: a refused zone reads as safeZone's fallback. */
function partsFormat(zone: string): Intl.DateTimeFormat {
  return rawPartsFormat(safeZone(zone))
}

/** Year, month, day, time and weekday of an instant as the clock in `zone` shows it. */
export function zoneParts(instant: Date, zone: string): ZonedParts {
  const out: Record<string, string> = {}
  for (const p of partsFormat(zone).formatToParts(instant)) out[p.type] = p.value
  const hour = Number(out.hour) % 24 // some engines print midnight as 24
  return {
    year: Number(out.year), month: Number(out.month), day: Number(out.day),
    hour, minute: Number(out.minute), second: Number(out.second), millisecond: instant.getUTCMilliseconds(),
    weekday: PARTS_WEEKDAYS.indexOf(out.weekday) as Weekday,
  }
}

/** The zone's offset from UTC at this instant, in minutes (Chicago in summer: -300). */
export function zoneOffset(instant: Date, zone: string): number {
  const p = zoneParts(instant, zone)
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)
  return Math.round((asUtc - Math.floor(instant.getTime() / 1000) * 1000) / MIN)
}

function wallUtc(w: WallTime): number {
  return Date.UTC(w.year, w.month - 1, w.day, w.hour ?? 0, w.minute ?? 0, w.second ?? 0, w.millisecond ?? 0)
}

function sameWall(a: ZonedParts, w: WallTime): boolean {
  return a.year === w.year && a.month === w.month && a.day === w.day && a.hour === (w.hour ?? 0) && a.minute === (w.minute ?? 0)
}

/**
 * The instant at which `zone`'s clock shows this wall time. DST edges follow Temporal's rules:
 * a repeated hour (autumn) gives the earlier instant unless `later` is asked for; a skipped hour
 * (spring) moves forward by the gap ("compatible"), so 02:30 on a spring-forward night is 03:30.
 */
export function fromZoned(w: WallTime, zone: string, disambiguation: 'compatible' | 'earlier' | 'later' = 'compatible'): Date {
  const utc = wallUtc(w)
  const before = zoneOffset(new Date(utc - DAY), zone)
  const after = zoneOffset(new Date(utc + DAY), zone)
  const cands = [...new Set([utc - before * MIN, utc - after * MIN])]
    .filter((t) => sameWall(zoneParts(new Date(t), zone), w))
    .sort((a, b) => a - b)
  if (cands.length) return new Date(disambiguation === 'later' ? cands[cands.length - 1] : cands[0])
  // In a gap: the offset in force before the gap lands past it; 'earlier' lands before it.
  return new Date(disambiguation === 'earlier' ? utc - after * MIN : utc - before * MIN)
}

// ---------- plain dates ----------

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

export function isISODate(s: unknown): boolean {
  return typeof s === 'string' && ISO_DATE.test(s)
}

export function parseISODate(s: ISODate): { year: number; month: number; day: number } {
  const m = ISO_DATE.exec(s.slice(0, 10))
  if (!m) throw new Error(tr('lib.dates.notADate', { s }))
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) }
}

const pad = (n: number, w = 2) => String(n).padStart(w, '0')

export function isoDate(year: number, month: number, day: number): ISODate {
  // Normalises overflow (day 32 → next month) through Date.UTC.
  const d = new Date(Date.UTC(year, month - 1, day))
  return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

export function addDaysISO(date: ISODate, n: number): ISODate {
  const { year, month, day } = parseISODate(date)
  return isoDate(year, month, day + n)
}

export function addMonthsISO(date: ISODate, n: number): ISODate {
  // Clamps to the month's last day: 31 Jan + 1 month = 28/29 Feb.
  const { year, month, day } = parseISODate(date)
  const first = parseISODate(isoDate(year, month + n, 1))
  return isoDate(first.year, first.month, Math.min(day, daysInMonth(first.year, first.month)))
}

export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

export function weekdayOf(date: ISODate): Weekday {
  const { year, month, day } = parseISODate(date)
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay() as Weekday
}

/** Whole days from `a` to `b` (b − a). */
export function diffDaysISO(a: ISODate, b: ISODate): number {
  const x = parseISODate(a)
  const y = parseISODate(b)
  return Math.round((Date.UTC(y.year, y.month - 1, y.day) - Date.UTC(x.year, x.month - 1, x.day)) / DAY)
}

/** The calendar date `zone`'s clock shows at this instant. */
export function toISODate(instant: Date, zone: string): ISODate {
  const p = zoneParts(instant, zone)
  return isoDate(p.year, p.month, p.day)
}

export function todayISO(zone: string, now: Date = new Date()): ISODate {
  return toISODate(now, zone)
}

// ---------- wall-clock arithmetic in a zone ----------

/** Midnight (or the first moment of the day, where midnight is skipped) in `zone`. */
export function startOfDay(instant: Date | ISODate, zone: string): Date {
  const d = typeof instant === 'string' ? parseISODate(instant) : zoneParts(instant, zone)
  return fromZoned({ year: d.year, month: d.month, day: d.day }, zone)
}

/** Same wall time, `n` calendar days later in `zone` (09:00 stays 09:00 across DST). */
export function addDays(instant: Date, n: number, zone: string): Date {
  const p = zoneParts(instant, zone)
  const d = parseISODate(isoDate(p.year, p.month, p.day + n))
  return fromZoned({ ...d, hour: p.hour, minute: p.minute, second: p.second, millisecond: p.millisecond }, zone)
}

/** The instants a calendar day spans in `zone`: [start, end). 23 or 25 hours on DST days. */
export function dayRange(date: ISODate, zone: string): { start: Date; end: Date } {
  return { start: startOfDay(date, zone), end: startOfDay(addDaysISO(date, 1), zone) }
}

/** Minutes since local midnight (a DST day still counts wall minutes: 09:00 is 540). */
export function minutesOfDay(instant: Date, zone: string): number {
  const p = zoneParts(instant, zone)
  return p.hour * 60 + p.minute
}

export function sameDay(a: Date, b: Date, zone: string): boolean {
  return toISODate(a, zone) === toISODate(b, zone)
}

// ---------- weeks, months, ranges ----------

/** The first day of the week holding `date`. */
export function startOfWeekISO(date: ISODate, weekStart: Weekday): ISODate {
  return addDaysISO(date, -((weekdayOf(date) - weekStart + 7) % 7))
}

/** `n` consecutive dates from `start`. */
export function daysFrom(start: ISODate, n: number): ISODate[] {
  return Array.from({ length: n }, (_, i) => addDaysISO(start, i))
}

export interface DateRange {
  /** The dates shown, in order. */
  days: ISODate[]
  /** The instants they cover in the zone: [start, end). */
  start: Date
  end: Date
}

export function rangeOfDays(first: ISODate, n: number, zone: string): DateRange {
  return { days: daysFrom(first, n), start: startOfDay(first, zone), end: startOfDay(addDaysISO(first, n), zone) }
}

/** The week holding `date` (an ISODate, or an instant read in `zone`), by the week-start setting. */
export function weekRange(date: ISODate | Date, zone: string, weekStart: Weekday): DateRange {
  const d = typeof date === 'string' ? date : toISODate(date, zone)
  return rangeOfDays(startOfWeekISO(d, weekStart), 7, zone)
}

/** Monday to Friday of the week holding `date` (whatever the week-start setting). */
export function workWeekRange(date: ISODate | Date, zone: string): DateRange {
  const d = typeof date === 'string' ? date : toISODate(date, zone)
  return rangeOfDays(startOfWeekISO(d, 1), 5, zone)
}

export function startOfMonthISO(date: ISODate): ISODate {
  const { year, month } = parseISODate(date)
  return isoDate(year, month, 1)
}

/** The weeks a month grid shows: whole weeks from the one holding the 1st to the one holding the last day. */
export function monthGrid(date: ISODate, weekStart: Weekday): ISODate[][] {
  const { year, month } = parseISODate(date)
  const first = startOfWeekISO(isoDate(year, month, 1), weekStart)
  const last = isoDate(year, month, daysInMonth(year, month))
  const weeks: ISODate[][] = []
  for (let d = first; d <= last; d = addDaysISO(d, 7)) weeks.push(daysFrom(d, 7))
  return weeks
}

// ---------- parsing ----------

const ISO_DT = /^(\d{4})-?(\d{2})-?(\d{2})[T ](\d{2}):?(\d{2})(?::?(\d{2})(?:[.,](\d{1,9}))?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$/i

/**
 * An ISO 8601 string as an instant: "2026-10-01T15:00:00+00:00", "…Z", "…-05:00", "…+0530",
 * iCalendar's "20261001T150000Z". A string with no offset is a wall time in `zone`; a bare
 * date is the start of that day in `zone`. Returns null for anything else.
 */
export function parseISO(s: string | null | undefined, zone: string = browserZone()): Date | null {
  if (!s) return null
  const t = s.trim()
  if (ISO_DATE.test(t)) return startOfDay(t, zone)
  const m = ISO_DT.exec(t)
  if (!m) return null
  const w: WallTime = {
    year: Number(m[1]), month: Number(m[2]), day: Number(m[3]), hour: Number(m[4]), minute: Number(m[5]),
    second: Number(m[6] ?? 0), millisecond: m[7] ? Number(m[7].slice(0, 3).padEnd(3, '0')) : 0,
  }
  if (w.month < 1 || w.month > 12 || w.day < 1 || w.day > 31 || w.hour! > 24 || w.minute! > 59) return null
  const off = m[8]
  if (!off) return fromZoned(w, zone)
  if (off.toUpperCase() === 'Z') return new Date(wallUtc(w))
  const sign = off[0] === '-' ? -1 : 1
  const digits = off.slice(1).replace(':', '')
  const mins = Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2, 4) || 0)
  return new Date(wallUtc(w) - sign * mins * MIN)
}

// ---------- canonical occurrence ----------

/** A UTC instant as Python's isoformat() writes it: "2026-10-01T15:00:00+00:00". */
export function utcIsoformat(instant: Date): string {
  const d = instant
  return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}+00:00`
}

/**
 * The canonical occurrence string (plan §2): the UTC isoformat for a timed occurrence, the plain
 * date for an all-day one. Reminder keys and /calendar/event/:uid/:occ links use it.
 * A string start is parsed first (a zone-less one in `zone`); an all-day instant is read in `zone`.
 */
export function canonicalOccurrence(start: Date | string, allDay: boolean, zone: string = browserZone()): string {
  if (allDay) {
    if (typeof start === 'string') {
      const head = start.trim().slice(0, 10)
      if (ISO_DATE.test(head)) return head
      const compact = /^(\d{4})(\d{2})(\d{2})/.exec(start.trim())
      if (compact) return `${compact[1]}-${compact[2]}-${compact[3]}`
      throw new Error(tr('lib.dates.notADate2', { start }))
    }
    return toISODate(start, zone)
  }
  const d = typeof start === 'string' ? parseISO(start, zone) : start
  if (!d || Number.isNaN(d.getTime())) throw new Error(tr('lib.dates.notATime', { string: String(start) }))
  return utcIsoformat(d)
}

/** Read a canonical occurrence string back: a date for all-day, an instant otherwise. */
export function parseOccurrence(occ: string): { allDay: true; date: ISODate } | { allDay: false; instant: Date } | null {
  if (ISO_DATE.test(occ)) return { allDay: true, date: occ }
  const d = parseISO(occ, 'UTC')
  return d ? { allDay: false, instant: d } : null
}

// ---------- preferences ----------

const WEEKDAY_NAMES: Record<string, Weekday> = { sunday: 0, sun: 0, monday: 1, mon: 1, saturday: 6, sat: 6 }

function localeWeekStart(): Weekday {
  try {
    const loc = new Intl.Locale(appLocale()) as Intl.Locale & {
      getWeekInfo?: () => { firstDay: number }
      weekInfo?: { firstDay: number }
    }
    const first = loc.getWeekInfo?.().firstDay ?? loc.weekInfo?.firstDay
    if (first) return (first % 7) as Weekday // Intl counts 1 = Monday … 7 = Sunday
  } catch {
    /* older engines */
  }
  return 0
}

function localeHour12(): boolean {
  try {
    const hc = new Intl.DateTimeFormat(appLocale(), { hour: 'numeric' }).resolvedOptions().hourCycle
    return hc === 'h12' || hc === 'h11'
  } catch {
    return true
  }
}

/**
 * The user's date preferences from settings, with browser defaults for anything unset.
 * Accepts the setting names the settings tab may store: timezone, week_start (0-6 or a day
 * name) and time_format ('12h' | '24h' | 'auto').
 */
export function resolveDatePrefs(s?: { timezone?: string | null; week_start?: number | string | null; time_format?: string | null } | null): DatePrefs {
  const zone = isValidZone(s?.timezone) ? s!.timezone! : browserZone()
  let weekStart = localeWeekStart()
  const ws = s?.week_start
  if (typeof ws === 'number' && ws >= 0 && ws <= 6) weekStart = ws as Weekday
  else if (typeof ws === 'string' && ws.toLowerCase() in WEEKDAY_NAMES) weekStart = WEEKDAY_NAMES[ws.toLowerCase()]
  const tf = s?.time_format
  const hour12 = tf === '12h' ? true : tf === '24h' ? false : localeHour12()
  return { zone, hour12, weekStart }
}

// ---------- formatting ----------

type FmtPrefs = Pick<DatePrefs, 'zone' | 'hour12'>

function clock(hour: number, minute: number, hour12: boolean, compact: boolean): string {
  if (!ownStyle()) {
    try {
      return new Intl.DateTimeFormat(appLocale(), { hour: 'numeric', minute: '2-digit', hour12, timeZone: 'UTC' }).format(new Date(Date.UTC(2000, 0, 1, hour, minute)))
    } catch {
      /* the English form below */
    }
  }
  if (!hour12) return `${pad(hour)}:${pad(minute)}`
  const h = hour % 12 || 12
  const ap = hour < 12 ? 'AM' : 'PM'
  return compact && minute === 0 ? `${h} ${ap}` : `${h}:${pad(minute)} ${ap}`
}

/**
 * A time in the user's clock: "3:00 PM" or "15:00". `compact` drops ":00" on the hour
 * in 12-hour form ("3 PM"), as chips and grid labels show it.
 */
export function formatTime(instant: Date, prefs: FmtPrefs, opts: { compact?: boolean } = {}): string {
  const p = zoneParts(instant, prefs.zone)
  return clock(p.hour, p.minute, prefs.hour12, !!opts.compact)
}

/** An hour label for a time gutter: "9 AM" / "09:00". */
export function formatHour(hour: number, hour12: boolean): string {
  return clock(hour, 0, hour12, true)
}

export type DateStyle = 'day' | 'dayYear' | 'short' | 'shortYear' | 'long' | 'weekday' | 'weekdayLong' | 'month' | 'monthYear'

/** The same styles for another language, in its own order (Intl). */
const INTL_STYLE: Record<DateStyle, Intl.DateTimeFormatOptions> = {
  day: { weekday: 'short', day: 'numeric', month: 'short' },
  dayYear: { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' },
  short: { day: 'numeric', month: 'short' },
  shortYear: { day: 'numeric', month: 'short', year: 'numeric' },
  long: { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' },
  weekday: { weekday: 'short' },
  weekdayLong: { weekday: 'long' },
  month: { month: 'long' },
  monthYear: { month: 'long', year: 'numeric' },
}

/**
 * A date as the spec writes it: 'day' "Tue 6 Oct", 'dayYear' "Tue 6 Oct 2026", 'short' "6 Oct",
 * 'shortYear' "6 Oct 2026", 'long' "Tuesday, 6 October 2026", 'weekday' "Tue",
 * 'weekdayLong' "Tuesday", 'month' "October", 'monthYear' "October 2026".
 * An ISODate is formatted as is; an instant is read in `zone`.
 */
export function formatDate(value: Date | ISODate, style: DateStyle = 'day', zone: string = browserZone()): string {
  const d = typeof value === 'string' ? parseISODate(value) : zoneParts(value, zone)
  const wd = weekdayOf(isoDate(d.year, d.month, d.day))
  if (!ownStyle()) {
    try {
      return new Intl.DateTimeFormat(appLocale(), { ...INTL_STYLE[style], timeZone: 'UTC' }).format(new Date(Date.UTC(d.year, d.month - 1, d.day, 12)))
    } catch {
      /* the English form below */
    }
  }
  switch (style) {
    case 'day':
      return `${WEEKDAY_SHORT[wd]} ${d.day} ${MONTH_SHORT[d.month - 1]}`
    case 'dayYear':
      return `${WEEKDAY_SHORT[wd]} ${d.day} ${MONTH_SHORT[d.month - 1]} ${d.year}`
    case 'short':
      return `${d.day} ${MONTH_SHORT[d.month - 1]}`
    case 'shortYear':
      return `${d.day} ${MONTH_SHORT[d.month - 1]} ${d.year}`
    case 'long':
      return `${WEEKDAY_LONG[wd]}, ${d.day} ${MONTH_LONG[d.month - 1]} ${d.year}`
    case 'weekday':
      return WEEKDAY_SHORT[wd]
    case 'weekdayLong':
      return WEEKDAY_LONG[wd]
    case 'month':
      return MONTH_LONG[d.month - 1]
    case 'monthYear':
      return `${MONTH_LONG[d.month - 1]} ${d.year}`
  }
}

/**
 * When an event happens, in one line: "Tue 6 Oct, 3:00 – 4:00 PM", "Tue 6 Oct, 11:30 PM – Wed 7 Oct, 1:00 AM",
 * "Tue 6 Oct (all day)", "Tue 6 Oct – Thu 8 Oct". All-day `end` is exclusive, as stored.
 */
export function formatRange(start: Date | ISODate, end: Date | ISODate | null, allDay: boolean, prefs: FmtPrefs): string {
  if (allDay) {
    const s = typeof start === 'string' ? start : toISODate(start, prefs.zone)
    const e = end ? addDaysISO(typeof end === 'string' ? end : toISODate(end, prefs.zone), -1) : s
    return e > s ? `${formatDate(s, 'day')} – ${formatDate(e, 'day')}` : tr('lib.dates.allDay', { date: formatDate(s, 'day') })
  }
  const s = typeof start === 'string' ? parseISO(start, prefs.zone)! : start
  const day = formatDate(s, 'day', prefs.zone)
  if (!end) return `${day}, ${formatTime(s, prefs)}`
  const e = typeof end === 'string' ? parseISO(end, prefs.zone)! : end
  if (!sameDay(s, e, prefs.zone)) return `${day}, ${formatTime(s, prefs)} – ${formatDate(e, 'day', prefs.zone)}, ${formatTime(e, prefs)}`
  const sp = zoneParts(s, prefs.zone)
  const ep = zoneParts(e, prefs.zone)
  // "3:00 – 4:00 PM" when both halves share AM/PM in 12-hour form.
  if (prefs.hour12 && sp.hour < 12 === ep.hour < 12) {
    const left = formatTime(s, prefs).replace(/ (AM|PM)$/, '')
    return `${day}, ${left} – ${formatTime(e, prefs)}`
  }
  return `${day}, ${formatTime(s, prefs)} – ${formatTime(e, prefs)}`
}

/** The zone's short name at an instant ("CDT", "GMT+1"), for the zone chip. */
export function zoneAbbr(instant: Date, zone: string): string {
  if (!isValidZone(zone)) return zone
  try {
    const p = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'short' }).formatToParts(instant)
    return p.find((x) => x.type === 'timeZoneName')?.value ?? zone
  } catch {
    return zone
  }
}

// ---------- the account's clock outside a component ----------

let clockSetting: string | null | undefined = null

/** The account's time_format ('12h' | '24h' | 'auto'), set by the signed-in shell, so plain
 *  functions (list dates, chips, toasts) show times in the clock the user chose. */
export function setClockSetting(tf: string | null | undefined): void {
  clockSetting = tf
}

/** The clock this device shows, '12h' or '24h': the account's choice, else the locale's (Automatic).
 *  Sent as X-Mailr-Clock, so Ambrose writes times the same way (backend calendar/tz.py user_clock). */
export function deviceClock(): '12h' | '24h' {
  if (clockSetting === '12h' || clockSetting === '24h') return clockSetting
  return localeHour12() ? '12h' : '24h'
}

/** Intl options for that clock: `{ hour12 }` when the user chose one, nothing for Automatic. Spread
 *  it into every `toLocaleString` / `toLocaleTimeString` option object that shows a time. */
export function clockOpts(): { hour12?: boolean } {
  return clockSetting === '12h' ? { hour12: true } : clockSetting === '24h' ? { hour12: false } : {}
}
