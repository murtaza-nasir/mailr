// Recurrence rules for the repeat picker (product spec §6.3): parse and write the
// RRULE subset the custom editor can edit, a human summary, the smart options for a start
// date, and the next few dates of a simple rule. Dates here are plain ISODates: recurrence
// expands in the event's own zone, so 9:00 stays 9:00 and only the date matters.
// Rules outside the subset (BYHOUR, BYSETPOS combinations, …) are kept as they are and shown
// as "Custom rule from another app".

import {
  addDaysISO, daysInMonth, formatDate, isISODate, isoDate, MONTH_LONG, parseISODate, startOfWeekISO, WEEKDAY_LONG, WEEKDAY_SHORT,
  weekdayOf, type ISODate, type Weekday,
} from './dates'
import { t } from '../i18n'

export type Freq = 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'YEARLY'
export type DayCode = 'SU' | 'MO' | 'TU' | 'WE' | 'TH' | 'FR' | 'SA'

export const DAY_CODES: DayCode[] = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA']
const WEEKDAYS: DayCode[] = ['MO', 'TU', 'WE', 'TH', 'FR']

export interface ByDay {
  day: DayCode
  /** 1…5 = first…fifth, -1 = last (monthly/yearly). Absent: every such day. */
  nth?: number
}

export interface RRule {
  freq: Freq
  interval: number
  byDay?: ByDay[]
  byMonthDay?: number
  byMonth?: number
  count?: number
  /** The UNTIL value as written: an ISODate for date rules, else the UTC "YYYYMMDDTHHMMSSZ" form. */
  until?: string
  /** Rule parts this editor does not understand. Present: the rule is read-only. */
  unsupported?: string[]
  /** The text it was parsed from (without the "RRULE:" prefix). */
  raw: string
}

const ORDINALS: Record<string, string> = { '1': 'first', '2': 'second', '3': 'third', '4': 'fourth', '5': 'fifth', '-1': 'last' }

const dayIndex = (d: DayCode) => DAY_CODES.indexOf(d) as Weekday
const dayCodeOf = (date: ISODate) => DAY_CODES[weekdayOf(date)]

