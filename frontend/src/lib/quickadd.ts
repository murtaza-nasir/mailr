// The quick-add grammar. The Android twin is
// ui/calendar/QuickAddGrammar.kt; both run the vectors in spec/cases/quickadd-cases.yaml.
// No model is involved: a word is taken only when a rule below matches it exactly, and every taken word
// comes back as a token with its span so the input can highlight it and turn it back into text.

import { t as tr } from '../i18n'
import { dateTimeFormat } from './dates'

export type QuickMode = 'event' | 'task'

export type TokenKind =
  | 'date' | 'time' | 'duration' | 'span' | 'repeat' | 'start' | 'reminder'
  | 'priority' | 'tag' | 'collection' | 'location' | 'guest' | 'video'

export interface QuickToken {
  kind: TokenKind
  /** UTF-16 offsets into the input, end exclusive. */
  start: number
  end: number
  text: string
}

export interface QuickCollection { id: string; name: string }
export interface QuickPerson { name: string; email: string }

export interface QuickGuest {
  /** The words as typed ("Dana", "dana@x.example"). */
  text: string
  /** Set only for a typed email address. */
  email: string | null
  /** True only for a typed email address; a name stays a suggestion until the user confirms it. */
  confirmed: boolean
  /** People whose name (or a word of it) starts with the text, or whose email is the text. */
  matches: QuickPerson[]
}

export interface QuickReminder {
  /** Minutes before the start or due (negative = after; all-day items count from local midnight). */
  minutes: number | null
  /** Absolute local time for "remind at 9am" / "remind tom 9am". */
  at: string | null
}

export interface QuickResult {
  title: string
  tokens: QuickToken[]
  /** Event: local wall time "2026-10-02T12:30", or a date "2026-10-02" when all_day. */
  start: string | null
  /** Event: as start; an all-day end is exclusive. */
  end: string | null
  all_day: boolean
  /** Event: minutes from "for 45 min", whether or not a time was given. */
  duration: number | null
  /** Task: "2026-10-02T09:00", or "2026-10-02" when due_all_day. */
  due: string | null
  due_all_day: boolean
  /** Task: the start (defer) date. */
  start_date: string | null
  rrule: string | null
  /** Task only: 'completion' for every!, else 'due' when a repeat was given. */
  repeat_from: 'due' | 'completion' | null
  location: string | null
  guests: QuickGuest[]
  list_or_calendar: { id: string | null; name: string; create: boolean } | null
  tags: string[]
  /** iCalendar priority: 1 high, 5 medium, 9 low, 0 none. */
  priority: number
  reminders: QuickReminder[]
  video: boolean
}

export interface QuickOptions {
  mode: QuickMode
  now: Date
  /** IANA zone for "now" and every result. */
  tz: string
  lists?: QuickCollection[]
  calendars?: QuickCollection[]
  people?: QuickPerson[]
  /** Known past locations, matched after "at", "@" or "in" whatever their case. */
  places?: string[]
  /** How "12/10" reads (the date-format setting). */
  dateOrder?: 'mdy' | 'dmy'
  /** Event length when no end or duration is typed, in minutes. */
  defaultDuration?: number
  morningHour?: number
  afternoonHour?: number
  eveningHour?: number
  /** Token texts the user turned back into title text ("Keep as text"); compared case-insensitively. */
  keep?: string[]
}

// ---------------------------------------------------------------- civil calendar arithmetic

/** Days since 1970-01-01 for a proleptic Gregorian date. */
export function epochDay(y: number, m: number, d: number): number {
  const yy = y - (m <= 2 ? 1 : 0)
  const era = Math.floor(yy / 400)
  const yoe = yy - era * 400
  const doy = Math.floor((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy
  return era * 146097 + doe - 719468
}

export function civil(z: number): [number, number, number] {
  const zz = z + 719468
  const era = Math.floor(zz / 146097)
  const doe = zz - era * 146097
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365)
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100))
  const mp = Math.floor((5 * doy + 2) / 153)
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1
  const m = mp + (mp < 10 ? 3 : -9)
  return [yoe + era * 400 + (m <= 2 ? 1 : 0), m, d]
}

/** ISO day of week, 1 = Monday … 7 = Sunday. */
const dow = (z: number) => ((((z + 3) % 7) + 7) % 7) + 1

