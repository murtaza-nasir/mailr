// The reminder presets for the web (the Android twin is
// data/reminders/QuickChoices.kt; both follow the spec, and both have tests for it).

import { t, appLocale } from '../i18n'
import { clockOpts } from './dates'

export interface Choice {
  id: string
  label: string
  at: Date
}

const MIN = 60_000

function atHour(d: Date, h: number, m = 0): Date {
  const x = new Date(d)
  x.setHours(h, m, 0, 0)
  return x
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}

/** The next given weekday (0 = Sunday), strictly after today. */
function nextDow(d: Date, dow: number): Date {
  const diff = ((dow - d.getDay() + 7) % 7) || 7
  return addDays(d, diff)
}

/** Round up to the next 5 minutes (seconds dropped). */
export function roundUp5(d: Date): Date {
  const x = new Date(d)
  x.setSeconds(0, 0)
  const extra = (5 - (x.getMinutes() % 5)) % 5
  return new Date(x.getTime() + extra * MIN)
}

/** ACT task.reschedule @web.sheet presets: keep the task's time of day, or the morning for a date-only task. */
export function taskReschedule(now: Date, due: Date | null, dateOnly: boolean, morningHour = 9): Choice[] {
  const h = due && !dateOnly ? due.getHours() : morningHour
  const m = due && !dateOnly ? due.getMinutes() : 0
  const on = (day: Date) => atHour(day, h, m)
  const out: Choice[] = []
  if (now.getHours() < 18) out.push({ id: 'later', label: t('common.laterToday'), at: roundUp5(new Date(now.getTime() + 3 * 60 * MIN)) })
  if (now.getHours() < 20) out.push({ id: 'tonight', label: t('common.tonight'), at: atHour(now, 20) })
  out.push({ id: 'tomorrow', label: t('common.tomorrow'), at: on(addDays(now, 1)) })
  if (now.getDay() >= 1 && now.getDay() <= 4) out.push({ id: 'weekend', label: t('common.thisWeekend'), at: on(nextDow(now, 6)) })
  out.push({ id: 'monday', label: t('lib.quickChoices.nextMonday'), at: on(nextDow(now, 1)) })
  out.push({ id: 'week', label: t('lib.quickChoices.inAWeek'), at: on(addDays(now, 7)) })
  return out.filter((c, i) => out.findIndex((o) => o.at.getTime() === c.at.getTime()) === i)
}

/** ACT event.reschedule @web.sheet presets: +15 min, +1 hour, tomorrow and next week at the same time. */
export function eventReschedule(start: Date): Choice[] {
  return [
    { id: '15m', label: t('lib.quickChoices.n15Min'), at: new Date(start.getTime() + 15 * MIN) },
    { id: '1h', label: t('lib.quickChoices.n1Hour'), at: new Date(start.getTime() + 60 * MIN) },
    { id: 'tomorrow', label: t('lib.quickChoices.tomorrowSameTime'), at: addDays(start, 1) },
    { id: 'week', label: t('lib.quickChoices.nextWeekSameTime'), at: addDays(start, 7) },
  ]
}

export const OFFSETS = [0, 5, 10, 15, 30, 60, 120, 1440, 2880]

export function offsetLabel(m: number): string {
  if (m === 0) return t('lib.quickChoices.atStart')
  if (m < 60) return t('lib.quickChoices.minBefore', { m })
  if (m < 1440) return t('lib.quickChoices.hourBefore', { count: m / 60 })
  return t('lib.quickChoices.dayBefore', { count: m / 1440 })
}

/** ACT event.remind_before @web.sheet: the offsets still ahead (at least 30 s away). */
export function offsets(now: Date, start: Date): number[] {
  return OFFSETS.filter((m) => start.getTime() - m * MIN > now.getTime() + 30_000)
}

/** "Thu 2 Oct, 9:05 AM", or "Today, 4:40 PM" / "Tomorrow, …". */
export function describeMoment(at: Date, now: Date, dateOnly = false): string {
  const day0 = atHour(now, 0).getTime()
  const d = atHour(at, 0).getTime()
  const days = Math.round((d - day0) / 86_400_000)
  const dayText = days === 0 ? t('common.today') : days === 1 ? t('common.tomorrow') : at.toLocaleDateString(appLocale(), { weekday: 'short', day: 'numeric', month: 'short' })
  if (dateOnly) return dayText
  return `${dayText}, ${at.toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })}`
}