/** Parse an RRULE value ("FREQ=WEEKLY;BYDAY=MO,TH" or "RRULE:…"). Null when it has no usable FREQ. */
export function parseRRule(text: string | null | undefined): RRule | null {
  if (!text) return null
  const raw = text.trim().replace(/^RRULE:/i, '')
  const parts = new Map<string, string>()
  for (const p of raw.split(';')) {
    const [k, v] = p.split('=')
    if (k && v !== undefined) parts.set(k.trim().toUpperCase(), v.trim().toUpperCase())
  }
  const freq = parts.get('FREQ')
  if (!freq) return null
  const unsupported: string[] = []
  const rule: RRule = { freq: freq as Freq, interval: 1, raw }
  if (!['DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'].includes(freq)) unsupported.push('FREQ')
  for (const [k, v] of parts) {
    switch (k) {
      case 'FREQ':
      case 'WKST':
        break
      case 'INTERVAL':
        rule.interval = Math.max(1, Number(v) || 1)
        break
      case 'COUNT':
        rule.count = Number(v) || undefined
        break
      case 'UNTIL':
        rule.until = /^\d{8}$/.test(v) ? `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6, 8)}` : v
        break
      case 'BYDAY': {
        const days: ByDay[] = []
        for (const tok of v.split(',')) {
          const m = /^([+-]?\d)?(SU|MO|TU|WE|TH|FR|SA)$/.exec(tok)
          if (!m) {
            unsupported.push('BYDAY')
            break
          }
          days.push(m[1] ? { day: m[2] as DayCode, nth: Number(m[1]) } : { day: m[2] as DayCode })
        }
        rule.byDay = days
        break
      }
      case 'BYMONTHDAY':
        if (/^-?\d{1,2}$/.test(v)) rule.byMonthDay = Number(v)
        else unsupported.push(k)
        break
      case 'BYMONTH':
        if (/^\d{1,2}$/.test(v)) rule.byMonth = Number(v)
        else unsupported.push(k)
        break
      case 'BYSETPOS':
        break
      default:
        unsupported.push(k)
    }
  }
  // Outlook writes "the first Tuesday" as BYDAY=TU;BYSETPOS=1. One day and one position is
  // the same rule as BYDAY=1TU; anything more is a combination this editor leaves alone.
  const setpos = parts.get('BYSETPOS')
  if (setpos !== undefined) {
    const one = rule.byDay?.length === 1 && rule.byDay[0].nth === undefined
    if (/^-?\d$/.test(setpos) && one && (rule.freq === 'MONTHLY' || rule.freq === 'YEARLY')) rule.byDay = [{ day: rule.byDay![0].day, nth: Number(setpos) }]
    else unsupported.push('BYSETPOS')
  }
  // Shapes the editor cannot show: positions on a daily or weekly rule, several positions in a month.
  const nths = (rule.byDay ?? []).filter((d) => d.nth !== undefined)
  if ((rule.freq === 'DAILY' || rule.freq === 'WEEKLY') && nths.length) unsupported.push('BYDAY')
  if ((rule.freq === 'MONTHLY' || rule.freq === 'YEARLY') && rule.byDay && (rule.byDay.length !== 1 || nths.length !== 1)) unsupported.push('BYDAY')
  if (rule.byDay && rule.byMonthDay !== undefined) unsupported.push('BYMONTHDAY')
  if (rule.byMonth !== undefined && rule.freq !== 'YEARLY') unsupported.push('BYMONTH')
  if (rule.byMonthDay !== undefined && (rule.freq === 'DAILY' || rule.freq === 'WEEKLY')) unsupported.push('BYMONTHDAY')
  if (unsupported.length) rule.unsupported = [...new Set(unsupported)]
  return rule
}

export function isEditable(rule: RRule | null): boolean {
  return !!rule && !rule.unsupported
}

/** Write a rule back. A read-only rule is returned exactly as it was parsed. */
export function formatRRule(rule: Omit<RRule, 'raw'> & { raw?: string }): string {
  if (rule.unsupported && rule.raw) return rule.raw
  const out = [`FREQ=${rule.freq}`]
  if (rule.interval > 1) out.push(`INTERVAL=${rule.interval}`)
  if (rule.byMonth) out.push(`BYMONTH=${rule.byMonth}`)
  if (rule.byMonthDay !== undefined) out.push(`BYMONTHDAY=${rule.byMonthDay}`)
  if (rule.byDay?.length) {
    const order = (d: ByDay) => (dayIndex(d.day) + 6) % 7 // Monday first, as clients write it
    out.push(`BYDAY=${[...rule.byDay].sort((a, b) => order(a) - order(b)).map((d) => `${d.nth ?? ''}${d.day}`).join(',')}`)
  }
  if (rule.count) out.push(`COUNT=${rule.count}`)
  else if (rule.until) out.push(`UNTIL=${isISODate(rule.until) ? rule.until.replaceAll('-', '') : rule.until}`)
  return out.join(';')
}

// ---------- summary ----------

function joinWords(words: string[]): string {
  if (words.length <= 1) return words.join('')
  return t('lib.rrule.and', { words: words.slice(0, -1).join(', '), words2: words[words.length - 1] })
}

function untilDate(until: string): ISODate {
  return isISODate(until) ? until : `${until.slice(0, 4)}-${until.slice(4, 6)}-${until.slice(6, 8)}`
}

function nthDay(b: ByDay): string {
  return t('lib.rrule.the', { ordinals: ORDINALS[String(b.nth)] ?? `${b.nth}th`, weekdayLong: WEEKDAY_LONG[dayIndex(b.day)] })
}

function every(n: number, unit: string, oneWord: string): string {
  return n === 1 ? oneWord : t('lib.rrule.everyS', { n, unit })
}

