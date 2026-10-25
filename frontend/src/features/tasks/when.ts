// The when picker for tasks: the presets with their
// accelerators, typed input through the shared grammar ("fri 3p", "tom", "next week", "no date"),
// and the live result line. A value is what the API takes: a date "2026-10-02" (no time) or a
// UTC instant "2026-10-02T20:00:00.000Z".

import {
  addDaysISO, diffDaysISO, formatDate, formatTime, fromZoned, parseISO, parseISODate, toISODate, todayISO, weekdayOf,
  type DatePrefs, type ISODate,
} from '../../lib/dates'
import { parse } from '../../lib/quickadd'
import { t as tr } from '../../i18n'

export interface When {
  /** A date, or a UTC instant; null = no date. */
  value: string | null
}

export const isDateOnly = (v: string | null | undefined) => !!v && /^\d{4}-\d{2}-\d{2}$/.test(v)

/** The day and the wall time ("15:30", or null for a date-only value) of a value in the zone. */
export function split(value: string | null | undefined, zone: string): { day: ISODate | null; hm: string | null } {
  if (!value) return { day: null, hm: null }
  if (isDateOnly(value)) return { day: value, hm: null }
  const d = parseISO(value, zone)
  if (!d) return { day: null, hm: null }
  return { day: toISODate(d, zone), hm: formatTime(d, { zone, hour12: false }) }
}

/** A day plus an optional wall time → a value. */
export function join(day: ISODate, hm: string | null, zone: string): string {
  if (!hm) return day
  const [h, m] = hm.split(':').map(Number)
  const { year, month, day: dd } = parseISODate(day)
  return fromZoned({ year, month, day: dd, hour: h, minute: m }, zone).toISOString()
}

export interface Preset {
  id: string
  label: string
  /** The accelerator key in the popover (t, m, w, n, 0). */
  key?: string
  value: string | null
}

/**
 * The reschedule presets (keys: t Today, m Tomorrow, w This weekend, n Next week, 0 No
 * date), keeping the task's time ("Keep time" is on by default), plus Later today and Tonight
 * while they are still ahead.
 */
export function presets(now: Date, current: string | null, zone: string, opts: { noDate?: boolean; laterToday?: boolean } = {}): Preset[] {
  const today = todayISO(zone, now)
  const { hm } = split(current, zone)
  const wd = weekdayOf(today)
  const sat = wd === 6 ? today : addDaysISO(today, (6 - wd + 7) % 7 || 7)
  const mon = addDaysISO(today, ((1 - wd + 7) % 7) || 7)
  const at = (day: ISODate) => join(day, hm, zone)
  const out: Preset[] = []
  const hour = Number(formatTime(now, { zone, hour12: false }).slice(0, 2))
  if (opts.laterToday !== false) {
    if (hour < 18) {
      const later = new Date(Math.ceil((now.getTime() + 3 * 3_600_000) / 300_000) * 300_000)
      out.push({ id: 'later', label: tr('common.laterToday'), value: later.toISOString() })
    }
    if (hour < 20) out.push({ id: 'tonight', label: tr('common.tonight'), value: join(today, '20:00', zone) })
  }
  out.push(
    { id: 'today', label: tr('common.today'), key: 't', value: at(today) },
    { id: 'tomorrow', label: tr('common.tomorrow'), key: 'm', value: at(addDaysISO(today, 1)) },
    { id: 'weekend', label: tr('common.thisWeekend'), key: 'w', value: at(sat) },
    { id: 'nextweek', label: tr('common.nextWeek'), key: 'n', value: at(mon) },
  )
  if (opts.noDate !== false) out.push({ id: 'none', label: tr('common.noDate'), key: '0', value: null })
  return out
}

const NONE = /^(no ?date|none|clear|remove|-|0)$/i

/**
 * Typed input: the date and time parts of the quick-add grammar. Returns the value, null for
 * "no date", or undefined when the text is not a date (the field turns red and keeps it).
 */
export function parseWhen(text: string, now: Date, zone: string, keepHm: string | null = null): string | null | undefined {
  const t = text.trim()
  if (!t) return undefined
  if (NONE.test(t)) return null
  const r = parse(t, { mode: 'task', now, tz: zone })
  // Every word must be a date or time word: "fri 3p" yes, "call fri" no.
  if (r.title) return undefined
  if (r.due) {
    if (r.due_all_day && keepHm && !r.tokens.some((k) => k.kind === 'time')) return join(r.due, keepHm, zone)
    return r.due_all_day ? r.due : join(r.due.slice(0, 10), r.due.slice(11, 16), zone)
  }
  if (r.start_date) return r.start_date
  return undefined
}

