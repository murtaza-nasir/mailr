// Repeating lists (the server is notes/repeat.py). A list repeats
// weekly on a day or every N days; on each repeat its ticked items are unticked at 4 in the morning,
// history kept. The rule is RRULE text, the same as tasks and events, so lib/rrule.ts describes it and
// the task grammar (lib/quickadd.ts) reads a typed "every sunday" or "every 3 days".

import { parse as quickParse } from '../../lib/quickadd'
import { DAY_CODES, summarizeRRule, type DayCode } from '../../lib/rrule'
import { t as tr } from '../../i18n'
import { formatInstant } from '../../lib/dates'

export type ListRepeat = { freq: 'weekly'; day: DayCode; interval: number } | { freq: 'daily'; interval: number }

/** A list's rule as its two shapes, or null (none, or a shape a list cannot take). */
export function listRepeat(rule: string | null | undefined): ListRepeat | null {
  const raw = (rule ?? '').replace(/^RRULE:/i, '').trim()
  if (!raw) return null
  const parts = new Map<string, string>()
  for (const p of raw.split(';')) {
    const [k, v] = p.split('=')
    if (!k || v === undefined) return null
    parts.set(k.trim().toUpperCase(), v.trim().toUpperCase())
  }
  const freq = parts.get('FREQ')
  const interval = parts.has('INTERVAL') ? Number(parts.get('INTERVAL')) : 1
  if (!Number.isInteger(interval) || interval < 1) return null
  const known = new Set(['FREQ', 'INTERVAL', 'BYDAY'])
  if ([...parts.keys()].some((k) => !known.has(k))) return null
  if (freq === 'WEEKLY') {
    const day = parts.get('BYDAY') as DayCode | undefined
    if (!day || !DAY_CODES.includes(day) || interval > 52) return null
    return { freq: 'weekly', day, interval }
  }
  if (freq === 'DAILY' && !parts.has('BYDAY') && interval <= 365) return { freq: 'daily', interval }
  return null
}

/** The rule text of a list repeat (the server's one spelling). */
export function repeatRule(r: ListRepeat): string {
  if (r.freq === 'weekly') return `FREQ=WEEKLY;BYDAY=${r.day}${r.interval > 1 ? `;INTERVAL=${r.interval}` : ''}`
  return `FREQ=DAILY;INTERVAL=${r.interval}`
}

/** "Weekly on Sunday", "Every 3 days", "Daily" (lib/rrule.ts words). */
export function repeatWords(rule: string | null | undefined): string {
  return rule ? summarizeRRule(rule) : tr('common.doesNotRepeat')
}

/** "weekly on Sunday": the words inside a sentence (day names keep their capital). */
export function repeatPhrase(rule: string | null | undefined): string {
  const w = repeatWords(rule)
  return w.charAt(0).toLowerCase() + w.slice(1)
}

/** A typed repeat read with the task grammar: "every sunday", "weekly", "every 3 days",
 *  "daily". Null when the words name no repeat a list can take (several days, monthly). */
export function repeatFromWords(text: string, tz: string, now = new Date()): ListRepeat | null {
  const t = text.trim()
  if (!t) return null
  const words = /^(every|each|weekly|daily)\b/i.test(t) ? t : tr('notes.repeatModel.every', { t })
  const r = quickParse(`x ${words}`, { mode: 'task', now, tz })
  // "every week", "weekly": on today's weekday.
  const rule = r.rrule && /FREQ=WEEKLY/i.test(r.rrule) && !/BYDAY=/i.test(r.rrule) ? `${r.rrule};BYDAY=${todayCode(now, tz)}` : r.rrule
  return listRepeat(rule)
}

function todayCode(now: Date, tz: string): DayCode {
  const wd = formatInstant(now, 'en-US', { weekday: 'short' }, tz).slice(0, 2).toUpperCase()
  return (DAY_CODES as string[]).includes(wd) ? (wd as DayCode) : 'MO'
}
