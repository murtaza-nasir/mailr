// Find a time on the client: the ranking the server's POST /calendar/find-time uses (slots fully
// free in my busy calendars, inside working hours, nearest first, spread over days, avoiding the
// first and last hour), for servers that do not have the route yet. Pure, so vitest covers it.

import { addDaysISO, weekdayOf, type ISODate } from '../../lib/dates'
import { gridInstant } from './geometry'

export interface FindInput {
  busy: { start: Date; end: Date }[]
  /** The days to search, in order. */
  days: ISODate[]
  zone: string
  /** Working hours in minutes since midnight. */
  workStart: number
  workEnd: number
  workDays: number[]
  duration: number
  buffer: number
  granularity?: number
  now: Date
  limit?: number
  /** "Avoid before 10:00 / after 16:00" (minutes), when set. */
  avoidBefore?: number | null
  avoidAfter?: number | null
}

export interface Slot {
  start: Date
  end: Date
  score: number
}

/** The candidate slots, ranked; at most `limit` (8), at most two per day until every day had a turn. */
export function findSlots(o: FindInput): Slot[] {
  const step = o.granularity ?? 15
  const from = Math.max(o.workStart, o.avoidBefore ?? 0)
  const to = Math.min(o.workEnd, o.avoidAfter ?? 1440)
  const busy = o.busy.map((b) => ({ s: b.start.getTime() - o.buffer * 60_000, e: b.end.getTime() + o.buffer * 60_000 }))
  const perDay: Slot[][] = []
  o.days.forEach((day, di) => {
    if (!o.workDays.includes(weekdayOf(day))) return
    const list: Slot[] = []
    for (let m = from; m + o.duration <= to; m += step) {
      const start = gridInstant(day, m, o.zone)
      const end = new Date(start.getTime() + o.duration * 60_000)
      if (start.getTime() < o.now.getTime()) continue
      if (busy.some((b) => b.s < end.getTime() && b.e > start.getTime())) continue
      // Nearest first; the first and the last working hour cost a little.
      const edge = m < o.workStart + 60 || m + o.duration > o.workEnd - 60 ? 30 : 0
      list.push({ start, end, score: di * 100 + (m - from) / step + edge })
    }
    list.sort((a, b) => a.score - b.score)
    // Slots that touch the same free stretch are near-duplicates: keep the ones an hour apart.
    const kept: Slot[] = []
    for (const s of list) if (kept.every((k) => Math.abs(k.start.getTime() - s.start.getTime()) >= 60 * 60_000)) kept.push(s)
    perDay.push(kept)
  })
  const out: Slot[] = []
  const limit = o.limit ?? 8
  // Spread over days: two per day in day order, then the rest.
  for (let round = 0; out.length < limit && perDay.some((d) => d.length > round * 2); round++) {
    for (const d of perDay) {
      for (const s of d.slice(round * 2, round * 2 + 2)) if (out.length < limit) out.push(s)
    }
  }
  return out
}

/** The ranges the find-a-time tab offers. */
export function rangeDays(kind: 'this' | 'next' | 'next5', today: ISODate, weekStart: number, workDays: number[]): ISODate[] {
  const wd = weekdayOf(today)
  const startOfWeek = addDaysISO(today, -((wd - weekStart + 7) % 7))
  if (kind === 'this') return Array.from({ length: 7 }, (_, i) => addDaysISO(startOfWeek, i)).filter((d) => d >= today)
  if (kind === 'next') return Array.from({ length: 7 }, (_, i) => addDaysISO(startOfWeek, 7 + i))
  const out: ISODate[] = []
  for (let i = 0, d = today; out.length < 5 && i < 60; i++, d = addDaysISO(d, 1)) if (workDays.includes(weekdayOf(d))) out.push(d)
  return out
}