/**
 * The rule in words, as the repeat picker and the event detail show it:
 * "Weekly on Tuesday", "Every 2 weeks on Mon and Thu, until 15 Dec", "Monthly on the first Tuesday",
 * "Yearly on 6 October, 5 times". `start` fills in what the rule leaves to DTSTART.
 */
export function summarizeRRule(input: string | RRule | null | undefined, start?: ISODate): string {
  const rule = typeof input === 'string' ? parseRRule(input) : input
  if (!rule) return t('common.doesNotRepeat')
  if (rule.unsupported) return t('lib.rrule.customRuleSetInAnother')
  const n = rule.interval
  let text: string
  switch (rule.freq) {
    case 'DAILY': {
      const days = (rule.byDay ?? []).map((d) => d.day)
      if (days.length && n === 1 && sameDays(days, WEEKDAYS)) text = t('lib.rrule.everyWeekdayMonFri')
      else if (days.length) text = t('lib.rrule.on', { every: every(n, 'day', t('lib.rrule.daily')), dayList: dayList(days) })
      else text = every(n, 'day', t('lib.rrule.daily'))
      break
    }
    case 'WEEKLY': {
      const days = rule.byDay?.length ? rule.byDay.map((d) => d.day) : start ? [dayCodeOf(start)] : []
      if (n === 1 && sameDays(days, WEEKDAYS)) text = t('lib.rrule.everyWeekdayMonFri')
      else if (!days.length) text = every(n, 'week', t('lib.rrule.weekly'))
      else text = t('lib.rrule.on2', { every: every(n, 'week', t('lib.rrule.weekly')), value: days.length === 1 ? WEEKDAY_LONG[dayIndex(days[0])] : dayList(days) })
      break
    }
    case 'MONTHLY': {
      const on = rule.byDay?.[0]
        ? nthDay(rule.byDay[0])
        : rule.byMonthDay === -1
          ? t('lib.rrule.theLastDay')
          : rule.byMonthDay !== undefined
            ? t('lib.rrule.day', { byMonthDay: rule.byMonthDay })
            : start
              ? t('lib.rrule.day2', { day: parseISODate(start).day })
              : ''
      text = on ? t('lib.rrule.on3', { every: every(n, 'month', t('lib.rrule.monthly')), on }) : every(n, 'month', t('lib.rrule.monthly'))
      break
    }
    case 'YEARLY': {
      const month = rule.byMonth ?? (start ? parseISODate(start).month : undefined)
      const day = rule.byMonthDay ?? (start ? parseISODate(start).day : undefined)
      const on = rule.byDay?.[0] && month
        ? t('lib.rrule.of', { nthDay: nthDay(rule.byDay[0]), monthLong: MONTH_LONG[month - 1] })
        : month && day
          ? `${day} ${MONTH_LONG[month - 1]}`
          : ''
      text = on ? t('lib.rrule.on3', { every: every(n, 'year', t('lib.rrule.yearly')), on }) : every(n, 'year', t('lib.rrule.yearly'))
      break
    }
  }
  if (rule.count) text += t('lib.rrule.times', { count: rule.count })
  else if (rule.until) {
    const u = untilDate(rule.until)
    const sameYear = start && parseISODate(start).year === parseISODate(u).year
    text += t('lib.rrule.until', { date: formatDate(u, sameYear ? 'short' : 'shortYear') })
  }
  return text
}

function sameDays(a: DayCode[], b: DayCode[]): boolean {
  return a.length === b.length && b.every((d) => a.includes(d))
}

function dayList(days: DayCode[]): string {
  const sorted = [...days].sort((a, b) => ((dayIndex(a) + 6) % 7) - ((dayIndex(b) + 6) % 7))
  return joinWords(sorted.map((d) => WEEKDAY_SHORT[dayIndex(d)]))
}

// ---------- smart options ----------

