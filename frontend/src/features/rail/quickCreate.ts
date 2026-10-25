// One quick-add line as a record, for the places outside the apps that create
// events and tasks: the palette ("New event: …", "New task: …") and the side panels. The grammar
// is lib/quickadd.ts; the times follow the Calendar app's quick create (no clicked slot here, so
// an untimed event starts at the next half hour) and the task body is the Tasks app's.

import { formatDate, formatRange, formatTime, parseISO, type DatePrefs } from '../../lib/dates'
import { parse, type QuickCollection, type QuickResult } from '../../lib/quickadd'
import { offsetLabel } from '../../lib/quickChoices'
import { summarizeRRule } from '../../lib/rrule'
import { timesFor } from '../calendar/actions'
import { resolveTimes, videoLink } from '../calendar/QuickCreate'
import type { EventWrite } from '../calendar/types'
import { bodyFor } from '../tasks/QuickAdd'
import { join } from '../tasks/when'
import { t as tr } from '../../i18n'

export interface QuickCtx {
  prefs: Pick<DatePrefs, 'zone' | 'hour12'>
  now: Date
  defaultDuration?: number
  morningHour?: number
  calendars?: QuickCollection[]
  lists?: QuickCollection[]
  videoTemplate?: string
}

export function parseLine(text: string, mode: 'event' | 'task', c: QuickCtx): QuickResult {
  return parse(text, {
    mode, now: c.now, tz: c.prefs.zone, calendars: c.calendars, lists: c.lists,
    defaultDuration: c.defaultDuration ?? 60, morningHour: c.morningHour ?? 9,
  })
}

const NO_SLOT = { start: null, end: null, allDay: false, anchor: null }

/** The POST /calendar/events body for a parsed line. Guests are left for the editor. */
export function quickEventBody(r: QuickResult, c: QuickCtx): EventWrite {
  const t = resolveTimes(r, NO_SLOT, c.prefs.zone, c.defaultDuration ?? 60, c.now)
  const body: EventWrite = { summary: r.title.trim() || tr('common.noTitle'), ...timesFor(t.start, t.end, t.allDay) }
  if (!t.allDay) body.tz = c.prefs.zone
  if (r.location) body.location = r.location
  if (r.rrule) body.recurrence = r.rrule
  const mins = r.reminders.map((m) => m.minutes).filter((m): m is number => m != null)
  if (mins.length) body.reminders = mins
  if (r.video) body.url = videoLink(c.videoTemplate || 'https://meet.jit.si/mailr-{id}')
  if (r.list_or_calendar?.id && !r.list_or_calendar.create) body.calendar = r.list_or_calendar.id
  return body
}

/** The POST /tasks body for a parsed line (null without a title). */
export function quickTaskBody(r: QuickResult, c: QuickCtx, defaultList: string | null): Record<string, unknown> | null {
  return bodyFor(r, {}, defaultList, c.prefs.zone)
}

/** The parse as chip labels, as the line shows them before Enter. */
export function quickChips(r: QuickResult, mode: 'event' | 'task', c: QuickCtx): string[] {
  const out: string[] = []
  const prefs = c.prefs
  if (mode === 'event') {
    const t = resolveTimes(r, NO_SLOT, prefs.zone, c.defaultDuration ?? 60, c.now)
    const typed = r.tokens.some((x) => x.kind === 'date' || x.kind === 'time' || x.kind === 'span')
    out.push(`${typed ? '' : 'Next: '}${formatRange(t.start, t.allDay ? (t.end as string) : t.end, t.allDay, prefs)}`)
  } else if (r.due) {
    if (r.due_all_day) out.push(tr('rail.quickCreate.due', { date: formatDate(r.due, 'day') }))
    else {
      const at = parseISO(join(r.due.slice(0, 10), r.due.slice(11, 16), prefs.zone), prefs.zone)
      out.push(tr('rail.quickCreate.due2', { date: formatDate(r.due.slice(0, 10), 'day'), value: at ? formatTime(at, prefs, { compact: true }) : r.due.slice(11, 16) }))
    }
  }
  if (mode === 'task' && r.start_date) out.push(tr('rail.quickCreate.starts', { date: formatDate(r.start_date, 'day') }))
  if (r.rrule) out.push(summarizeRRule(r.rrule, (r.start ?? r.due ?? '').slice(0, 10) || undefined))
  if (r.location) out.push(tr('rail.quickCreate.at', { location: r.location }))
  if (r.video) out.push(tr('rail.quickCreate.videoCall'))
  if (r.list_or_calendar) out.push(tr('rail.quickCreate.text', { value: mode === 'task' ? tr('common.list') : tr('common.calendar'), listOrCalendarName: r.list_or_calendar.name, value2: r.list_or_calendar.create ? ' (new)' : '' }))
  for (const t of r.tags) out.push(`@${t}`)
  if (r.priority) out.push(r.priority <= 4 ? tr('common.highPriority') : r.priority === 5 ? tr('rail.quickCreate.mediumPriority') : tr('rail.quickCreate.lowPriority'))
  for (const m of r.reminders) if (m.minutes != null) out.push(m.minutes === 0 ? tr('rail.quickCreate.remindAtTheTime') : tr('rail.quickCreate.remind', { offsetLabel: offsetLabel(m.minutes).toLowerCase() }))
  for (const g of r.guests) out.push(tr('rail.quickCreate.withAddInTheEditor', { email: g.email ?? g.text }))
  return out
}

/** "New event: lunch fri 1pm" → the mode and the rest; null when the text is not a create command. */
export function createCommand(q: string): { mode: 'event' | 'task'; text: string } | null {
  const m = /^\s*(?:new\s+|add\s+)?(event|task|todo)\s*:?\s+(.*\S)\s*$/i.exec(q) ?? /^\s*(?:new\s+|add\s+)?(event|task|todo)\s*:\s*(.*)$/i.exec(q)
  if (!m) return null
  const word = m[1].toLowerCase()
  return { mode: word === 'event' ? 'event' : 'task', text: m[2] ?? '' }
}

/** "Go to 12 Dec", "go to next friday": a date for the calendar, or null. */
export function goToDate(q: string, c: QuickCtx): string | null {
  const m = /^\s*(?:go\s*to|goto|jump\s+to)\s+(.+)$/i.exec(q)
  if (!m) return null
  const r = parse(m[1], { mode: 'event', now: c.now, tz: c.prefs.zone, defaultDuration: 60 })
  if (!r.tokens.some((t) => t.kind === 'date') || !r.start) return null
  if (r.title.trim()) return null
  return r.start.slice(0, 10)
}