const leap = (y: number) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
const monthDays = (y: number, m: number) => [31, leap(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]

function validDate(y: number, m: number, d: number): number | null {
  if (m < 1 || m > 12 || d < 1 || d > monthDays(y, m)) return null
  return epochDay(y, m, d)
}

/** Adds months, clamping the day to the month's length (31 Jan + 1 month = 28 Feb). */
function plusMonths(z: number, n: number): number {
  const [y, m, d] = civil(z)
  const t = y * 12 + (m - 1) + n
  const ny = Math.floor(t / 12)
  const nm = t - ny * 12 + 1
  return epochDay(ny, nm, Math.min(d, monthDays(ny, nm)))
}

const pad = (n: number) => String(n).padStart(2, '0')

export function fmtDate(z: number): string {
  const [y, m, d] = civil(z)
  return `${y}-${pad(m)}-${pad(d)}`
}

/** "2026-10-02T12:30" from minutes since the epoch (wall clock). */
export function fmtDateTime(t: number): string {
  const z = Math.floor(t / 1440)
  const min = t - z * 1440
  return `${fmtDate(z)}T${pad(Math.floor(min / 60))}:${pad(min % 60)}`
}

const fmtCache = new Map<string, Intl.DateTimeFormat>()

/** The wall clock in `tz` at an instant, as minutes since the epoch (seconds dropped). */
export function wallMinutes(ms: number, tz: string): number {
  let f = fmtCache.get(tz)
  if (!f) {
    f = dateTimeFormat('en-US', {
      hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    }, tz)
    fmtCache.set(tz, f)
  }
  const p: Record<string, number> = {}
  for (const part of f.formatToParts(new Date(ms))) if (part.type !== 'literal') p[part.type] = Number(part.value)
  return epochDay(p.year, p.month, p.day) * 1440 + (p.hour % 24) * 60 + p.minute
}

/**
 * The instant for a local wall time in `tz` ("2026-10-02T12:30" or a date at midnight). A time in the
 * spring-forward gap moves forward by the gap; a repeated autumn time takes the earlier instant.
 */
export function zonedToInstant(local: string, tz: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/.exec(local)
  if (!m) throw new Error(tr('lib.quickadd.notALocalTime', { local }))
  const wall = epochDay(+m[1], +m[2], +m[3]) * 1440 + (m[4] ? +m[4] * 60 + +m[5] : 0)
  const guess = wall * 60_000
  const off1 = wallMinutes(guess, tz) * 60_000 - guess
  let r = guess - off1
  const off2 = wallMinutes(r, tz) * 60_000 - r
  if (off2 !== off1) {
    const r2 = guess - off2
    r = wallMinutes(r2, tz) * 60_000 === guess ? Math.min(r, r2) : Math.max(r, r2)
  }
  return new Date(r)
}

// ---------------------------------------------------------------- vocabulary

const WEEKDAYS: Record<string, number> = {
  monday: 1, mon: 1, tuesday: 2, tue: 2, tues: 2, wednesday: 3, wed: 3, weds: 3, thursday: 4, thu: 4, thur: 4,
  thurs: 4, friday: 5, fri: 5, saturday: 6, sat: 6, sunday: 7, sun: 7,
}
const MONTHS: Record<string, number> = {
  january: 1, jan: 1, february: 2, feb: 2, march: 3, mar: 3, april: 4, apr: 4, may: 5, june: 6, jun: 6, july: 7,
  jul: 7, august: 8, aug: 8, september: 9, sep: 9, sept: 9, october: 10, oct: 10, november: 11, nov: 11,
  december: 12, dec: 12,
}
const BYDAY = ['', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']
const NTH: Record<string, number> = { first: 1, '1st': 1, second: 2, '2nd': 2, third: 3, '3rd': 3, fourth: 4, '4th': 4, last: -1 }
const BARE_REPEAT: Record<string, Rule> = {
  daily: { freq: 'DAILY', interval: 1 }, weekly: { freq: 'WEEKLY', interval: 1 }, monthly: { freq: 'MONTHLY', interval: 1 },
  yearly: { freq: 'YEARLY', interval: 1 }, annually: { freq: 'YEARLY', interval: 1 },
  weekdays: { freq: 'WEEKLY', interval: 1, byday: [1, 2, 3, 4, 5] },
}
const LOCATION_LINKS = new Set(['of', 'the', 'de', 'la', 'le', 'du', 'del', 'da', 'des', '&'])

const UPPER = /^\p{Lu}/u
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const MERIDIEM_WORD = /^(am|pm|a\.m|p\.m)$/
const CLOCK = /^(\d{1,2})(?::(\d{2}))?(am|pm|a|p|a\.m|p\.m)?$/

type Freq = 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'YEARLY'
interface Rule { freq: Freq; interval: number; byday?: number[]; nth?: number; nthDay?: number; monthday?: number }

function rruleText(r: Rule): string {
  let s = `FREQ=${r.freq}`
  if (r.interval > 1) s += `;INTERVAL=${r.interval}`
  if (r.nth != null && r.nthDay != null) s += `;BYDAY=${r.nth}${BYDAY[r.nthDay]}`
  else if (r.byday) s += `;BYDAY=${[...r.byday].sort((a, b) => a - b).map((d) => BYDAY[d]).join(',')}`
  if (r.monthday != null) s += `;BYMONTHDAY=${r.monthday}`
  return s
}

interface Word { start: number; end: number; raw: string; core: string; lc: string; prot: boolean; used: boolean }
interface DateHit { n: number; day: number; time?: number; tonight?: boolean; positional?: boolean; weekday?: number; fillsTime?: boolean }
interface Clock { n: number; min: number; mer: boolean; h: number }
interface Part { h: number; m: number; mer: 'am' | 'pm' | null; colon: boolean; zero?: boolean }

// ---------------------------------------------------------------- the parser

export function parse(text: string, opts: QuickOptions): QuickResult {
  const mode = opts.mode
  const event = mode === 'event'
  const nowWall = wallMinutes(opts.now.getTime(), opts.tz)
  const today = Math.floor(nowWall / 1440)
  const nowMin = nowWall - today * 1440
  const dateOrder = opts.dateOrder ?? 'mdy'
  const dayparts: Record<string, number> = {
    morning: (opts.morningHour ?? 9) * 60, afternoon: (opts.afternoonHour ?? 14) * 60,
    evening: (opts.eveningHour ?? 18) * 60, night: 20 * 60,
  }
  const keep = new Set((opts.keep ?? []).map((k) => k.toLowerCase()))

  // Words, with quoted stretches protected (straight or curly quotes; an open quote runs to the end).
  const prot: Array<[number, number]> = []
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '"' || c === '“') {
      const close = text.indexOf(c === '"' ? '"' : '”', i + 1)
      const end = close < 0 ? text.length : close + 1
      prot.push([i, end])
      i = end - 1
    }
  }
  const words: Word[] = []
  for (const m of text.matchAll(/\S+/g)) {
    const raw = m[0]
    const start = m.index!
    let core = raw.replace(/[,.;:]+$/, '')
    if (!core) core = raw
    words.push({
      start, end: start + core.length, raw, core, lc: core.toLowerCase(), used: false,
      prot: prot.some(([a, b]) => start < b && start + raw.length > a),
    })
  }
  const n = words.length
  const tokens: Array<QuickToken & { keepText?: boolean }> = []

  const free = (i: number) => i >= 0 && i < n && !words[i].prot && !words[i].used
  const lc = (i: number): string | null => (free(i) ? words[i].lc : null)
  const core = (i: number): string | null => (free(i) ? words[i].core : null)
  const punct = (i: number) => words[i].raw !== words[i].core

  function take(i: number, k: number, kind: TokenKind, keepText = false): boolean {
    const start = words[i].start
    const end = words[i + k - 1].end
    const t = text.slice(start, end)
    if (keep.has(t.toLowerCase())) return false
    for (let j = i; j < i + k; j++) words[j].used = true
    tokens.push({ kind, start, end, text: t, keepText })
    return true
  }

  /** The first free position where `f` matches and the take succeeds. */
  function scan(f: (i: number) => boolean) {
    for (let i = 0; i < n; i++) if (free(i) && f(i)) return
  }

  // ---- shared sub-grammars

  function nextStrict(wd: number) {
    return today + (((wd - dow(today) + 7) % 7) || 7)
  }

  /** A month and day without a year: the next one on or after today. */
  function inferMonthDay(m: number, d: number): number | null {
    const [y] = civil(today)
    for (let k = 0; k < 8; k++) {
      const z = validDate(y + k, m, d)
      if (z != null && z >= today) return z
    }
    return null
  }

  function dayNum(s: string | null): number | null {
    const m = s ? /^(\d{1,2})(st|nd|rd|th)?$/.exec(s) : null
    if (!m) return null
    const d = +m[1]
    return d >= 1 && d <= 31 ? d : null
  }
  function ordinal(s: string | null): number | null {
    const m = s ? /^(\d{1,2})(st|nd|rd|th)$/.exec(s) : null
    if (!m) return null
    const d = +m[1]
    return d >= 1 && d <= 31 ? d : null
  }
  const yearAt = (i: number) => {
    const s = lc(i)
    return s && /^20\d\d$/.test(s) ? +s : null
  }

  /** A date expression at i, without prefix words. */
  function dateAt(i: number): DateHit | null {
    const hit = dateCore(i)
    if (!hit || hit.fillsTime) return hit
    const dp = lc(i + hit.n)
    if (dp != null && dp in dayparts && hit.time == null) return { ...hit, n: hit.n + 1, time: dayparts[dp] }
    return hit
  }

  function dateCore(i: number): DateHit | null {
    const a = lc(i)
    if (a == null) return null
    const b = lc(i + 1)
    if (a === 'today') return { n: 1, day: today }
    if (a === 'tonight') return { n: 1, day: today, time: 20 * 60, tonight: true }
    if (a === 'tomorrow' || a === 'tmrw' || a === 'tmr' || words[i].core === 'tom') return { n: 1, day: today + 1 }
    if (a === 'day' && b === 'after' && lc(i + 2) === 'tomorrow') return { n: 3, day: today + 2 }
    if (a === 'the' && b === 'day' && lc(i + 2) === 'after' && lc(i + 3) === 'tomorrow') return { n: 4, day: today + 2 }
    if (a === 'this' || a === 'next') {
      if (b != null && b in WEEKDAYS) {
        const wd = WEEKDAYS[b]
        if (a === 'this') return { n: 2, day: today + ((wd - dow(today) + 7) % 7), weekday: wd }
        const d = nextStrict(wd)
        return { n: 2, day: d < today + 7 ? d + 7 : d, weekday: wd }
      }
      if (b === 'weekend') {
        const sat = today + ((6 - dow(today) + 7) % 7)
        return { n: 2, day: a === 'this' ? sat : sat + 7 }
      }
      if (a === 'next' && b === 'week') return { n: 2, day: nextStrict(1) }
      if (a === 'next' && b === 'month') {
        const [y, m] = civil(plusMonths(today, 1))
        return { n: 2, day: epochDay(y, m, 1) }
      }
      if (a === 'this' && b != null && b in dayparts && b !== 'night') return { n: 2, day: today, time: dayparts[b] }
      return null
    }
    if (a === 'eom' || (a === 'end' && b === 'of' && (lc(i + 2) === 'month' || (lc(i + 2) === 'the' && lc(i + 3) === 'month')))) {
      const [y, m] = civil(today)
      return { n: a === 'eom' ? 1 : lc(i + 2) === 'the' ? 4 : 3, day: epochDay(y, m, monthDays(y, m)) }
    }
    if (a === 'in') {
      const r = relAt(i + 1)
      if (r) return { ...r, n: r.n + 1 }
      return null
    }
    let m = /^\+(\d{1,3})([dw])$/.exec(a)
    if (m) return { n: 1, day: today + +m[1] * (m[2] === 'w' ? 7 : 1) }
    if (a in WEEKDAYS) return { n: 1, day: nextStrict(WEEKDAYS[a]), positional: true, weekday: WEEKDAYS[a] }
    m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(a)
    if (m) {
      const z = validDate(+m[1], +m[2], +m[3])
      return z == null ? null : { n: 1, day: z }
    }
    m = /^(\d{1,2})\/(\d{1,2})(?:\/(\d{4}|\d{2}))?$/.exec(a)
    if (m) {
      const [x, y] = [+m[1], +m[2]]
      const [mo, d] = dateOrder === 'dmy' ? [y, x] : [x, y]
      if (m[3]) {
        const yr = m[3].length === 2 ? 2000 + +m[3] : +m[3]
        const z = validDate(yr, mo, d)
        return z == null ? null : { n: 1, day: z }
      }
      if (mo < 1 || mo > 12 || d < 1 || d > 31) return null
      const z = inferMonthDay(mo, d)
      return z == null ? null : { n: 1, day: z, positional: true }
    }
    // oct 12 [2027]
    if (a in MONTHS) {
      const d = dayNum(b)
      if (d == null) return null
      const yr = yearAt(i + 2)
      const z = yr != null ? validDate(yr, MONTHS[a], d) : inferMonthDay(MONTHS[a], d)
      return z == null ? null : { n: yr != null ? 3 : 2, day: z }
    }
    // 12 oct [2027], 12th of oct
    const d = dayNum(a)
    if (d != null) {
      let k = 1
      if (ordinal(a) != null && b === 'of') k = 2
      const mw = lc(i + k)
      if (mw == null || !(mw in MONTHS)) return null
      const yr = yearAt(i + k + 1)
      const z = yr != null ? validDate(yr, MONTHS[mw], d) : inferMonthDay(MONTHS[mw], d)
      return z == null ? null : { n: k + 1 + (yr != null ? 1 : 0), day: z }
    }
    // the 12th [of oct]
    if (a === 'the') {
      const od = ordinal(b)
      if (od == null) return null
      if (lc(i + 2) === 'of' && lc(i + 3) != null && lc(i + 3)! in MONTHS) {
        const z = inferMonthDay(MONTHS[lc(i + 3)!], od)
        return z == null ? null : { n: 4, day: z }
      }
      const [ty, tm] = civil(today)
      for (let k = 0; k < 12; k++) {
        const [y, mo] = civil(plusMonths(epochDay(ty, tm, 1), k))
        const z = validDate(y, mo, od)
        if (z != null && z >= today) return { n: 2, day: z }
      }
    }
    return null
  }

  /** "3 days", "2h", "an hour", "half an hour" after "in". */
  function relAt(i: number): DateHit | null {
    const d = amountAt(i, true)
    if (!d) return null
    if (d.unit === 'min') {
      const t = wallMinutes(opts.now.getTime() + d.value * 60_000, opts.tz)
      const day = Math.floor(t / 1440)
      return { n: d.n, day, time: t - day * 1440, fillsTime: true }
    }
    if (d.unit === 'month') return { n: d.n, day: plusMonths(today, d.value) }
    return { n: d.n, day: today + d.value }
  }

  /**
   * An amount of time: "45 min", "2h", "1.5 hours", "1h30", "an hour", "half an hour", "1 hour 30 min",
   * and with `days`, also "3 days", "2 weeks", "1 month". Minutes for clock units, else the count.
   */
  function amountAt(i: number, days: boolean): { n: number; value: number; unit: 'min' | 'day' | 'month' } | null {
    const a = lc(i)
    if (a == null) return null
    const clockUnit = (u: string | null): number | null => {
      if (u == null) return null
      if (/^(m|min|mins|minute|minutes)$/.test(u)) return 1
      if (/^(h|hr|hrs|hour|hours)$/.test(u)) return 60
      return null
    }
    const dayUnit = (u: string | null): [number, 'day' | 'month'] | null => {
      if (!days || u == null) return null
      if (/^(d|day|days)$/.test(u)) return [1, 'day']
      if (/^(w|wk|wks|week|weeks)$/.test(u)) return [7, 'day']
      if (/^(month|months)$/.test(u)) return [1, 'month']
      return null
    }
    if (a === 'half' && lc(i + 1) === 'an' && clockUnit(lc(i + 2)) === 60) return { n: 3, value: 30, unit: 'min' }
    if (a === 'an' || a === 'a') {
      const cu = clockUnit(lc(i + 1))
      if (cu === 60) return { n: 2, value: 60, unit: 'min' }
      const du = dayUnit(lc(i + 1))
      if (du) return { n: 2, value: du[0], unit: du[1] }
      return null
    }
    let m = /^(\d{1,2})h(\d{1,2})m?$/.exec(a)
    if (m) return { n: 1, value: +m[1] * 60 + +m[2], unit: 'min' }
    m = /^(\d+(?:\.\d+)?)([a-z]+)$/.exec(a)
    let num: number
    let k: number
    let unitWord: string | null
    if (m) {
      num = +m[1]
      unitWord = m[2]
      k = 1
    } else if (/^\d+(?:\.\d+)?$/.test(a)) {
      num = +a
      unitWord = lc(i + 1)
      k = 2
    } else return null
    const cu = clockUnit(unitWord)
    if (cu != null) {
      let value = Math.round(num * cu)
      // "1 hour 30 min"
      if (cu === 60 && Number.isInteger(num)) {
        const more = lc(i + k)
        const mm = more ? /^(\d{1,2})(m|min|mins|minutes?)?$/.exec(more) : null
        if (mm && mm[2]) {
          value += +mm[1]
          k += 1
        } else if (mm && clockUnit(lc(i + k + 1)) === 1) {
          value += +mm[1]
          k += 2
        }
      }
      return value > 0 ? { n: k, value, unit: 'min' } : null
    }
    const du = dayUnit(unitWord)
    if (du && Number.isInteger(num) && num > 0) return { n: k, value: num * du[0], unit: du[1] }
    return null
  }

  /** A clock time at i: "3pm", "3 pm", "3:30p", "15:30", "noon", "midnight", and a bare hour when `bare`. */
  function clockAt(i: number, bare: boolean): Clock | null {
    let a = lc(i)
    if (a == null) return null
    if (a === 'noon') return { n: 1, min: 720, mer: true, h: 12 }
    if (a === 'midnight') return { n: 1, min: 0, mer: true, h: 0 }
    if (!bare && /^@\d/.test(a)) a = a.slice(1)
    const m = CLOCK.exec(a)
    if (!m) return null
    const h = +m[1]
    const min = m[2] ? +m[2] : 0
    if (min > 59) return null
    let mer = m[3] ? m[3][0] : null
    let k = 1
    if (!mer) {
      const nx = lc(i + 1)
      if (nx != null && MERIDIEM_WORD.test(nx)) {
        mer = nx[0]
        k = 2
      }
    }
    if (mer) {
      if (h < 1 || h > 12) return null
      return { n: k, min: ((h % 12) + (mer === 'p' ? 12 : 0)) * 60 + min, mer: true, h }
    }
    if (!m[2] && !bare) return null
    if (h > 23) return null
    // A bare 1 to 6 is an afternoon hour ("at 3" = 15:00); "03:00" and 7 to 23 stay as written.
    const pm = h >= 1 && h <= 6 && !m[1].startsWith('0') ? 720 : 0
    return { n: 1, min: (h * 60 + min) + pm, mer: false, h }
  }

  /** A time with its optional prefix: "at 9", "@ 9:30", "by 5pm", "in the morning". */
  function timeAt(i: number): Clock | null {
    const a = lc(i)
    if (a === 'in' && lc(i + 1) === 'the' && lc(i + 2) != null && lc(i + 2)! in dayparts && lc(i + 2) !== 'night') {
      return { n: 3, min: dayparts[lc(i + 2)!], mer: true, h: 0 }
    }
    if (a === 'at' || a === '@' || a === 'by') {
      const c = clockAt(i + 1, true)
      return c ? { ...c, n: c.n + 1 } : null
    }
    return clockAt(i, false)
  }

  function partOf(s: string | null): Part | null {
    if (s == null) return null
    if (s === 'noon') return { h: 12, m: 0, mer: 'pm', colon: false }
    const m = CLOCK.exec(s)
    if (!m) return null
    const p: Part = { h: +m[1], m: m[2] ? +m[2] : 0, mer: m[3] ? (m[3][0] === 'p' ? 'pm' : 'am') : null, colon: !!m[2],
      zero: m[1].startsWith('0') }
    if (p.m > 59) return null
    if (p.mer ? p.h < 1 || p.h > 12 : p.h > 23) return null
    return p
  }

  /** "3-4pm", "9:30–11", "2 to 4pm", "from 2 to 4", "3pm - 4:30pm". Start and end in minutes (end may pass 24:00). */
  function rangeAt(i: number): { n: number; s: number; e: number } | null {
    let j = i
    let pre = false
    if (lc(j) === 'from' || lc(j) === 'at') {
      j++
      pre = true
    }
    let a: Part | null
    let b: Part | null
    const w = lc(j)
    if (w == null) return null
    const one = /^([^-–—]+)[-–—]([^-–—]+)$/.exec(w)
    let k: number
    if (one) {
      a = partOf(one[1])
      b = partOf(one[2])
      k = j + 1
    } else {
      a = partOf(w)
      k = j + 1
      if (a && !a.mer && lc(k) != null && MERIDIEM_WORD.test(lc(k)!)) {
        a.mer = lc(k)![0] === 'p' ? 'pm' : 'am'
        if (a.h < 1 || a.h > 12) return null
        k++
      }
      const sep = lc(k)
      if (!sep || !['to', 'till', 'until', '-', '–', '—'].includes(sep)) return null
      b = partOf(lc(k + 1))
      k += 2
    }
    if (!a || !b) return null
    if (!b.mer && lc(k) != null && MERIDIEM_WORD.test(lc(k)!)) {
      if (b.h < 1 || b.h > 12) return null
      b.mer = lc(k)![0] === 'p' ? 'pm' : 'am'
      k++
    }
    if (!(pre || a.mer || b.mer || a.colon || b.colon)) return null
    const at = (p: Part, mer: 'am' | 'pm' | null) => (mer ? (p.h % 12) + (mer === 'pm' ? 12 : 0) : p.h) * 60 + p.m
    const len = (s: number, e: number) => ((e - s + 1440) % 1440) || 1440
    let s: number
    let e: number
    if (!!a.mer === !!b.mer) {
      s = at(a, a.mer)
      e = at(b, b.mer)
      if (!a.mer) {
        // No meridiem anywhere: a bare 1 to 6 starts in the afternoon ("from 2 to 4"), and an end
        // hour that would fall before the start is read as pm ("9-5" = 9:00-17:00).
        if (a.h >= 1 && a.h <= 6 && !a.zero) s += 720
        if (e <= s && b.h >= 1 && b.h <= 12 && !b.zero && e + 720 > s) e += 720
      }
    } else if (!a.mer) {
      e = at(b, b.mer)
      if (a.h >= 1 && a.h <= 12) {
        const am = at(a, 'am')
        const pm = at(a, 'pm')
        s = len(am, e) <= len(pm, e) ? am : pm
      } else s = at(a, null)
    } else {
      s = at(a, a.mer)
      if (b.h >= 1 && b.h <= 12) {
        const am = at(b, 'am')
        const pm = at(b, 'pm')
        e = len(s, am) <= len(s, pm) ? am : pm
      } else e = at(b, null)
    }
    if (e <= s) e += 1440
    return { n: k - i, s, e }
  }

  /** A weekday list: "mon", "mon and thu", "mon, wed and fri", "mon wed fri". */
  function weekdayList(i: number): { n: number; days: number[] } | null {
    const days: number[] = []
    let k = i
    for (;;) {
      const s = lc(k)
      if (s == null || !(s in WEEKDAYS)) break
      days.push(WEEKDAYS[s])
      k++
      const c = lc(k)
      if ((c === 'and' || c === '&') && lc(k + 1) != null && lc(k + 1)! in WEEKDAYS) k++
    }
    return days.length ? { n: k - i, days: [...new Set(days)] } : null
  }

  function repeatAt(i: number): { n: number; rule: Rule; bang: boolean } | null {
    const a = lc(i)
    if (a !== 'every' && a !== 'every!') return null
    const bang = a === 'every!'
    let k = i + 1
    const s = lc(k)
    if (s == null) return null
    let rule: Rule | null
    const unitFreq = (u: string | null): Freq | null =>
      u == null ? null : /^days?$/.test(u) ? 'DAILY' : /^weeks?$/.test(u) ? 'WEEKLY' : /^months?$/.test(u) ? 'MONTHLY' : /^years?$/.test(u) ? 'YEARLY' : null
    if (unitFreq(s) && !/s$/.test(s)) {
      rule = { freq: unitFreq(s)!, interval: 1 }
      k++
    } else if (s === 'other' && unitFreq(lc(k + 1)) && !/s$/.test(lc(k + 1)!)) {
      rule = { freq: unitFreq(lc(k + 1))!, interval: 2 }
      k += 2
    } else if (/^\d{1,3}$/.test(s) && unitFreq(lc(k + 1))) {
      const iv = +s
      if (iv < 1) return null
      rule = { freq: unitFreq(lc(k + 1))!, interval: iv }
      k += 2
    } else if (s === 'weekday' || s === 'weekdays') {
      rule = { freq: 'WEEKLY', interval: 1, byday: [1, 2, 3, 4, 5] }
      k++
    } else if (s === 'weekend' || s === 'weekends') {
      rule = { freq: 'WEEKLY', interval: 1, byday: [6, 7] }
      k++
    } else if (s in NTH && lc(k + 1) != null && lc(k + 1)! in WEEKDAYS) {
      rule = { freq: 'MONTHLY', interval: 1, nth: NTH[s], nthDay: WEEKDAYS[lc(k + 1)!] }
      k += 2
    } else if (ordinal(s) != null) {
      rule = { freq: 'MONTHLY', interval: 1, monthday: ordinal(s)! }
      k++
    } else {
      const wl = weekdayList(k)
      if (!wl) return null
      rule = { freq: 'WEEKLY', interval: 1, byday: wl.days }
      k += wl.n
    }
    // "every 2 weeks on mon and thu", "every month on the 1st", "every month on the last fri"
    if (lc(k) === 'on') {
      if (rule.freq === 'WEEKLY' && !rule.byday) {
        const wl = weekdayList(k + 1)
        if (wl) {
          rule.byday = wl.days
          k += 1 + wl.n
        }
      } else if (rule.freq === 'MONTHLY' && rule.monthday == null && rule.nth == null) {
        let j = k + 1
        if (lc(j) === 'the') j++
        const od = ordinal(lc(j))
        const nth = lc(j) != null ? NTH[lc(j)!] : undefined
        if (od != null) {
          rule.monthday = od
          k = j + 1
        } else if (lc(j) === 'day' && dayNum(lc(j + 1)) != null) {
          rule.monthday = dayNum(lc(j + 1))!
          k = j + 2
        } else if (nth != null && lc(j + 1) != null && lc(j + 1)! in WEEKDAYS) {
          rule.nth = nth
          rule.nthDay = WEEKDAYS[lc(j + 1)!]
          k = j + 2
        }
      }
    }
    return { n: k - i, rule, bang }
  }

  /** The first day of a series: the first matching day `ok` accepts, counting from today. */
  function firstDay(r: Rule, ok: (z: number) => boolean): number {
    const matches = (z: number): boolean => {
      const [, , d] = civil(z)
      if (r.byday) return r.byday.includes(dow(z))
      if (r.monthday != null) return d === r.monthday
      if (r.nth != null && r.nthDay != null) {
        if (dow(z) !== r.nthDay) return false
        if (r.nth > 0) return Math.floor((d - 1) / 7) + 1 === r.nth
        const [y, m] = civil(z)
        return d + 7 > monthDays(y, m)
      }
      return true
    }
    if (r.byday || r.monthday != null || r.nth != null) {
      for (let z = today; z < today + 3000; z++) if (matches(z) && ok(z)) return z
      return today
    }
    for (let k = 0; k < 1000; k++) {
      const step = k * r.interval
      const z = r.freq === 'DAILY' ? today + step : r.freq === 'WEEKLY' ? today + 7 * step
        : r.freq === 'MONTHLY' ? plusMonths(today, step) : plusMonths(today, 12 * step)
      if (ok(z)) return z
    }
    return today
  }

  // ---- slots
  let priority = 0
  const tags: string[] = []
  let collection: QuickResult['list_or_calendar'] = null
  const reminders: Array<{ before?: number; day?: number; time?: number }> = []
  let repeat: { rule: Rule; bang: boolean } | null = null
  let startDate: number | null = null
  let span: [number, number] | null = null
  let date: DateHit | null = null
  let range: { s: number; e: number } | null = null
  let time: Clock | null = null
  let duration: number | null = null
  let allDay = false
  let location: string | null = null
  const guests: QuickGuest[] = []
  let video = false

  // 1. Priority (tasks): !!!, !!, ! and p1, p2, p3.
  if (!event) scan((i) => {
    const s = words[i].lc
    const p = s === '!!!' || s === 'p1' ? 1 : s === '!!' || s === 'p2' ? 5 : s === '!' || s === 'p3' ? 9 : 0
    if (p && take(i, 1, 'priority')) {
      priority = p
      return true
    }
    return false
  })

  // 2. Tags (tasks): @phone.
  if (!event) for (let i = 0; i < n; i++) {
    const c = core(i)
    if (c && /^@\p{L}[\p{L}\p{N}_-]*$/u.test(c) && take(i, 1, 'tag')) {
      const t = c.slice(1)
      if (!tags.some((x) => x.toLowerCase() === t.toLowerCase())) tags.push(t)
    }
  }

  // 3. Calendar or list: #Work.
  scan((i) => {
    const c = words[i].core
    if (!/^#\p{L}[\p{L}\p{N}_\-/]*$/u.test(c)) return false
    const q = c.slice(1).toLowerCase()
    const pool = (event ? opts.calendars : opts.lists) ?? []
    const squash = (s: string) => s.toLowerCase().replace(/[\s_-]+/g, '')
    const found = pool.filter((x) => x.name.toLowerCase() === q)
    const pick = found.length ? found : pool
      .filter((x) => x.name.toLowerCase().startsWith(q) || squash(x.name).startsWith(q))
      .sort((x, y) => x.name.length - y.name.length || (x.name < y.name ? -1 : x.name > y.name ? 1 : 0))
    if (pick.length) {
      if (!take(i, 1, 'collection')) return false
      collection = { id: pick[0].id, name: pick[0].name, create: false }
      return true
    }
    if (event || !take(i, 1, 'collection')) return false
    collection = { id: null, name: c.slice(1), create: true }
    return true
  })

  // 4. Reminders: "remind 10m before", "remind me at 9am", "remind tom 9am".
  for (let i = 0; i < n; i++) {
    const a = lc(i)
    if (a !== 'remind' && a !== 'reminder') continue
    let j = i + 1
    if (lc(j) === 'me') j++
    const amt = amountAt(j, true)
    if (amt && lc(j + amt.n) === 'before') {
      const before = amt.unit === 'min' ? amt.value : amt.unit === 'day' ? amt.value * 1440 : null
      if (before != null && take(i, j + amt.n + 1 - i, 'reminder')) reminders.push({ before })
      continue
    }
    const d = dateAt(j)
    if (d) {
      let k = j + d.n
      const c = d.fillsTime ? null : lc(k) === 'at' ? clockAt(k + 1, true) : clockAt(k, false)
      if (c) k += c.n + (lc(j + d.n) === 'at' ? 1 : 0)
      const t = c ? c.min : d.time
      if (t != null && take(i, k - i, 'reminder')) reminders.push({ day: d.day, time: t })
      continue
    }
    const c = timeAt(j)
    if (c && take(i, j + c.n - i, 'reminder')) reminders.push({ time: c.min })
  }

  // 5. Repeat: "every …" anywhere (the bare words wait for the positional pass).
  scan((i) => {
    const r = repeatAt(i)
    if (!r || !take(i, r.n, 'repeat')) return false
    repeat = r
    return true
  })

  // 6. Start (tasks): "start mon", "starts 12 oct", "from mon".
  if (!event) scan((i) => {
    const a = words[i].lc
    if (!['start', 'starts', 'starting', 'from'].includes(a)) return false
    let j = i + 1
    if (lc(j) === 'on') j++
    const d = dateAt(j)
    if (!d || d.fillsTime || !take(i, j + d.n - i, 'start')) return false
    startDate = d.day
    return true
  })

  // 7. Multi-day span (events): "12–14 oct", "oct 12-14", "fri to sun", "30 oct - 2 nov".
  if (event) scan((i) => {
    let j = i
    if (lc(j) === 'from') j++
    const a = lc(j)
    if (a == null) return false
    let hit: { k: number; s: number; e: number } | null = null
    const dash = /^(\d{1,2})(?:st|nd|rd|th)?[-–—](\d{1,2})(?:st|nd|rd|th)?$/
    let m = dash.exec(a)
    if (m && lc(j + 1) != null && lc(j + 1)! in MONTHS) {
      const mo = MONTHS[lc(j + 1)!]
      const yr = yearAt(j + 2)
      const s = yr != null ? validDate(yr, mo, +m[1]) : inferMonthDay(mo, +m[1])
      const e = s == null ? null : validDate(civil(s)[0], mo, +m[2])
      if (s != null && e != null && e > s) hit = { k: j + 2 + (yr != null ? 1 : 0), s, e }
    } else if (a in MONTHS && lc(j + 1) != null && (m = dash.exec(lc(j + 1)!))) {
      const mo = MONTHS[a]
      const yr = yearAt(j + 2)
      const s = yr != null ? validDate(yr, mo, +m[1]) : inferMonthDay(mo, +m[1])
      const e = s == null ? null : validDate(civil(s)[0], mo, +m[2])
      if (s != null && e != null && e > s) hit = { k: j + 2 + (yr != null ? 1 : 0), s, e }
    } else {
      const da = dateAt(j)
      const sep = da ? lc(j + da.n) : null
      if (da && da.time == null && sep && ['to', 'through', 'thru', 'until', 'till', '-', '–', '—'].includes(sep)) {
        const b0 = j + da.n + 1
        const db = dateAt(b0)
        if (db && db.time == null) {
          let e = db.day
          if (db.weekday != null && lc(b0) != null && lc(b0)! in WEEKDAYS) {
            e = da.day + (((db.weekday - dow(da.day) + 7) % 7) || 7)
          } else {
            while (e < da.day) e = plusMonths(e, 12)
          }
          if (e > da.day) hit = { k: b0 + db.n, s: da.day, e }
        }
      }
    }
    if (!hit || !take(i, hit.k - i, 'span')) return false
    span = [hit.s, hit.e]
    return true
  })

  // 8. Date (explicit forms anywhere; "on", "by", "due" may lead).
  if (!span) scan((i) => {
    let j = i
    const a = words[i].lc
    if (a === 'due') {
      j++
      if (lc(j) === 'on' || lc(j) === 'by') j++
    } else if (a === 'on' || a === 'by') j++
    const d = dateAt(j)
    if (!d || (d.positional && j === i)) return false
    if (!take(i, j + d.n - i, 'date')) return false
    date = d
    return true
  })

  // 9. Range (events), then a single time.
  if (event && !(date as DateHit | null)?.fillsTime) scan((i) => {
    const r = rangeAt(i)
    if (!r || !take(i, r.n, 'time')) return false
    range = r
    return true
  })
  if (!range && !(date as DateHit | null)?.fillsTime) scan((i) => {
    const c = timeAt(i)
    if (!c || !take(i, c.n, 'time')) return false
    time = c
    return true
  })

  // 10. Duration and all day (events).
  if (event) scan((i) => {
    const a = words[i].lc
    if (a === 'all-day' || a === 'allday' || (a === 'all' && lc(i + 1) === 'day')) {
      if (!take(i, a === 'all' ? 2 : 1, 'duration')) return false
      allDay = true
      return true
    }
    if (a !== 'for') {
      // A bare amount right after a time or a date: "thu 3pm 45m", "3pm 45 min", "9am 1h30". Taking it
      // lets a bare weekday before it stay a date.
      const prev = i > 0 ? tokens.find((t) => t.end === words[i - 1].end) : undefined
      if (!prev || (prev.kind !== 'time' && prev.kind !== 'date')) return false
      const bare = amountAt(i, false)
      if (!bare || bare.unit !== 'min' || !take(i, bare.n, 'duration')) return false
      duration = bare.value
      return true
    }
    const amt = amountAt(i + 1, false)
    if (!amt || !take(i, amt.n + 1, 'duration')) return false
    duration = amt.value
    return true
  })

  // 11. Location (events): "at Café Luna", "@ Café Luna", "@Home", "in Room 204", or a known place.
  if (event) scan((i) => {
    const a = words[i].lc
    let j: number
    let atWord = false
    if (a === 'at' || a === '@' || a === 'in') j = i + 1
    else if (/^@\p{Lu}/u.test(words[i].core)) {
      j = i
      atWord = true
    } else return false
    // A known place, longest first.
    const places = [...(opts.places ?? [])].sort((x, y) => y.length - x.length)
    for (const p of places) {
      const pw = p.toLowerCase().split(/\s+/)
      let ok = true
      for (let k = 0; k < pw.length && ok; k++) {
        const w = lc(j + k)
        ok = w != null && (atWord && k === 0 ? w.slice(1) : w) === pw[k]
      }
      if (ok && take(i, j + pw.length - i, 'location')) {
        location = text.slice(words[j].start + (atWord ? 1 : 0), words[j + pw.length - 1].end)
        return true
      }
    }
    const first = core(j)
    if (first == null) return false
    const firstText = atWord ? first.slice(1) : first
    const fl = firstText.toLowerCase()
    if (!UPPER.test(firstText) || fl in MONTHS || fl in WEEKDAYS) return false
    // After "at" or "@" the place runs to the end of the line ("at Back office"), up to a comma or
    // the next token: a weekday or other date, a repeat word, "with", "video", or a word that starts
    // with # ! @ +. After "in" it takes capitalised words only ("in Room 204"), as "in" is common in
    // plain titles.
    const rest = a !== 'in'
    const placeWord = (x: number, c: string): boolean => {
      const l = c.toLowerCase()
      if (l === 'with' || l in BARE_REPEAT || (event && l === 'video') || /^[#!@+]/.test(c)) return false
      return dateAt(x) == null
    }
    let k = j + 1
    if (!punct(j)) {
      while (rest || k - j < 6) {
        const c = core(k)
        if (c == null) break
        if (UPPER.test(c) || /^\d/.test(c)) {
          k++
          if (punct(k - 1)) break
        } else if (LOCATION_LINKS.has(c.toLowerCase()) && core(k + 1) != null && UPPER.test(core(k + 1)!)) {
          k += 2
          if (punct(k - 1)) break
        } else if (rest && placeWord(k, c)) {
          k++
          if (punct(k - 1)) break
        } else break
      }
    }
    if (!take(i, k - i, 'location')) return false
    location = text.slice(words[j].start + (atWord ? 1 : 0), words[k - 1].end)
    return true
  })

  // 12. Guests (events): "with Dana", "with Dana and Lee", "with dana@x.example". Suggestions only; the
  // words stay in the title.
  if (event) for (let i = 0; i < n; i++) {
    if (lc(i) !== 'with') continue
    let k = i + 1
    const found: Array<[number, number]> = []
    for (;;) {
      const c = core(k)
      if (c == null) break
      if (EMAIL.test(c)) {
        found.push([k, 1])
        k++
      } else if (UPPER.test(c) && !(c.toLowerCase() in MONTHS) && !(c.toLowerCase() in WEEKDAYS)) {
        let e = k + 1
        if (!punct(k)) while (e - k < 3 && core(e) != null && UPPER.test(core(e)!)) {
          e++
          if (punct(e - 1)) break
        }
        found.push([k, e - k])
        k = e
      } else break
      const sepComma = punct(k - 1) && words[k - 1].raw.endsWith(',')
      if ((lc(k) === 'and' || lc(k) === '&') && core(k + 1) != null && (EMAIL.test(core(k + 1)!) || UPPER.test(core(k + 1)!))) k++
      else if (!sepComma) break
    }
    if (!found.length) continue
    words[i].used = true
    for (const [g, len] of found) {
      if (!take(g, len, 'guest', true)) continue
      const t = text.slice(words[g].start, words[g + len - 1].end)
      const isEmail = EMAIL.test(t)
      const tl = t.toLowerCase()
      const matches = (opts.people ?? []).filter((p) => isEmail
        ? p.email.toLowerCase() === tl
        : p.name.toLowerCase().startsWith(tl) || p.name.toLowerCase().split(/\s+/).some((w) => w.startsWith(tl)))
      guests.push({ text: t, email: isEmail ? t : null, confirmed: isEmail, matches })
    }
    // The joining words count as taken for the positional rules; they stay in the title.
    for (let j = i + 1; j < k; j++) if (!words[j].prot) words[j].used = true
  }

  // 13. Video (events): +meet / +video anywhere.
  if (event) for (let i = 0; i < n; i++) {
    const a = lc(i)
    if ((a === '+meet' || a === '+video') && take(i, 1, 'video')) video = true
  }

  // 14. Positional words, right to left until nothing changes: a bare weekday or 12/10, and the bare
  // repeat words, only at the end or before other tokens (a weekday also before "at"); bare "video"
  // only after a token and at the end.
  const tailOk = (j: number, isDate: boolean): boolean => {
    for (let k = j; k < n; k++) {
      if (words[k].used) continue
      if (isDate && event && words[k].lc === 'video' && !words[k].prot) continue
      if (isDate && !words[k].prot && (words[k].lc === 'at' || words[k].lc === '@')) {
        for (let x = j; x < k; x++) if (!words[x].used && words[x].lc !== 'video') return false
        return true
      }
      return false
    }
    return true
  }
  for (let changed = true; changed;) {
    changed = false
    for (let i = n - 1; i >= 0; i--) {
      if (!free(i)) continue
      const a = words[i].lc
      if (!repeat && a in BARE_REPEAT && tailOk(i + 1, false) && take(i, 1, 'repeat')) {
        repeat = { rule: { ...BARE_REPEAT[a] }, bang: false }
        changed = true
        continue
      }
      if (!date && !span) {
        const d = dateAt(i)
        if (d && d.positional && tailOk(i + d.n, true) && take(i, d.n, 'date')) {
          date = d
          changed = true
          continue
        }
      }
      if (event && !video && a === 'video' && i > 0 && words[i - 1].used && tailOk(i + 1, false) && take(i, 1, 'video')) {
        video = true
        changed = true
      }
    }
  }

  // ---- resolution
  const out: QuickResult = {
    title: '', tokens: [], start: null, end: null, all_day: false, duration: null, due: null, due_all_day: false,
    start_date: null, rrule: null, repeat_from: null, location: null, guests: [], list_or_calendar: null, tags,
    priority, reminders: [], video: false,
  }
  const d0 = date as DateHit | null
  const r0 = range as { s: number; e: number } | null
  const c0 = time as Clock | null
  const rep = repeat as { rule: Rule; bang: boolean } | null
  let clock: number | null = r0 ? r0.s : c0 ? c0.min : d0?.time ?? null
  if (c0 && !c0.mer && d0?.tonight && c0.h >= 1 && c0.h <= 11 && c0.min < 720) clock = c0.min + 720
  if (r0 && d0?.tonight && r0.s < 720) clock = r0.s + 720
  let day: number | null = d0 ? d0.day : null
  let anchor: { day: number; min: number } | null = null

  if (event) {
    const sp = span as [number, number] | null
    if (sp) {
      out.all_day = true
      out.start = fmtDate(sp[0])
      out.end = fmtDate(sp[1] + 1)
      anchor = { day: sp[0], min: 0 }
    } else if (clock != null && !allDay) {
      if (day == null) day = rep ? firstDay(rep.rule, () => true) : clock > nowMin ? today : today + 1
      const s = day * 1440 + clock
      const e = r0 ? day * 1440 + r0.e + (clock - r0.s) : s + (duration ?? opts.defaultDuration ?? 60)
      out.start = fmtDateTime(s)
      out.end = fmtDateTime(e)
      anchor = { day, min: clock }
    } else if (day != null || rep || allDay) {
      if (day == null) day = rep ? firstDay(rep.rule, () => true) : today
      out.all_day = true
      out.start = fmtDate(day)
      out.end = fmtDate(day + 1)
      anchor = { day, min: 0 }
    }
    out.duration = duration
    out.location = location
    out.guests = guests
    out.video = video
  } else {
    if (clock != null) {
      const t = clock
      if (day == null) day = rep ? firstDay(rep.rule, (z) => z > today || t > nowMin) : t > nowMin ? today : today + 1
      out.due = fmtDateTime(day * 1440 + t)
      anchor = { day, min: t }
    } else if (day != null || rep) {
      if (day == null) day = firstDay(rep!.rule, (z) => z > today)
      out.due = fmtDate(day)
      out.due_all_day = true
      anchor = { day, min: 0 }
    }
    out.start_date = startDate == null ? null : fmtDate(startDate)
    out.repeat_from = rep ? (rep.bang ? 'completion' : 'due') : null
  }
  out.rrule = rep ? rruleText(rep.rule) : null
  out.list_or_calendar = collection
  out.reminders = reminders.map((r) => {
    if (r.before != null) return { minutes: r.before, at: null }
    let rd = r.day
    if (rd == null) rd = anchor ? anchor.day : r.time! > nowMin ? today : today + 1
    const at = rd * 1440 + r.time!
    return { minutes: anchor ? anchor.day * 1440 + anchor.min - at : null, at: fmtDateTime(at) }
  })

  tokens.sort((x, y) => x.start - y.start)
  out.tokens = tokens.map(({ kind, start, end, text: t }) => ({ kind, start, end, text: t }))
  out.title = buildTitle(text, tokens.filter((t) => !t.keepText))
  return out
}

/**
 * The input without the given token spans, tidied: one space between words, no space left before
 * punctuation that followed a removed span, no doubled comma, no stray separators at the ends.
 */
export function buildTitle(text: string, remove: Array<{ start: number; end: number }>): string {
  const cut = '\u0000'
  let s = ''
  let at = 0
  for (const t of [...remove].sort((a, b) => a.start - b.start)) {
    s += text.slice(at, t.start) + cut
    at = t.end
  }
  s += text.slice(at)
  // The NUL character marks where a token was cut out (it never occurs in typed text).
  /* eslint-disable no-control-regex */
  return s
    .replace(/\s*\u0000[\s\u0000]*/g, cut)
    .replace(/,\u0000,/g, ',')
    .replace(/\u0000(?=[,.;:!?])/g, '')
    .replace(/\u0000/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^[\s,;:\-–—]+/, '')
    .replace(/[\s,;:\-–—.]+$/, '')
  /* eslint-enable no-control-regex */
}