export interface RepeatOption {
  /** Stable id for menus and tests: none, daily, weekly, weekdays, monthly-nth, monthly-last, monthly-day, yearly, custom. */
  id: string
  label: string
  /** The RRULE value to store; null for "Does not repeat" and for "Custom…" (opens the editor). */
  rrule: string | null
}

/** Which week of its month a date falls in (1…5), and whether it is that weekday's last. */
export function weekdayPosition(date: ISODate): { nth: number; last: boolean } {
  const { year, month, day } = parseISODate(date)
  return { nth: Math.ceil(day / 7), last: day + 7 > daysInMonth(year, month) }
}

/**
 * The smart options for an event or task on `date` (Tue 6 Oct 2026): Does not repeat, Daily,
 * Weekly on Tuesday, Every weekday (Mon–Fri), Monthly on the first Tuesday (plus "the last
 * Tuesday" in a month's last week), Monthly on day 6, Yearly on 6 October, Custom….
 */
export function smartOptions(date: ISODate): RepeatOption[] {
  const { month, day } = parseISODate(date)
  const code = dayCodeOf(date)
  const name = WEEKDAY_LONG[dayIndex(code)]
  const pos = weekdayPosition(date)
  const out: RepeatOption[] = [
    { id: 'none', label: t('common.doesNotRepeat'), rrule: null },
    { id: 'daily', label: t('lib.rrule.daily'), rrule: 'FREQ=DAILY' },
    { id: 'weekly', label: t('lib.rrule.weeklyOn', { itemName: name }), rrule: `FREQ=WEEKLY;BYDAY=${code}` },
    { id: 'weekdays', label: t('lib.rrule.everyWeekdayMonFri'), rrule: 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR' },
  ]
  if (pos.nth <= 4) out.push({ id: 'monthly-nth', label: t('lib.rrule.monthlyOnThe', { ordinals: ORDINALS[String(pos.nth)], itemName: name }), rrule: `FREQ=MONTHLY;BYDAY=${pos.nth}${code}` })
  if (pos.last) out.push({ id: 'monthly-last', label: t('lib.rrule.monthlyOnTheLast', { itemName: name }), rrule: `FREQ=MONTHLY;BYDAY=-1${code}` })
  out.push(
    { id: 'monthly-day', label: t('lib.rrule.monthlyOnDay', { day }), rrule: `FREQ=MONTHLY;BYMONTHDAY=${day}` },
    { id: 'yearly', label: t('lib.rrule.yearlyOn', { day, monthLong: MONTH_LONG[month - 1] }), rrule: `FREQ=YEARLY;BYMONTH=${month};BYMONTHDAY=${day}` },
    { id: 'custom', label: t('common.custom'), rrule: null },
  )
  return out
}

/** The smart option a stored rule matches (for the picker's checked item), else 'custom'. */
export function matchSmartOption(rrule: string | null | undefined, date: ISODate): string {
  if (!rrule) return 'none'
  const want = parseRRule(rrule)
  if (!want || want.unsupported) return 'custom'
  const norm = formatRRule({ ...want, raw: undefined })
  for (const o of smartOptions(date)) {
    if (!o.rrule) continue
    if (formatRRule({ ...parseRRule(o.rrule)!, raw: undefined }) === norm) return o.id
  }
  // The same rule written without the parts DTSTART implies ("FREQ=WEEKLY" on a Tuesday).
  const implied = summarizeRRule(want, date)
  return smartOptions(date).find((o) => o.rrule && summarizeRRule(o.rrule, date) === implied)?.id ?? 'custom'
}

// ---------- next dates ----------

function nthWeekdayOf(year: number, month: number, day: DayCode, nth: number): ISODate | null {
  const wd = dayIndex(day)
  if (nth > 0) {
    const first = weekdayOf(isoDate(year, month, 1))
    const d = 1 + ((wd - first + 7) % 7) + (nth - 1) * 7
    return d <= daysInMonth(year, month) ? isoDate(year, month, d) : null
  }
  const lastDay = daysInMonth(year, month)
  const lastWd = weekdayOf(isoDate(year, month, lastDay))
  const d = lastDay - ((lastWd - wd + 7) % 7) + (nth + 1) * 7
  return d >= 1 ? isoDate(year, month, d) : null
}

function monthDay(year: number, month: number, md: number): ISODate | null {
  const len = daysInMonth(year, month)
  const d = md < 0 ? len + md + 1 : md
  return d >= 1 && d <= len ? isoDate(year, month, d) : null
}

/** Candidate dates of period `k` (k = 0 is the period holding the start), in order. */
function periodDates(rule: RRule, start: ISODate, k: number): ISODate[] {
  const s = parseISODate(start)
  const n = rule.interval
  switch (rule.freq) {
    case 'DAILY': {
      const d = addDaysISO(start, k * n)
      const days = rule.byDay?.map((b) => b.day)
      return !days?.length || days.includes(dayCodeOf(d)) ? [d] : []
    }
    case 'WEEKLY': {
      // RFC 5545 weeks start on Monday (WKST default).
      const weekStart = addDaysISO(startOfWeekISO(start, 1), k * n * 7)
      const days = rule.byDay?.length ? rule.byDay.map((b) => b.day) : [dayCodeOf(start)]
      return days.map((d) => addDaysISO(weekStart, (dayIndex(d) + 6) % 7)).sort()
    }
    case 'MONTHLY': {
      const m = parseISODate(isoDate(s.year, s.month + k * n, 1))
      const by = rule.byDay?.[0]
      const d = by ? nthWeekdayOf(m.year, m.month, by.day, by.nth ?? 1) : monthDay(m.year, m.month, rule.byMonthDay ?? s.day)
      return d ? [d] : []
    }
    case 'YEARLY': {
      const year = s.year + k * n
      const month = rule.byMonth ?? s.month
      const by = rule.byDay?.[0]
      const d = by ? nthWeekdayOf(year, month, by.day, by.nth ?? 1) : monthDay(year, month, rule.byMonthDay ?? s.day)
      return d ? [d] : []
    }
  }
}

/**
 * The first `n` dates of a simple rule on or after `from` (default: the start). The start
 * itself counts as the first occurrence, as DTSTART does. Read-only rules return [].
 */
export function nextDates(input: string | RRule, start: ISODate, n: number, opts: { from?: ISODate; exdates?: ISODate[] } = {}): ISODate[] {
  const rule = typeof input === 'string' ? parseRRule(input) : input
  if (!rule || rule.unsupported || n <= 0) return []
  const until = rule.until ? untilDate(rule.until) : null
  const skip = new Set(opts.exdates ?? [])
  const out: ISODate[] = []
  let seen = 0
  const take = (d: ISODate) => {
    seen++
    if (!skip.has(d) && (!opts.from || d >= opts.from)) out.push(d)
  }
  take(start)
  for (let k = 0; k < 5000 && out.length < n; k++) {
    for (const d of periodDates(rule, start, k)) {
      if (d <= start) continue
      if (until && d > until) return out.slice(0, n)
      if (rule.count && seen >= rule.count) return out.slice(0, n)
      take(d)
      if (out.length >= n) break
    }
  }
  return out.slice(0, n)
}

/** "Every 2 weeks on Mon and Thu, until 15 Dec · next: Mon 12 Oct, Thu 15 Oct, Mon 26 Oct". */
export function describeRepeat(rrule: string | null | undefined, start: ISODate, opts: { from?: ISODate; count?: number } = {}): string {
  const summary = summarizeRRule(rrule, start)
  const rule = parseRRule(rrule)
  if (!rule || rule.unsupported) return summary
  const next = nextDates(rule, start, opts.count ?? 3, { from: opts.from })
  return next.length ? t('lib.rrule.next', { summary, map: next.map((d) => formatDate(d, 'day')).join(', ') }) : summary
}