/** A wall time typed alone: "3p", "15", "1530", "3:30 pm", "noon" → "15:30". */
export function parseTime(text: string): string | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, '')
  if (!t) return null
  if (t === 'noon') return '12:00'
  if (t === 'midnight') return '00:00'
  const m = /^(\d{1,2})(?::?(\d{2}))?(a|am|p|pm)?$/.exec(t)
  if (!m) return null
  let h = Number(m[1])
  const min = Number(m[2] ?? 0)
  if (m[1].length > 2 || min > 59) return null
  if (m[3]) {
    if (h < 1 || h > 12) return null
    h = (h % 12) + (m[3].startsWith('p') ? 12 : 0)
  }
  if (h > 23) return null
  return `${String(h).padStart(2, '0')}:${String(min).padStart(2, '0')}`
}

/** "Fri 2 Oct, 3:00 PM · in 1 day", "Today · today", "No date". */
export function describe(value: string | null, now: Date, prefs: Pick<DatePrefs, 'zone' | 'hour12'>): string {
  if (!value) return tr('common.noDate')
  const { day } = split(value, prefs.zone)
  if (!day) return value
  const today = todayISO(prefs.zone, now)
  const n = diffDaysISO(today, day)
  const rel = n === 0 ? tr('tasks.when.today') : n === 1 ? tr('tasks.when.tomorrow') : n === -1 ? tr('tasks.when.yesterday') : n > 0 ? tr('tasks.when.inDays', { count: n }) : tr('tasks.when.daysAgo', { count: -n })
  const dayText = formatDate(day, day.slice(0, 4) === today.slice(0, 4) ? 'day' : 'dayYear')
  if (isDateOnly(value)) return `${dayText} · ${rel}`
  const at = parseISO(value, prefs.zone)!
  return `${dayText}, ${formatTime(at, prefs)} · ${rel}`
}

/** Short label for a preset row: "Fri 2 Oct" or "Fri 3 PM". */
export function presetHint(value: string | null, prefs: Pick<DatePrefs, 'zone' | 'hour12'>): string {
  if (!value) return ''
  const { day } = split(value, prefs.zone)
  if (isDateOnly(value)) return formatDate(day!, 'day')
  return `${formatDate(day!, 'weekday')} ${formatTime(parseISO(value, prefs.zone)!, prefs, { compact: true })}`
}

const WEEKDAY = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA']

/**
 * The repeat rule after the due of a repeating task moved from `oldDue` to `newDue` (Android
 * TaskQueries.ruleAfterMove). "this" moves only this occurrence: a weekly or
 * monthly rule that took its day from the due is pinned to the old day, so the next ones stay
 * where they were. "all" moves the series: a single BYDAY or BYMONTHDAY that matched the old day
 * follows the new one. Returns null when the rule needs no change.
 */
export function ruleAfterMove(rrule: string | null | undefined, oldDue: string | null | undefined, newDue: string | null | undefined,
  scope: 'this' | 'all', zone: string): string | null {
  if (!rrule || !oldDue || !newDue) return null
  const from = split(oldDue, zone).day
  const to = split(newDue, zone).day
  if (!from || !to || from === to) return null
  const parts = rrule.split(';').filter(Boolean).map((p) => {
    const i = p.indexOf('=')
    return [(i < 0 ? p : p.slice(0, i)).toUpperCase(), i < 0 ? '' : p.slice(i + 1)] as [string, string]
  })
  const get = (k: string) => parts.find((p) => p[0] === k)?.[1]
  const set = (k: string, v: string) => {
    const i = parts.findIndex((p) => p[0] === k)
    if (i >= 0) parts[i] = [k, v]
    else parts.push([k, v])
  }
  const wd = (d: ISODate) => WEEKDAY[weekdayOf(d)]
  const dom = (d: ISODate) => String(Number(d.slice(8, 10)))
  const freq = get('FREQ')?.toUpperCase()
  if (freq === 'WEEKLY') {
    const by = get('BYDAY')
    if (scope === 'this') {
      if (by != null) return null
      set('BYDAY', wd(from))
    } else if (by != null && by.toUpperCase() === wd(from)) set('BYDAY', wd(to))
    else return null
  } else if (freq === 'MONTHLY') {
    if (get('BYDAY') != null || get('BYSETPOS') != null) return null
    const by = get('BYMONTHDAY')
    if (scope === 'this') {
      if (by != null) return null
      set('BYMONTHDAY', dom(from))
    } else if (by != null && by === dom(from)) set('BYMONTHDAY', dom(to))
    else return null
  } else return null
  return parts.map(([k, v]) => `${k}=${v}`).join(';')
}

/** The series question asks only when the series has more than one occurrence and the due really moves. */
export function needsDueScope(t: { rrule?: string | null; recurring?: boolean; due?: string | null }, due: string | null): boolean {
  if (!t.rrule || !due || !t.due || due === t.due) return false
  return !/(^|;)COUNT=1(;|$)/i.test(t.rrule)
}
